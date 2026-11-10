"""Sale-process check for recent bankruptcy families (filed within 36 months), newest first.

    python -m src.discover.courts.sale_check [--channel claims_agent|courtlistener|news] [--max-families N] [--dry-run]

Channels (one core.sale_check row per family per channel; a later check never downgrades a found stage):
  claims_agent  Public case pages only. ElevenFlo /cases/<slug> pages carry a case narrative and the recent docket
                entry titles; Stretto case pages carry a case narrative. Stretto's docket list is reCAPTCHA-gated and
                Epiq's case pages are JavaScript-only (their docket search is an internal API), so both are treated as
                manual and never worked around. Docket-entry links on ElevenFlo point at /api/search/open/, which
                robots.txt disallows, so the evidence URL is the case page itself (with the Dkt. number in the text).
  courtlistener Only families with no readable claims-agent page. One docket-entries request per family (a second
                page only if nothing was found and the budget allows), at most --max-families (default and hard cap
                15) per run AND at most DAILY_BUDGET requests per rolling 24 h counted from core.sale_check, so the
                account's 125/day limit keeps >= 110 requests for the case-list loader. Families checked in the last
                7 days are skipped. Any 429 stops the run at once (no retry). 401/403 stops it too.
  news          Our own news (raw.document, sources with category 'news'). A match needs the family's distinctive
                name phrase AND one of the family's places (city or state of linked facilities, or the court's
                state) in the same article, and a sentence naming the family that mentions a sale step.
Stages (latest wins): sale motion < bidding procedures < stalking horse < auction < sale order < sale closed.
Buyer names are recorded only when they appear literally next to a buyer phrase in the evidence text.
"""
from __future__ import annotations

import argparse
import html
import re
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from src.common.config import MissingSetting
from src.common.http import PoliteClient, SourceBlocked

from .courtlistener import RequestCapReached
from .families import STATE_NAMES, court_state, norm_docket, source_kind

CHANNELS = ("claims_agent", "courtlistener", "news")
MAX_FAMILIES_CAP = 15
DAILY_BUDGET = 15             # CourtListener requests per rolling 24 h for this job (account limit: 125/day)
RECHECK_DAYS = 7
MAX_PAGES_PER_FAMILY = 4      # claims-agent pages fetched per family (lead docket first)
EVIDENCE_MAX = 480

# ── stage + buyer detection (pure functions, tested) ────────────────────────────────────────────────────────────
STAGES = ["sale motion", "bidding procedures", "stalking horse", "auction", "sale order", "sale closed"]
STAGE_RANK = {s: i + 1 for i, s in enumerate(STAGES)}
_SALE_WORD = r"\b(?:sale|sell|selling)\b"
STAGE_PATTERNS = {
    "sale closed": re.compile(
        r"notice of (?:the )?(?:sale )?closing|closing of (?:the )?sale|sale (?:has |was )?closed|"
        r"consummat\w* (?:of )?(?:the )?sale|completion of (?:the )?sale|completed (?:the|its) sale", re.I),
    "sale order": re.compile(
        r"order[^.;]{0,40}\b(?:approving|authorizing)\b[^.;]{0,160}" + _SALE_WORD + r"|\bsale order\b|"
        r"approved the sale|court approv\w*[^.;]{0,60}" + _SALE_WORD + r"|"
        r"order[^.;]{0,40}\bapproving\b[^.;]{0,80}purchase agreement", re.I),
    "auction": re.compile(r"\bauction\b|successful bidder|winning bid|back-?up bidder", re.I),
    "stalking horse": re.compile(r"stalking[\s-]*horse", re.I),
    "bidding procedures": re.compile(r"\bbid(?:ding)? procedures\b", re.I),
    "sale motion": re.compile(
        r"motion[^.;]{0,160}" + _SALE_WORD + r"|\bsale motion\b|\bsection 363\b|§\s*363|"
        r"asset purchase agreement|\bpurchase agreement\b|\bsale process\b|"
        r"\bsale of (?:substantially )?(?:all|the|certain|its|their)\b", re.I),
}
NEWS_CLOSED = re.compile(r"\b(?:sold|acquired|acquires|purchased|bought|took over|has taken over|new operator)\b|"
                         r"\b(?:arrange[sd]?|complete[sd]?|close[sd]?|brokered)\b[^;]{0,40}?\bsale\b", re.I)
NOT_A_SALE = re.compile(r"de minimis|transfer of claim|claims? transfer|wholesale|sale of (?:a |the )?claims?\b|"
                        r"garage sale|point[- ]of[- ]sale", re.I)


# A filing that IS a motion/application only asks for an order: "Motion ... for Entry of an Order Authorizing the Sale"
# is a sale motion, not a sale order. ("Order Granting Motion to Sell" starts with Order and stays a sale order.)
_MOTION_FIRST = re.compile(r"^\W*(?:[\w'’()-]+\W+){0,8}?(?:motion|application|request)\b", re.I)


def classify_stage(text: str | None, *, news: bool = False) -> str | None:
    """Latest sale stage literally indicated by the text, or None."""
    if not text or NOT_A_SALE.search(text):
        return None
    found = [s for s, rx in STAGE_PATTERNS.items() if rx.search(text)]
    m = _MOTION_FIRST.match(text)
    if m and not re.search(r"\border\b", text[:m.start(0) + len(m.group(0)) - 6], re.I):
        found = ["sale motion" if s in ("sale order", "sale closed") else s for s in found]
    if news and NEWS_CLOSED.search(text):
        found.append("sale closed")
    return max(found, key=STAGE_RANK.get) if found else None


_SUFFIX = r"(?:,?\s+(?:LLC|L\.L\.C\.|Inc\.?|LP|L\.P\.|LLP|Ltd\.?|Corp\.?|Corporation|Company|Co\.))"
_WORD = r"[A-Z0-9][\w.'’&-]*"
ENTITY = rf"(?P<e>{_WORD}(?:\s+(?:(?:of|and|&|the|de|at)\s+)?{_WORD}){{0,7}}{_SUFFIX}?)"
BUYER_PATTERNS = [   # entity names stay case-sensitive (capitalized words); the buyer phrases are case-insensitive
    re.compile(ENTITY + r",?\s+(?:\([^)]{0,40}\)\s+)?(?i:as\s+(?:the\s+)?(?:proposed\s+)?(?:stalking[\s-]*horse"
               r"(?:\s+(?:bidder|purchaser|buyer))?|successful\s+bidder|winning\s+bidder|buyer|purchaser|back-?up\s+bidder))"),
    re.compile(r"(?i:stalking[\s-]*horse\s+(?:bidder|purchaser|buyer)|successful\s+bidder|winning\s+bidder)"
               r"[,:]?\s+(?:is\s+|was\s+|,\s*)?" + ENTITY),
    re.compile(r"(?i:\b(?:sale|sell|sold)\s+(?:of\s+[^.;]{0,80}?\s+)?to)\s+" + ENTITY),
    re.compile(r"(?i:\bacquired\s+by)\s+" + ENTITY),
]
NEWS_BUYER = re.compile(ENTITY + r"\s+(?i:(?:has\s+|have\s+)?(?:acquired|bought|purchased|took over|has taken over))\b")
GENERIC_START = {"the", "debtor", "debtors", "court", "purchaser", "buyer", "sale", "order", "motion", "asset",
                 "assets", "bidding", "stalking", "notice", "successful", "back", "backup", "it", "they", "this",
                 "that", "such", "its", "their", "plan", "chapter", "united", "bankruptcy", "trustee", "dkt",
                 "a", "an", "any", "all", "each", "certain", "official", "committee", "agreement", "in", "on",
                 "of", "and", "&", "at", "de", "designation", "selection", "approval", "approving", "authorizing",
                 "filed", "entry", "revised", "amended", "supplemental", "proposed"}


def _valid_entity(e: str) -> str | None:
    e = e.strip(" ,.;:")
    words = e.split()
    while words and words[0].lower().strip(".,") in GENERIC_START:   # 'Notice of Designation of X LLC' -> 'X LLC'
        words = words[1:]
    e = " ".join(words)
    if not words:
        return None
    has_suffix = re.search(_SUFFIX + r"$", e) is not None
    if len(words) < 2 and not has_suffix:
        return None
    return e if len(e) >= 4 else None


def extract_buyer(text: str | None, *, news: bool = False) -> str | None:
    """A buyer / stalking-horse name only when it literally appears next to a buyer phrase. Never inferred."""
    if not text:
        return None
    for rx in BUYER_PATTERNS + ([NEWS_BUYER] if news else []):
        for m in rx.finditer(text):
            e = _valid_entity(m.group("e"))
            if e:
                return e
    return None


@dataclass
class Evidence:
    stage: str
    text: str
    url: str
    buyer: str | None = None
    when: date | None = None


def best_evidence(items: list[Evidence]) -> Evidence | None:
    """Latest stage wins; ties go to an item naming a buyer, then the most recent."""
    if not items:
        return None
    return max(items, key=lambda e: (STAGE_RANK[e.stage], e.buyer is not None, e.when or date.min))


def snippet(text: str, stage: str, width: int = EVIDENCE_MAX) -> str:
    t = re.sub(r"\s+", " ", text).strip()
    if len(t) <= width:
        return t
    m = STAGE_PATTERNS[stage].search(t) or (NEWS_CLOSED.search(t) if stage == "sale closed" else None)
    start = max(0, (m.start() if m else 0) - width // 3)
    return ("…" if start else "") + t[start:start + width - 2] + "…"


SENT_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"“(])")
# A piece ending in one of these is not a sentence end ('Dkt. 143', '101 W. State Street', 'Holdings, Inc. as').
_ABBREV = re.compile(r"(?:\b[A-Z]|\b(?:Inc|Co|Corp|Ltd|No|Nos|Dkt|St|Mr|Ms|Mrs|Dr|Jr|Sr|U\.S|L\.P|L\.L\.C|v|vs|"
                     r"Jan|Feb|Mar|Apr|Aug|Sept?|Oct|Nov|Dec|Bankr|Mt|Ft|approx|e\.g|i\.e))\.$")


def sentences(text: str) -> list[str]:
    out: list[str] = []
    for piece in SENT_SPLIT.split(re.sub(r"\s+", " ", text or "")):
        if out and _ABBREV.search(out[-1]):
            out[-1] += " " + piece
        else:
            out.append(piece)
    return [s.strip() for s in out if s.strip()]


# ── claims-agent pages ──────────────────────────────────────────────────────────────────────────────────────────
_LI = re.compile(r'<li class="group/filing[^"]*"[^>]*>(.*?)</li>', re.S)
_TIME = re.compile(r'<time dateTime="(\d{4}-\d{2}-\d{2})')
_TITLE = re.compile(r'<span[^>]*\btitle="([^"]*)"[^>]*>([^<]*)</span>')
_DKT = re.compile(r'aria-label="Open Dkt\.\s*(\d+)')


def _visible(page: str) -> str:
    t = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", page, flags=re.S)
    t = html.unescape(re.sub(r"<[^>]+>", " ", t))
    return re.sub(r"\s+", " ", t).strip()


def parse_elevenflo_entries(page: str) -> list[dict]:
    out = []
    for li in _LI.findall(page):
        t, ti, d = _TIME.search(li), _TITLE.search(li), _DKT.search(li)
        if not ti:
            continue
        out.append({"date": t.group(1) if t else None, "full": html.unescape(ti.group(1)),
                    "short": html.unescape(ti.group(2)).strip(), "dkt": d.group(1) if d else None})
    return out


def elevenflo_narrative(page: str) -> str:
    """The case overview text (between the case header and the weekly-filings chart)."""
    text = _visible(page)
    end = min([i for i in (text.find("Docket filings per week"), text.find("Developments"), text.find("View more"))
               if i > 0] or [len(text)])
    head = text[:end]
    for marker in ("Overview", "Toggle sidebar ElevenFlo"):   # summary starts after the tabs / site navigation
        start = head.find(marker)
        if start >= 0:
            return head[start + len(marker):]
    return head


def scan_elevenflo(url: str, page: str) -> tuple[list[Evidence], int]:
    items: list[Evidence] = []
    entries = parse_elevenflo_entries(page)
    for e in entries:
        text = f"{e['short']}: {e['full']}"
        stage = classify_stage(text)
        if stage:
            label = f"Dkt. {e['dkt']} ({e['date']}) " if e["dkt"] else ""
            items.append(Evidence(stage, label + snippet(text, stage, EVIDENCE_MAX - len(label)), url,
                                  extract_buyer(e["full"]) or extract_buyer(e["short"]),
                                  datetime.strptime(e["date"], "%Y-%m-%d").date() if e["date"] else None))
    sents = sentences(elevenflo_narrative(page))
    for s in sents:
        stage = classify_stage(s)
        if stage:
            items.append(Evidence(stage, "Case summary: " + snippet(s, stage, EVIDENCE_MAX - 14), url, extract_buyer(s)))
    return items, len(entries) + len(sents)


def stretto_narrative(page: str) -> str:
    text = _visible(page)
    start = text.find("Powered by Stretto")
    end = text.find("Chat locations")
    return text[start:end] if 0 <= start < end else text


def scan_stretto(url: str, page: str) -> tuple[list[Evidence], int]:
    sents = sentences(stretto_narrative(page))
    items = [Evidence(st, "Case page: " + snippet(s, st, EVIDENCE_MAX - 11), url, extract_buyer(s))
             for s in sents for st in [classify_stage(s)] if st]
    return items, len(sents)


# ── database helpers ────────────────────────────────────────────────────────────────────────────────────────────
@dataclass
class FamilyRow:
    family_id: int
    family_name: str
    filed_date: date | None
    court: str | None
    lead_docket_no: str | None
    senior_housing: str = "yes"
    members: list[dict] = field(default_factory=list)


def load_recent_families(conn) -> list[FamilyRow]:
    rows = conn.execute("""
        select f.family_id, f.family_name, f.filed_date, f.court, f.lead_docket_no, f.senior_housing,
               c.id, c.source_url, c.claims_agent_url, c.courtlistener_id, c.docket_no,
               coalesce(nullif(c.debtor_name, ''), c.case_name) as name
        from core.case_family f
        join core.case_family_member m using (family_id)
        join stg.court_case c on c.id = m.stg_court_case_id
        where f.within_36m and f.senior_housing in ('yes', 'maybe')
        order by f.filed_date desc nulls last, f.family_id, c.id""").fetchall()
    fams: dict[int, FamilyRow] = {}
    for r in rows:
        f = fams.setdefault(r["family_id"], FamilyRow(r["family_id"], r["family_name"], r["filed_date"],
                                                      r["court"], r["lead_docket_no"], r["senior_housing"]))
        f.members.append(dict(r))
    return list(fams.values())


def save_check(conn, family_id: int, channel: str, *, found: bool, ev: Evidence | None, source_url: str,
               items: int | None, requests: int = 0, note: str | None = None) -> str:
    """Upsert one row per (family, channel). A found stage is never replaced by a not-found or an earlier stage;
    checked_at, items_scanned and requests_used always reflect the latest check. Returns 'new'/'updated'/'kept'."""
    old = conn.execute("select sale_process_found, stage_text from core.sale_check where family_id=%s and channel=%s",
                       (family_id, channel)).fetchone()
    vals = dict(fid=family_id, ch=channel, found=found, stage=ev.stage if ev else None,
                buyer=ev.buyer if ev else None, ev=(ev.text[:EVIDENCE_MAX] if ev else None),
                url=(ev.url if ev else source_url), items=items, req=requests, note=(note or None) and note[:500])
    keep_old = old and old["sale_process_found"] and (
        not found or STAGE_RANK.get(vals["stage"], 0) < STAGE_RANK.get(old["stage_text"], 0))
    if keep_old:
        conn.execute("""update core.sale_check set checked_at=now(), items_scanned=%(items)s, requests_used=%(req)s
                        where family_id=%(fid)s and channel=%(ch)s""", vals)
        return "kept"
    conn.execute("""
        insert into core.sale_check (family_id, channel, checked_at, sale_process_found, stage_text, buyer_name,
                                     evidence_text, source_url, items_scanned, requests_used, note)
        values (%(fid)s, %(ch)s, now(), %(found)s, %(stage)s, %(buyer)s, %(ev)s, %(url)s, %(items)s, %(req)s, %(note)s)
        on conflict (family_id, channel) do update set checked_at=now(), sale_process_found=excluded.sale_process_found,
          stage_text=excluded.stage_text, buyer_name=excluded.buyer_name, evidence_text=excluded.evidence_text,
          source_url=excluded.source_url, items_scanned=excluded.items_scanned,
          requests_used=excluded.requests_used, note=excluded.note""", vals)
    return "updated" if old else "new"


# ── channel: claims agent ───────────────────────────────────────────────────────────────────────────────────────
def claims_agent_pages(fam: FamilyRow) -> tuple[list[tuple[str, str]], list[str]]:
    """[(kind, url)] readable pages (lead docket first) and notes about unreadable ones."""
    pages, notes, seen = [], [], set()
    for m in sorted(fam.members, key=lambda m: norm_docket(m["docket_no"]) != fam.lead_docket_no):
        for url in (m["claims_agent_url"], m["source_url"]):
            kind = source_kind(url)
            if not url or url in seen or kind not in ("elevenflo", "stretto", "epiq"):
                continue
            seen.add(url)
            if kind == "epiq":
                notes.append("Epiq page is JavaScript-only (manual)")
            else:
                pages.append((kind, url))
    return pages[:MAX_PAGES_PER_FAMILY], sorted(set(notes))


def run_claims_agent(conn, fams: list[FamilyRow], client, dry_run=False, log=print) -> list[dict]:
    results = []
    for fam in fams:
        pages, notes = claims_agent_pages(fam)
        if not pages:
            continue
        items, scanned, read_urls = [], 0, []
        for kind, url in pages:
            try:
                r = client.get(url, use_cache=False)
            except SourceBlocked as exc:
                notes.append(f"{kind} blocked: {exc}")
                continue
            if r.status_code != 200:
                notes.append(f"{kind} HTTP {r.status_code}")
                continue
            got, n = (scan_elevenflo if kind == "elevenflo" else scan_stretto)(url, r.text)
            if kind == "stretto":
                notes.append("Stretto docket list is reCAPTCHA-gated (manual); case page text only")
            items += got
            scanned += n
            read_urls.append(url)
        if not read_urls:
            continue
        ev = best_evidence(items)
        res = {"family_id": fam.family_id, "family": fam.family_name, "filed": fam.filed_date,
               "stage": ev.stage if ev else None, "buyer": ev.buyer if ev else None,
               "url": ev.url if ev else read_urls[0], "evidence": ev.text if ev else None, "scanned": scanned}
        results.append(res)
        if not dry_run:
            save_check(conn, fam.family_id, "claims_agent", found=ev is not None, ev=ev, source_url=read_urls[0],
                       items=scanned, note="; ".join(sorted(set(notes))))
            conn.commit()
        log(f"  [{fam.filed_date}] {fam.family_name[:50]:50} | {res['stage'] or '-':18} | {res['buyer'] or ''}")
    return results


# ── channel: CourtListener ──────────────────────────────────────────────────────────────────────────────────────
class RateLimited(Exception):
    pass


def courtlistener_candidates(conn, fams: list[FamilyRow], now: datetime | None = None) -> list[FamilyRow]:
    """Recent senior-housing 'yes' families with a CourtListener docket and no readable claims-agent page, not
    checked in the last 7 days ('maybe' families do not spend the scarce CourtListener budget)."""
    now = now or datetime.now(timezone.utc)
    readable = {r["family_id"] for r in conn.execute(
        "select family_id from core.sale_check where channel = 'claims_agent' and coalesce(items_scanned, 0) > 0")}
    recent = {r["family_id"] for r in conn.execute(
        "select family_id from core.sale_check where channel = 'courtlistener' and checked_at > %s",
        (now - timedelta(days=RECHECK_DAYS),))}
    out = []
    for f in fams:
        if f.senior_housing != "yes" or f.family_id in readable or f.family_id in recent:
            continue
        if any(m["courtlistener_id"] for m in f.members):
            out.append(f)
    return out


def requests_used_last_24h(conn, now: datetime | None = None) -> int:
    now = now or datetime.now(timezone.utc)
    r = conn.execute("select coalesce(sum(requests_used), 0) n from core.sale_check "
                     "where channel = 'courtlistener' and checked_at > %s", (now - timedelta(hours=24),)).fetchone()
    return int(r["n"])


def lead_cl_docket(fam: FamilyRow) -> dict | None:
    cl = [m for m in fam.members if m["courtlistener_id"]]
    cl.sort(key=lambda m: (norm_docket(m["docket_no"]) != fam.lead_docket_no, m["docket_no"]))
    return cl[0] if cl else None


def entry_evidence(entry: dict, docket_url: str) -> Evidence | None:
    from urllib.parse import urljoin
    from .courtlistener import SITE
    docs = entry.get("recap_documents") or []
    texts = [entry.get("description") or ""] + [d.get("description") or d.get("short_description") or "" for d in docs]
    text = " ".join(t for t in texts if t).strip()
    stage = classify_stage(text)
    if not stage:
        return None
    url = next((urljoin(SITE, d["absolute_url"]) for d in docs if d.get("absolute_url")), None)
    num = entry.get("entry_number")
    url = url or (f"{docket_url}#entry-{num}" if num is not None else docket_url)
    label = f"Entry {num} ({entry.get('date_filed')}) " if num is not None else ""
    return Evidence(stage, label + snippet(text, stage, EVIDENCE_MAX - len(label)), url, extract_buyer(text),
                    datetime.strptime(entry["date_filed"], "%Y-%m-%d").date() if entry.get("date_filed") else None)


def check_courtlistener_family(client, fam: FamilyRow, max_requests: int) -> tuple[Evidence | None, int, int, str]:
    """Returns (best evidence, entries scanned, requests used, docket url). Raises RateLimited on 429."""
    from .courtlistener import API
    m = lead_cl_docket(fam)
    url, params = f"{API}/docket-entries/", {"docket": m["courtlistener_id"], "order_by": "-entry_number"}
    items, scanned, used = [], 0, 0
    while url and used < max_requests:
        r = client.get(url, params=params, use_cache=False)
        used += 1
        if r.status_code == 429:
            raise RateLimited(f"429 from CourtListener (Retry-After={r.headers.get('retry-after', '?')})")
        if r.status_code != 200:
            break
        data = r.json()
        for e in data.get("results", []):
            scanned += 1
            ev = entry_evidence(e, m["source_url"])
            if ev:
                items.append(ev)
        if items:
            break
        url, params = data.get("next"), None
    return best_evidence(items), scanned, used, m["source_url"]


def run_courtlistener(conn, fams: list[FamilyRow], client, max_families: int = MAX_FAMILIES_CAP,
                      dry_run=False, now=None, log=print) -> dict:
    max_families = max(0, min(max_families, MAX_FAMILIES_CAP))
    used_today = requests_used_last_24h(conn, now)
    budget = min(max_families, DAILY_BUDGET - used_today)
    todo = courtlistener_candidates(conn, fams, now)
    summary = {"candidates": len(todo), "checked": 0, "requests": 0, "found": 0, "stopped": None,
               "budget": max(budget, 0), "used_before": used_today}
    if budget <= 0:
        summary["stopped"] = f"daily budget used ({used_today}/{DAILY_BUDGET} requests in the last 24 h)"
        return summary
    client.max_requests = budget          # hard cap inside the client as well
    batch = todo[:budget]
    for i, fam in enumerate(batch):
        remaining_after = len(batch) - i - 1
        spare = budget - summary["requests"] - remaining_after
        try:
            ev, scanned, used, docket_url = check_courtlistener_family(client, fam, max(1, spare))
        except RateLimited as exc:
            summary["stopped"] = str(exc)
            log(f"  STOP: {exc}. Not retrying; the rest waits for the next run.")
            break
        except RequestCapReached as exc:
            summary["stopped"] = str(exc)
            break
        except SourceBlocked as exc:
            summary["stopped"] = f"blocked: {exc}"
            log(f"  STOP: CourtListener refused access ({exc}); mark manual and check the token.")
            break
        summary["requests"] += used
        summary["checked"] += 1
        summary["found"] += ev is not None
        if not dry_run:
            save_check(conn, fam.family_id, "courtlistener", found=ev is not None, ev=ev, source_url=docket_url,
                       items=scanned, requests=used)
            conn.commit()
        log(f"  [{fam.filed_date}] {fam.family_name[:50]:50} | {ev.stage if ev else '-':18} | {(ev.buyer if ev else '') or ''}")
    return summary


# ── channel: news ───────────────────────────────────────────────────────────────────────────────────────────────
PHRASE_CUT = re.compile(r"^(?:llc|l\.l\.c|inc|lp|l\.p|ltd|corp|corporation|co|company|holdings?|operations|"
                        r"operating|opco|propco|realty|acquisition|jv|group|dst|gp|fund|et|dba|d/b/a|fka|f/k/a|"
                        r"trust|partners)\.?,?$", re.I)
CONNECTORS = {"of", "and", "&", "at", "the", "for", "a"}
GENERIC = {"senior", "living", "assisted", "care", "health", "healthcare", "nursing", "home", "center", "centre",
           "rehabilitation", "rehab", "memory", "skilled", "village", "community", "services", "the", "manor",
           "retirement", "life", "plan", "estate", "holding", "real"}


def name_phrases(name: str) -> list[str]:
    """Distinctive phrase(s) for news matching:
      - drop parentheses, d/b/a / f/k/a tails and a leading 'The'; cut at the first corporate word (LLC, Inc,
        Holdings, Operations, Opco, ...) or ' - ';
      - keep at most 4 words and drop trailing connectors ('of', 'and', ...);
      - a phrase needs at least two non-connector words, not all generic (Senior, Living, Care, ...); a single
        word ('Courtyard', 'Magnolia') is too common to match on;
      - a leading 'The' is kept, so 'The Oaks Senior Living' does not match '12 Oaks Senior Living';
      - also try the part before ' of ' / ' at ' when it is itself distinctive ('Mission Point of Detroit' ->
        'Mission Point').
    Returns [] when nothing distinctive is left (the family is then not matched in news)."""
    n = re.sub(r"\([^)]*\)", " ", name or "")
    n = re.split(r"\s+-\s+|,?\s*\b(?:d/b/a|dba|f/k/a|fka|a/k/a)\b", n, flags=re.I)[0]
    words = [w.strip(",") for w in n.split() if w.strip(",")]
    lead_the = bool(words) and words[0].lower() == "the"     # kept: 'The Oaks ...' must not match '12 Oaks ...'
    if lead_the:
        words = words[1:]
    kept = []
    for w in words:
        if PHRASE_CUT.match(w):
            break
        kept.append(w)
    kept = kept[:4]
    while kept and kept[-1].lower() in CONNECTORS:
        kept.pop()

    def distinctive(ws):
        core = [w for w in ws if w.lower() not in CONNECTORS]
        if not core or all(w.lower().strip(".") in GENERIC for w in core):
            return False
        return len(core) >= 2

    out = []
    if kept and distinctive(kept):
        out.append(("The " if lead_the else "") + " ".join(kept))
    for sep in ("of", "at"):
        low = [w.lower() for w in kept]
        if sep in low[1:]:
            head = kept[:low.index(sep, 1)]
            if len(head) >= 2 and distinctive(head):
                out.append(" ".join(head))
    return list(dict.fromkeys(out))


def place_patterns(cities: set[str], states: set[str]) -> list[re.Pattern]:
    pats = []
    for c in sorted(cities):
        if c and len(c) >= 3:
            pats.append(re.compile(r"\b" + re.escape(c) + r"\b", re.I))
    for s in sorted(states):
        if s in STATE_NAMES:
            pats.append(re.compile(r"\b" + re.escape(STATE_NAMES[s]) + r"\b", re.I))
            pats.append(re.compile(r",\s*" + s + r"\b"))           # 'Dallas, TX' (case-sensitive postal code)
    return pats


def news_match(title: str, body: str, phrases: list[str], places: list[re.Pattern], extracted_buyer: str | None = None,
               url: str = "", when: date | None = None) -> Evidence | None:
    """Article qualifies only if it names the family (phrase) AND one of its places. Evidence is a sentence that
    contains the phrase and a sale step; failing that, a headline naming a sale step plus the sentence naming
    the family."""
    text = f"{title}. {body or ''}"
    if not places or not any(p.search(text) for p in places):
        return None
    prx = [re.compile(r"\b" + re.escape(p) + r"\b", re.I) for p in phrases]
    best = None
    for s in sentences(text):
        if not any(r.search(s) for r in prx):
            continue
        stage = classify_stage(s, news=True)
        if not stage:
            continue
        buyer = extract_buyer(s, news=True)
        if not buyer and extracted_buyer and extracted_buyer != "NOT FOUND" and extracted_buyer in s:
            buyer = extracted_buyer
        ev = Evidence(stage, snippet(s, stage), url, buyer, when)
        if best is None or (STAGE_RANK[ev.stage], ev.buyer is not None) > (STAGE_RANK[best.stage], best.buyer is not None):
            best = ev
    if best is None:
        # Headline fallback: the headline names a sale step and the article body names the family.
        stage = classify_stage(title, news=True)
        named = next((s for s in sentences(body or "") if any(r.search(s) for r in prx)), None)
        if stage and named:
            text = f"Headline: {title} | {named}"
            best = Evidence(stage, snippet(text, stage), url, extract_buyer(title, news=True), when)
    return best


def family_places(conn, fam: FamilyRow) -> tuple[set[str], set[str]]:
    rows = conn.execute("""
        select distinct fa.city, fa.state from core.case_family_member m
        join stg.court_case c on c.id = m.stg_court_case_id
        join core.legal_case l on l.court = c.court and l.docket_no = c.docket_no
        join core.case_facility cf on cf.case_id = l.case_id
        join core.facility fa on fa.facility_id = cf.facility_id
        where m.family_id = %s""", (fam.family_id,)).fetchall()
    cities = {r["city"].title() for r in rows if r["city"]}
    states = {r["state"].upper() for r in rows if r["state"]}
    cs = court_state(fam.court)
    if cs:
        states.add(cs)
    return cities, states


def family_phrases(fam: FamilyRow) -> list[str]:
    """Phrases from the family name plus the member name carrying the family's most common distinctive word."""
    from collections import Counter
    from .families import clean_name, distinctive_tokens
    out = name_phrases(fam.family_name)
    names = [clean_name(m["name"]) for m in fam.members if m.get("name")]
    tok = Counter(t for n in names for t in set(distinctive_tokens(n)))
    # Only when one word runs through at least half of a multi-case family (e.g. 'Inspired', 'LaVie').
    if len(names) >= 3 and tok and tok.most_common(1)[0][1] >= len(names) / 2:
        top = tok.most_common(1)[0][0]
        cand = sorted((n for n in names if top in n.lower()), key=len)
        if cand:
            out += name_phrases(cand[0])
    return list(dict.fromkeys(out))


def run_news(conn, fams: list[FamilyRow], dry_run=False, log=print) -> list[dict]:
    results = []
    for fam in fams:
        phrases = family_phrases(fam)
        cities, states = family_places(conn, fam)
        places = place_patterns(cities, states)
        if not phrases:
            note = "name not distinctive enough for news matching"
            hits, best = [], None
        else:
            like = [f"%{p}%" for p in phrases]
            hits = conn.execute("""
                select d.source_url, d.title, d.body_text, d.published_at::date as d,
                       e.fields->>'buyer_or_new_operator' as buyer
                from raw.document d
                join raw.source s on s.source_id = d.source_id and s.category = 'news'
                left join lateral (select fields from stg.extraction x where x.document_id = d.id
                                   order by x.created_at desc limit 1) e on true
                where d.title ilike any(%s) or d.body_text ilike any(%s)
                order by d.published_at desc nulls last limit 60""", (like, like)).fetchall()
            evs = [ev for h in hits for ev in [news_match(h["title"] or "", h["body_text"] or "", phrases, places,
                                                          h["buyer"], h["source_url"], h["d"])] if ev]
            best = best_evidence(evs)
            note = f"phrases={phrases}; places={len(cities)} cities/{sorted(states)}; articles naming family={len(hits)}"
        res = {"family_id": fam.family_id, "family": fam.family_name, "filed": fam.filed_date,
               "stage": best.stage if best else None, "buyer": best.buyer if best else None,
               "url": best.url if best else None, "evidence": best.text if best else None, "articles": len(hits)}
        results.append(res)
        if not dry_run and best:
            save_check(conn, fam.family_id, "news", found=True, ev=best, source_url=best.url, items=len(hits), note=note)
        elif not dry_run and hits:
            # Not found, but record what was searched; source_url = the newest article that named the family.
            save_check(conn, fam.family_id, "news", found=False, ev=None, source_url=hits[0]["source_url"],
                       items=len(hits), note=note)
        conn.commit()
        if best:
            log(f"  [{fam.filed_date}] {fam.family_name[:50]:50} | {best.stage:18} | {best.buyer or ''} | {best.url}")
    return results


# ── CLI ─────────────────────────────────────────────────────────────────────────────────────────────────────────
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m src.discover.courts.sale_check")
    ap.add_argument("--channel", choices=CHANNELS, action="append",
                    help="channel(s) to run (default: claims_agent and news; courtlistener only when named)")
    ap.add_argument("--max-families", type=int, default=None,
                    help=f"courtlistener: families per run (default and hard cap {MAX_FAMILIES_CAP}); "
                         "other channels: newest N families only (for a quick sample run)")
    ap.add_argument("--dry-run", action="store_true", help="fetch and print; write nothing")
    a = ap.parse_args(argv)
    channels = a.channel or ["claims_agent", "news"]
    from src.common.db import connect
    conn = connect()
    code = 0
    try:
        fams = load_recent_families(conn)
        print(f"recent families (filed within 36 months, senior housing yes/maybe): {len(fams)}")
        for ch in channels:
            print(f"[{ch}]")
            if ch == "claims_agent":
                with PoliteClient("sale_check_claims_agent") as client:
                    res = run_claims_agent(conn, fams[:a.max_families] if a.max_families else fams, client,
                                           a.dry_run)
                print(f"  families with a readable page: {len(res)}; sale process found: {sum(bool(r['stage']) for r in res)}")
            elif ch == "news":
                res = run_news(conn, fams[:a.max_families] if a.max_families else fams, a.dry_run)
                print(f"  families searched: {len(res)}; with sale news: {sum(bool(r['stage']) for r in res)}")
            else:
                from .courtlistener import make_client
                try:
                    client = make_client(max_retries=0)
                except MissingSetting:
                    print("  COURTLISTENER_TOKEN is not set; cannot run the courtlistener channel.")
                    code = 2
                    continue
                with client:
                    s = run_courtlistener(conn, fams, client, a.max_families or MAX_FAMILIES_CAP, a.dry_run)
                print(f"  {s}")
    finally:
        conn.close()
    return code


if __name__ == "__main__":
    sys.exit(main())
