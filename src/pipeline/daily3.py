"""Nightly refresh of Deal Radar (daily3): follow open court sales, pick up new filings and articles, gate, publish.

    python -m src.pipeline.daily3 --dry-run            # reads Supabase + the network; writes ONLY output/daily3_dryrun/
    python -m src.pipeline.daily3 --live [--publish]   # writes Supabase (cursors, run log, findings); --publish uploads
                                                       # the snapshot when every gate passes (needs RENDER_API_KEY)

One of --dry-run / --live is required. Order of work (the follow-through is the main job, so it spends first):

B. Follow-through. Every lead whose stage is "in a sale" (no order yet) or "Sale approved": re-read its CourtListener
   docket entries (newest first, only entries after the docket's cursor) and its Epiq case page. A sale order or a
   closing notice is detected by the existing code rules (src.discover.courts.sale_check: classify_stage /
   extract_buyer, literal text only, no AI) and mapped to a stage with rules.court_sale_status. Kroll stays manual.
   - Manual overrides (config/manual_overrides.yaml, Nov 11) always win; only a public source dated AFTER the
     override may supersede one, and that change is flagged unreviewed.
   - A page we may not read (robots, 401/403, CAPTCHA, JavaScript-only) keeps its link and is flagged
     needs_human_click. Anything no source states stays NOT FOUND.
A. Discover (incremental, per-source cursors): CourtListener v4 search for new bankruptcy dockets (token from
   COURTLISTENER_TOKEN), Senior Housing News and Skilled Nursing News RSS. New documents only, de-duplicated across
   feeds (canonical URL + title hash) and against raw.document, then the existing pipeline: roundup split -> Claude
   extraction (only tonight's documents) -> enrich -> scoring (former-operator rule, stage rules, max 2 per buyer).
   Google News RSS is dropped (robots.txt disallows /rss/search, checked Nov 12, 2026) and Stretto is skipped
   (Step 0 rule: 0 first signals and 0 buyers among the published leads); see SKIPPED.
   A buyer read from a multi-property roundup article is shown as buyer_status 'Unverified' ONLY with
   --roundup-unverified (off by default: it changes List A and the published counts).

Budgets: CourtListener <= 45 calls per rolling hour and <= 100 per rolling 24 h, counting every logged call (this
run, earlier daily3 runs and the case-list downloader in raw.courtlistener_calls, the sale-check job in
core.sale_check); each call is checked before it is sent and logged with endpoint and status. LLM <= $1 per run.
Whatever does not fit is queued for the next run and logged. Gates (any failure blocks publish): tests pass; leak scan with the private term list at PRIVATE_TERMS_PATH
(terms are never printed); every lead has a public source link; lead count within +/-10% of the last run; packed
snapshot < 800 KB. A new top-20 lead or any stage change publishes with reviewed=false.

Dry run: no Supabase writes (read-only session), no cursor advance, no publish, no LLM calls (cost is estimated).
Cursors and the run log go to output/daily3_dryrun/*.json. The candidate snapshot is the last published one with
tonight's findings applied in memory. Live: cursors in raw.source_cursors, run log in raw.refresh_runs
(supabase/migrations/20261112000100_daily3_refresh.sql; NOT applied yet).
"""
from __future__ import annotations

import argparse
import base64
import copy
import gzip
import hashlib
import json
import os
import re
import subprocess
import sys
import uuid
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from src.common.config import OUTPUT_DIR, PROJECT_ROOT

# ── settings ────────────────────────────────────────────────────────────────────────────────────────────────────
CL_HOUR_CAP = 45                 # CourtListener calls per ROLLING hour, all logged jobs (account limit 50/hour)
CL_DAY_CAP = 100                 # CourtListener calls per ROLLING 24 h, all logged jobs (account limit 125/day)
LLM_MAX_USD = 1.0                # per run
SNAPSHOT_MAX_BYTES = 800 * 1024  # packed upload (Render allows 1 MB for all Secret Files)
LEAD_CHANGE_MAX = 0.10           # lead count may move at most +/-10% between runs
FULLTEXT_CAP = 30                # article pages fetched per run (the rest is queued)
OVERLAP_DAYS = 3                 # CourtListener search re-reads this many days before the cursor (late indexing)
FIRST_RUN_LOOKBACK_DAYS = 2      # a source without a cursor starts this far back
DRYRUN_DIR = OUTPUT_DIR / "daily3_dryrun"
PUBLISHED = OUTPUT_DIR / "site_snapshot.uploaded.b64"   # laptop only: last upload, used when no run log exists yet
NEWS_FEEDS = {"shn_rss": "https://seniorhousingnews.com/feed/", "snn_rss": "https://skillednursingnews.com/feed/"}
GNEWS = "gnews_rss"
SKIPPED = {   # decided Nov 12, 2026 (Step 0, output/source_impact.csv); re-check before re-enabling
    GNEWS: "robots.txt disallows /rss/search for all user agents (checked Nov 12, 2026); never worked around",
    "stretto": "Step 0 decision rule: 0 first signals and 0 buyers among the 485 published leads",
}
MANUAL_HOSTS = {"restructuring.ra.kroll.com": "Kroll stays manual"}
NF = "NOT FOUND"


# ── pure rules (tested in tests/pipeline/test_daily3.py) ────────────────────────────────────────────────────────
STATUS_RANK = {None: 0, "approved": 1, "closed": 2}


@dataclass
class Finding:
    """One sale step read from a public page for one lead."""
    lead_id: str
    stage_text: str                      # sale_check stage ('sale order', 'sale closed', ...)
    source_url: str
    when: str | None                     # ISO date the source gives; None = undated
    buyer: str | None = None             # literal name next to a buyer phrase, else None
    channel: str = "courtlistener"
    family_id: int | None = None
    evidence: str = ""


@dataclass
class Change:
    lead_id: str
    property: str
    kind: str                            # stage_changed / kept_override / not_newer / undated
    old_stage: str | None
    new_stage: str | None
    sale_status: dict | None = None
    buyer: str | None = None
    buyer_status: str | None = None
    supersedes_override: bool = False
    reviewed: bool = False
    source_url: str | None = None
    channel: str | None = None
    family_id: int | None = None
    note: str = ""


def stage_key(item: dict) -> str:
    """'sale', 'done:approved', 'done:closed', ... (what a stage change is measured on)."""
    s = (item.get("sale_status") or {}).get("status")
    return f"{item.get('stage')}:{s}" if s else str(item.get("stage"))


def decide(lead: dict, f: Finding, override: dict | None) -> Change | None:
    """Turn one finding into a change for one lead, or None when it changes nothing.

    lead: snapshot item (stage, sale_status, buyer, buyer_status, property). override: {'date': ISO,
    'sale_status': 'approved'|'closed'|None, 'buyer': bool} from config/manual_overrides.yaml, or None.
    Stage rules are code: rules.court_sale_status ('sale order' -> approved, 'sale closed' -> closed). Never a
    downgrade, never from an undated or non-web source; an override wins unless the source is dated after it.
    """
    from src.score import rules
    new_status = rules.court_sale_status(f.stage_text)
    cur_status = (lead.get("sale_status") or {}).get("status")
    if not new_status or STATUS_RANK[new_status] <= STATUS_RANK.get(cur_status, 0):
        return None
    base = dict(lead_id=f.lead_id, property=lead.get("property") or "", old_stage=stage_key(lead),
                source_url=f.source_url, channel=f.channel, family_id=f.family_id)
    if not str(f.source_url).startswith(("http://", "https://")):
        return None
    if not f.when:
        return Change(kind="undated", new_stage=None, note="sale step found but the source gives no date: not applied",
                      **base)
    supersedes = False
    if override and override.get("date"):
        if date.fromisoformat(f.when[:10]) <= date.fromisoformat(str(override["date"])[:10]):
            return Change(kind="kept_override", new_stage=None,
                          note=f"manual override ({override['date']}) wins: source dated {f.when} is not newer", **base)
        supersedes = True
    sale = {"status": new_status, "date": f.when[:10], "source_url": f.source_url, "basis": "court"}
    buyer, buyer_status = None, None
    cur_buyer = lead.get("buyer")
    if f.buyer and (not cur_buyer or lead.get("buyer_status") in ("candidate", NF, None)) \
            and not (override and override.get("buyer") and not supersedes):
        buyer, buyer_status = f.buyer, "named"          # named in a court filing next to a buyer phrase
    return Change(kind="stage_changed", new_stage=f"done:{new_status}", sale_status=sale, buyer=buyer,
                  buyer_status=buyer_status, supersedes_override=supersedes, reviewed=False,
                  note="newer public source supersedes the manual override" if supersedes else "", **base)


def is_roundup(source_url: str | None, title: str | None, body: str | None = None) -> bool:
    """Shared rule (src.extract.split.is_roundup): a split deal child or a roundup title / slug."""
    from src.extract.split import is_roundup as rule
    return rule(source_url, title)


def news_buyer_status(status: str, roundup: bool) -> str:
    """Buyer from a roundup article -> 'Unverified' (shown in the Unverified group, never as named)."""
    return "Unverified" if roundup and status not in (NF, None) else status


def title_hash(title: str | None) -> str:
    t = re.sub(r"[^a-z0-9]+", " ", (title or "").lower()).strip()
    return hashlib.sha256(t.encode()).hexdigest()[:16] if t else ""


def dedupe_new(docs: list, seen_urls: set[str], seen_titles: set[str]) -> tuple[list, int]:
    """Keep docs whose canonical URL AND title hash are both unseen (the same story in two feeds counts once)."""
    from src.discover.news.text import canonical_url
    out, dup = [], 0
    for d in docs:
        u, t = canonical_url(d.source_url), title_hash(d.title)
        if u in seen_urls or (t and t in seen_titles):
            dup += 1
            continue
        seen_urls.add(u)
        if t:
            seen_titles.add(t)
        d.source_url = u
        out.append(d)
    return out, dup


def newer_than(published: datetime | None, cursor_iso: str | None) -> bool:
    """Undated items count as new (the URL de-dupe keeps them from repeating)."""
    if not cursor_iso or published is None:
        return True
    return published > datetime.fromisoformat(cursor_iso)


def new_entries(entries: list[dict], last_entry: int | None) -> list[dict]:
    """Docket entries after the docket cursor (all of them on the first visit)."""
    if last_entry is None:
        return list(entries)
    return [e for e in entries if (e.get("entry_number") or 0) > last_entry]


def advance(cursor: dict | None, **kw) -> dict:
    """New cursor value: never moves backwards (a later run with older data keeps the newer mark)."""
    out = dict(cursor or {})
    for k, v in kw.items():
        if v is None:
            continue
        old = out.get(k)
        out[k] = v if old is None or v > old else old
    return out


def snapshot_state(snap: dict) -> dict:
    items = snap.get("items") or []
    return {"lead_count": len(items), "stages": {r["lead_id"]: stage_key(r) for r in items},
            "buyers": {r["lead_id"]: f"{r.get('buyer') or NF}|{r.get('buyer_status') or NF}" for r in items},
            "top20": [r["lead_id"] for r in items if r.get("rank") and r["rank"] <= 20]}


def diff_states(prev: dict | None, cand: dict) -> dict:
    if not prev:
        return {"new": [], "dropped": [], "stage_changed": [], "buyer_changed": [], "top20_entrants": [],
                "top20_buyer_changed": [], "baseline": "none"}
    ps, cs = prev["stages"], cand["stages"]
    pb, cb = prev.get("buyers") or {}, cand.get("buyers") or {}
    buyer_changed = sorted(k for k in set(pb) & set(cb) if pb[k] != cb[k])
    top = set(prev["top20"]) | set(cand["top20"])
    return {"new": sorted(set(cs) - set(ps)), "dropped": sorted(set(ps) - set(cs)),
            "stage_changed": sorted(k for k in set(ps) & set(cs) if ps[k] != cs[k]),
            "buyer_changed": buyer_changed, "top20_entrants": sorted(set(cand["top20"]) - set(prev["top20"])),
            "top20_buyer_changed": [k for k in buyer_changed if k in top], "baseline": prev.get("label", "")}


def lead_count_ok(prev_n: int | None, n: int) -> tuple[bool, str]:
    if not prev_n:
        return True, f"{n} leads (no earlier run to compare)"
    change = (n - prev_n) / prev_n
    return abs(change) <= LEAD_CHANGE_MAX, f"{prev_n} -> {n} ({change:+.1%}, limit +/-{LEAD_CHANGE_MAX:.0%})"


def links_ok(snap: dict) -> tuple[bool, str]:
    items = snap.get("items") or []
    bad = [r.get("lead_id") for r in items if not any(str(u).startswith(("http://", "https://"))
                                                      for u in (r.get("sources") or []))]
    bad_sale = [r.get("lead_id") for r in items if r.get("sale_status")
                and not str(r["sale_status"].get("source_url") or "").startswith(("http://", "https://"))]
    ok = not bad and not bad_sale
    return ok, f"{len(items) - len(bad)}/{len(items)} leads with a public link" + (
        f"; {len(bad_sale)} sale statuses without a link" if bad_sale else "")


def load_terms(path: str | None) -> tuple[list[str], list[str]]:
    """Private term list (same format as tools/leak_guard.py: one term per line, '!' = allowed phrase)."""
    if not path or not Path(path).is_file():
        return [], []
    terms, allow = [], []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            (allow if line.startswith("!") else terms).append(line.lstrip("!").strip().lower())
    return terms, allow


def leak_scan(snap: dict, terms_path: str | None) -> tuple[bool, str]:
    """Scan the upload keys (home-city names masked, as publish does). Reports 'term #N' only, never the term.
    The web server derives extra lists (leads, list_a, ...) when it loads a file; those are not uploaded."""
    from src.web.publish import CLIENT_TERM, mask_place_names
    from src.web.snapshot import UPLOAD_KEYS
    terms, allow = load_terms(terms_path)
    if not terms:
        return False, "PRIVATE_TERMS_PATH not set or empty: cannot check (blocks publish)"
    upload = {k: snap[k] for k in UPLOAD_KEYS if k in snap}         # exactly what is uploaded, as publish checks it
    low = json.dumps(mask_place_names(upload), ensure_ascii=False, default=str).lower()
    for a in allow:
        low = low.replace(a, "")
    hits = [f"term #{i}" for i, t in enumerate(terms, 1) if t in low]
    if "licensed:" in low:
        hits.append("'licensed:' tag")
    if CLIENT_TERM in low:
        hits.append("client name")
    return not hits, "clean" if not hits else "FOUND " + ", ".join(hits)


def packed_size(snap: dict) -> int:
    from src.web.snapshot import UPLOAD_KEYS
    raw = json.dumps({k: snap[k] for k in UPLOAD_KEYS if k in snap}, ensure_ascii=False, separators=(",", ":"),
                     default=str).encode("utf-8")
    return len(base64.b64encode(gzip.compress(raw, 9)))


def apply_changes(snap: dict, changes: list[Change], unverified: dict[str, str]) -> dict:
    """Candidate snapshot = snapshot + tonight's applied changes (dry run; live rebuilds from the database)."""
    out = copy.deepcopy(snap)
    by_id = {r["lead_id"]: r for r in out.get("items") or []}
    for c in changes:
        r = by_id.get(c.lead_id)
        if not r or c.kind != "stage_changed":
            continue
        r["stage"], r["sale_status"] = "done", {k: v for k, v in c.sale_status.items() if k != "basis"}
        flags = r.setdefault("flags", [])
        for fl in ["sale_" + c.sale_status["status"], "unreviewed_change"] + (
                ["override_superseded"] if c.supersedes_override else []):
            if fl not in flags:
                flags.append(fl)
        if c.source_url not in (r.get("sources") or []):
            r.setdefault("sources", []).insert(0, c.source_url)
        if c.buyer:
            r["buyer"], r["buyer_status"], r["buyer_group"] = c.buyer, c.buyer_status, "confirmed"
    changed = False
    for lid, why in unverified.items():
        r = by_id.get(lid)
        if r and r.get("buyer") and r.get("buyer_status") != "Unverified":
            r["buyer_status"], r["buyer_group"] = "Unverified", "inferred"
            r["rank_a"], r["lists"] = None, [x for x in r.get("lists") or [] if x != "A"]   # not a named buyer
            if "buyer_from_roundup" not in r.setdefault("flags", []):
                r["flags"].append("buyer_from_roundup")
            changed = True
    if changed and out.get("summary"):
        from src.web.snapshot import summary
        out["summary"] = summary(out["items"], out.get("list_c") or [], out["summary"].get("list_c_linked_leads", 0))
    return out


# ── budgets, cursors, run log ───────────────────────────────────────────────────────────────────────────────────
@dataclass
class Budget:
    ledger: object = None            # courtlistener.CallLedger: every call logged, rolling hour / 24 h caps
    llm_cap: float = LLM_MAX_USD
    llm_used: float = 0.0
    llm_estimated: float = 0.0
    queued: list = field(default_factory=list)

    def queue(self, kind: str, key: str, why: str) -> None:
        self.queued.append({"kind": kind, "key": key, "why": why})

    @property
    def cl_used(self) -> int:
        return len(self.ledger.calls) if self.ledger else 0

    def cl_left(self) -> int:
        return self.ledger.remaining() if self.ledger else 0


class Store:
    """Cursors + run log. Live: raw.source_cursors / raw.refresh_runs. Dry run: output/daily3_dryrun/*.json
    (the dry run reads the live cursors when the table exists, and never advances them)."""

    def __init__(self, conn, dry_run: bool):
        self.conn, self.dry_run = conn, dry_run
        self.table_ok = self._has("raw.source_cursors") and self._has("raw.refresh_runs")

    def _has(self, name: str) -> bool:
        return self.conn.execute("select to_regclass(%s) is not null ok", (name,)).fetchone()["ok"]

    def load_cursors(self) -> tuple[dict, str]:
        if self.table_ok:
            rows = self.conn.execute("select source_id, cursor_key, cursor, queue from raw.source_cursors").fetchall()
            return {f"{r['source_id']}|{r['cursor_key']}": {"cursor": r["cursor"], "queue": r["queue"]}
                    for r in rows}, "raw.source_cursors"
        p = DRYRUN_DIR / "cursors.json"
        if self.dry_run and p.exists():
            return json.loads(p.read_text()).get("current") or {}, f"{p.name} (table not created yet)"
        return {}, "none (first run)"

    def last_state(self) -> tuple[dict | None, str]:
        if self.table_ok:
            r = self.conn.execute("select state, started_at from raw.refresh_runs where mode = 'live' and status in "
                                  "('published', 'no_change') order by started_at desc limit 1").fetchone()
            if r and r["state"]:
                return dict(r["state"], label=f"run log {r['started_at']:%Y-%m-%d %H:%M}"), "raw.refresh_runs"
        if PUBLISHED.exists():                        # laptop only (see portability)
            from src.web.server import load_snapshot
            snap = load_snapshot(str(PUBLISHED))
            return dict(snapshot_state(snap), label=f"published {snap.get('generated_at')}"), PUBLISHED.name
        return None, "none"

    def changelog_history(self) -> tuple[list, str]:
        """Earlier daily checks (run entries with their rows, last 30 days). Live runs keep it in the run log; a dry
        run only reads it and never adds to it, so repeated dry runs keep the same check number."""
        if self.table_ok:
            r = self.conn.execute("select changelog from raw.refresh_runs where mode = 'live' and status in "
                                  "('published', 'no_change') and changelog is not null "
                                  "order by started_at desc limit 1").fetchone()
            if r and r["changelog"]:
                return list(r["changelog"]), "raw.refresh_runs"
        return [], "none (first check)"

    @staticmethod
    def handover_state() -> tuple[dict | None, str]:
        """The hand-over (Nov 11) list: CHANGELOG_BASELINE_B64 (gzip + base64 of the JSON; a CI secret), else
        output/changelog_baseline_state.json made by `python -m src.pipeline.changelog`."""
        raw = (os.environ.get("CHANGELOG_BASELINE_B64") or "").strip()
        if raw:
            return json.loads(gzip.decompress(base64.b64decode(raw))), "CHANGELOG_BASELINE_B64"
        p = OUTPUT_DIR / "changelog_baseline_state.json"
        return (json.loads(p.read_text()), p.name) if p.exists() else (None, "missing")

    def changelog_baseline(self, history: list, prev: dict | None) -> tuple[dict | None, str]:
        """What tonight's rows are measured against: the last run's list; for the very first check, the hand-over
        list when it is available."""
        st, src = self.handover_state()
        if not history and st:
            return st, f"{src} ({st.get('label', '')})"
        return prev, (prev or {}).get("label", "nothing")

    def excluded_lead_ids(self) -> set[str]:
        """Lead ids of the confidential exclusions (the list holds building ids). Read only."""
        from src.pipeline import preflight
        ids = preflight.exclusion_ids()[0]
        if not ids:
            return set()
        return {str(r["lead_id"]) for r in self.conn.execute(
            "select lead_id from app.lead where facility_id::text = any(%s)", (ids,)).fetchall()}

    def insert_run(self, run: dict) -> None:
        from psycopg.types.json import Jsonb
        cols = ["run_id", "mode", "status", "started_at", "finished_at", "new_leads", "stage_changed", "dropped",
                "lead_count", "gates", "budget", "changes", "queued", "reviewed", "published", "state", "note",
                "changelog"]
        vals = [Jsonb(run[c]) if isinstance(run.get(c), (dict, list)) else run.get(c) for c in cols]
        self.conn.execute(f"insert into raw.refresh_runs ({', '.join(cols)}) values ({', '.join(['%s'] * len(cols))})",
                          vals)
        self.conn.commit()

    def cl_history(self) -> tuple[list, dict]:
        """Earlier CourtListener calls (rolling 24 h) from every record: raw.courtlistener_calls, the sale-check
        job's core.sale_check, and (dry run / before the table exists) the local dry-run call log."""
        from src.discover.courts.courtlistener import call_history
        out, src = call_history(self.conn, 24)
        p = DRYRUN_DIR / "courtlistener_calls.json"
        if (self.dry_run or not self.table_ok) and p.exists():
            since = datetime.now(timezone.utc) - timedelta(hours=24)
            local = [datetime.fromisoformat(c["at"]) for c in json.loads(p.read_text())]
            local = [t for t in local if t > since]
            out += local
            src[p.name] = len(local)
        return out, src

    def save(self, current: dict, proposed: dict, run: dict, calls: list[dict] | None = None) -> list[str]:
        if self.dry_run:
            DRYRUN_DIR.mkdir(parents=True, exist_ok=True)
            p = DRYRUN_DIR / "courtlistener_calls.json"          # real calls against the shared quota: kept 48 h
            keep = datetime.now(timezone.utc) - timedelta(hours=48)
            old = [c for c in (json.loads(p.read_text()) if p.exists() else [])
                   if datetime.fromisoformat(c["at"]) > keep]
            p.write_text(json.dumps(old + (calls or []), indent=1, default=str))
            (DRYRUN_DIR / "cursors.json").write_text(json.dumps(
                {"current": current, "proposed_not_applied": proposed}, indent=1, default=str))
            (DRYRUN_DIR / "run_log.json").write_text(json.dumps(run, indent=1, default=str))
            return [str(DRYRUN_DIR / "cursors.json"), str(DRYRUN_DIR / "run_log.json"), str(p)]
        if not self.table_ok:
            raise SystemExit("raw.source_cursors / raw.refresh_runs missing: apply "
                             "supabase/migrations/20261112000100_daily3_refresh.sql first")
        from psycopg.types.json import Jsonb
        for key, v in proposed.items():
            sid, ck = key.split("|", 1)
            self.conn.execute(
                "insert into raw.source_cursors (source_id, cursor_key, cursor, queue, updated_by_run) "
                "values (%s, %s, %s, %s, %s) on conflict (source_id, cursor_key) do update set "
                "cursor = excluded.cursor, queue = excluded.queue, updated_at = now(), updated_by_run = excluded.updated_by_run",
                (sid, ck, Jsonb(v.get("cursor") or {}), Jsonb(v.get("queue") or []), run["run_id"]))
        self.insert_run(run)
        if calls:
            with self.conn.cursor() as cur:
                cur.executemany("insert into raw.courtlistener_calls (called_at, job, run_id, method, endpoint, query, "
                                "status) values (%(at)s, %(job)s, %(run_id)s, %(method)s, %(endpoint)s, %(query)s, "
                                "%(status)s)", calls)
            self.conn.commit()
        return ["raw.source_cursors", "raw.refresh_runs", "raw.courtlistener_calls"]


# ── follow-through (B) ──────────────────────────────────────────────────────────────────────────────────────────
DOCKET = re.compile(r"courtlistener\.com/docket/(\d+)/")
EPIQ_CASE = re.compile(r"^https://dm\.epiq11\.com/case/[^/]+/")


def follow_targets(conn) -> list[dict]:
    """Leads in a sale (no court order yet) or with an approved sale, from the public view, with their docket links."""
    rows = [dict(r) for r in conn.execute(
        "select lead_id::text, facility_id::text, property, stage, flags, source_urls, buyer, buyer_status, score "
        "from app.v_lead_list where (stage = 'sale' and not (flags && array['sale_approved','sale_closed'])) "
        "or 'sale_approved' = any(flags) order by score desc").fetchall()]
    fams = {}
    for r in conn.execute(
            "select distinct facility_id::text f, split_part(source_key, ':', 2)::bigint fam from core.signal "
            "where source_key like 'fam:%%' and facility_id = any(%s::uuid[])", ([r["facility_id"] for r in rows],)):
        fams.setdefault(r["f"], set()).add(r["fam"])
    from src.discover.courts.sale_check import load_recent_families
    members = {f.family_id: f for f in load_recent_families(conn)}
    from src.score import overrides
    extra = overrides.extra_links(conn)
    for r in rows:
        links = [u for u in (r.get("source_urls") or []) + (extra.get(r["facility_id"]) or []) if isinstance(u, str)]
        dockets, epiq, fam_ids = {}, set(), sorted(fams.get(r["facility_id"], ()))
        for fid in fam_ids:
            fam = members.get(fid)
            for m in (fam.members if fam else []):
                if m.get("courtlistener_id"):
                    dockets.setdefault(str(m["courtlistener_id"]), (fid, m.get("source_url")))
                if m.get("claims_agent_url") and EPIQ_CASE.match(m["claims_agent_url"]):
                    epiq.add(m["claims_agent_url"])
        for u in links:
            m = DOCKET.search(u)
            if m:
                dockets.setdefault(m[1], (fam_ids[0] if fam_ids else None, u))
            if EPIQ_CASE.match(u):
                epiq.add(u)
        r.update(dockets=dockets, epiq=sorted(epiq), fam_ids=fam_ids,
                 manual=[u for u in links if any(h in u for h in MANUAL_HOSTS)],
                 stretto=[u for u in links if "stretto.com" in u])
    return rows


def override_map(conn) -> dict:
    """{facility_id: {'date', 'sale_status', 'buyer'}} from config/manual_overrides.yaml."""
    from src.score import overrides, rules
    out = {}
    for o in rules.manual_overrides():
        fid = overrides._facility(conn, o["match"])
        if fid:
            out[fid] = {"date": str(o["date"]), "sale_status": o.get("sale_status"), "buyer": "buyer" in o}
    return out


def read_docket(client, docket_id: str, docket_url: str, cursor: dict, budget: Budget) -> tuple[list, dict, str]:
    """Newest docket entries after the cursor. Returns (evidence list, new cursor, note)."""
    from src.discover.courts.courtlistener import API
    from src.discover.courts.sale_check import entry_evidence
    last = (cursor or {}).get("last_entry")
    url, params, found, top, note = f"{API}/docket-entries/", {"docket": docket_id, "order_by": "-entry_number"}, [], None, ""
    while url:
        if budget.cl_left() <= 0:
            budget.queue("courtlistener_docket", docket_id, "CourtListener rolling budget used up: next run")
            note = "queued: budget"
            break
        r = client.get(url, params=params, use_cache=False)
        if r.status_code != 200:
            note = f"HTTP {r.status_code}"
            break
        data = r.json()
        entries = data.get("results") or []
        fresh = new_entries(entries, last)
        for e in entries:
            top = max(top or 0, e.get("entry_number") or 0)
        for e in fresh:
            ev = entry_evidence(e, docket_url)
            if ev:
                found.append(ev)
        # keep paging only while the whole page was new AND an earlier cursor exists (a gap since the last run)
        if last is None or len(fresh) < len(entries) or not data.get("next"):
            break
        url, params = data.get("next"), None
    return found, advance(cursor, last_entry=top, checked=date.today().isoformat()), note


def read_epiq(client, url: str) -> tuple[list, str]:
    """Epiq case pages are JavaScript apps: readable text is used if present, else needs_human_click."""
    from src.common.http import SourceBlocked
    from src.discover.courts.sale_check import Evidence, _visible, classify_stage, extract_buyer, sentences
    try:
        r = client.get(url, use_cache=False)
    except SourceBlocked as exc:
        return [], f"needs_human_click: {exc}"
    if r.status_code != 200:
        return [], f"needs_human_click: HTTP {r.status_code}"
    text = _visible(r.text)
    if len(text) < 400 or re.search(r"enable javascript|captcha", text, re.I):
        return [], "needs_human_click: JavaScript-only page (no docket text without a browser)"
    out = []
    for s in sentences(text):
        st = classify_stage(s)
        if st:
            out.append(Evidence(st, s[:480], url, extract_buyer(s), None))   # no per-sentence date: undated
    return out, f"read ({len(text)} chars)"


def follow_through(conn, budget: Budget, cursors: dict, proposed: dict, log=print) -> tuple[list[Change], list, dict]:
    from src.common.config import MissingSetting
    from src.common.http import PoliteClient, RateLimited, SourceBlocked
    from src.discover.courts import courtlistener as clmod
    targets = follow_targets(conn)
    ovs = override_map(conn)
    log(f"B. follow-through: {len(targets)} leads in a sale / sale approved "
        f"({sum(1 for t in targets if t['stage'] == 'sale' and not set(t['flags'] or []) & {'sale_approved'})} in a sale, "
        f"{sum(1 for t in targets if 'sale_approved' in (t['flags'] or []))} approved)")
    human, stats = [], {"targets": len(targets), "dockets": 0, "dockets_read": 0, "entries_with_sale_step": 0,
                        "epiq_pages": 0, "findings": 0}
    findings: list[Finding] = []
    # one read per docket, shared by every lead that cites it
    docket_leads: dict[str, list] = {}
    for t in targets:
        for d, (fid, url) in t["dockets"].items():
            docket_leads.setdefault(d, []).append((t, fid, url))
        for u in t["manual"]:
            human.append({"lead_id": t["lead_id"], "property": t["property"], "url": u, "why": "Kroll stays manual"})
        for u in t["stretto"]:
            human.append({"lead_id": t["lead_id"], "property": t["property"], "url": u, "why": SKIPPED["stretto"]})
    stats["dockets"] = len(docket_leads)
    try:
        cl = clmod.make_client(ledger=budget.ledger)      # every call logged; caps checked before each request
    except MissingSetting:
        cl = None
        log("   CourtListener: COURTLISTENER_TOKEN not set; dockets queued")
    if cl:
        with cl:
            order = sorted(docket_leads.items(), key=lambda kv: -max(u[0]["score"] or 0 for u in kv[1]))
            for i, (d, uses) in enumerate(order):
                key = f"courtlistener|docket:{d}"
                try:
                    evs, newc, note = read_docket(cl, d, uses[0][2], (cursors.get(key) or {}).get("cursor"), budget)
                except (SourceBlocked, clmod.RequestCapReached, RateLimited) as exc:
                    # stop CourtListener for tonight; this docket and every one after it wait for the next run
                    for rest, _ in order[i:]:
                        budget.queue("courtlistener_docket", rest, f"{type(exc).__name__}: next run")
                    stats["stopped"] = f"{type(exc).__name__}: {exc}"
                    log(f"   CourtListener stopped ({type(exc).__name__}): {len(order) - i} dockets queued for the next run")
                    break
                if note.startswith("queued"):
                    continue
                stats["dockets_read"] += 1
                proposed[key] = {"cursor": newc, "queue": []}
                stats["entries_with_sale_step"] += len(evs)
                for t, fid, _ in uses:
                    for ev in evs:
                        findings.append(Finding(t["lead_id"], ev.stage, ev.url, ev.when.isoformat() if ev.when else None,
                                                ev.buyer, "courtlistener", fid, ev.text))
    else:
        for d in docket_leads:
            budget.queue("courtlistener_docket", d, "no token")
    epiq_urls = sorted({u for t in targets for u in t["epiq"]})
    if epiq_urls:
        with PoliteClient("epiq") as ep:
            for u in epiq_urls:
                evs, note = read_epiq(ep, u)
                stats["epiq_pages"] += 1
                users = [t for t in targets if u in t["epiq"]]
                if note.startswith("needs_human_click"):
                    human += [{"lead_id": t["lead_id"], "property": t["property"], "url": u, "why": note[19:]}
                              for t in users]
                for t in users:
                    findings += [Finding(t["lead_id"], ev.stage, ev.url, None, ev.buyer, "epiq", None, ev.text)
                                 for ev in evs]
    stats["findings"] = len(findings)
    stats["finding_list"] = [{"property": next((t["property"] for t in targets if t["lead_id"] == f.lead_id), ""),
                              "step": f.stage_text, "date": f.when, "buyer": f.buyer, "url": f.source_url}
                             for f in findings]
    # decide per lead: the furthest step wins, ties go to the newest dated source
    by_lead = {t["lead_id"]: t for t in targets}
    changes = []
    for lid in sorted({f.lead_id for f in findings}):
        t = by_lead[lid]
        approved = "sale_approved" in (t["flags"] or [])
        lead = {"property": t["property"], "stage": "done" if approved else t["stage"], "buyer": t["buyer"],
                "buyer_status": t["buyer_status"], "sale_status": {"status": "approved"} if approved else None}
        from src.discover.courts.sale_check import STAGE_RANK
        best = max((f for f in findings if f.lead_id == lid),
                   key=lambda f: (STAGE_RANK.get(f.stage_text, 0), f.when is not None, f.when or ""))
        c = decide(lead, best, ovs.get(t["facility_id"]))
        if c:
            changes.append(c)
    return changes, human, stats


# ── discover (A) ────────────────────────────────────────────────────────────────────────────────────────────────
def gnews_allowed() -> tuple[bool, str]:
    from src.common.http import PoliteClient
    from src.discover.news.loader import GNEWS_QUERIES, gnews_url
    with PoliteClient(GNEWS, use_cache=False) as c:
        ok = c.allowed(gnews_url(GNEWS_QUERIES[0]))
    return ok, "robots.txt allows /rss/search" if ok else "robots.txt disallows /rss/search: DROPPED (not worked around)"


def discover_news(conn, budget: Budget, cursors: dict, proposed: dict, log=print) -> tuple[list, dict]:
    from src.common.http import PoliteClient, SourceBlocked
    from src.discover.news.loader import fetch_fulltext, parse_feed
    from src.extract import client as xcl
    from src.extract.prefilter import passes
    from src.extract.run import estimate_usd
    stats, fresh = {}, []
    seen_urls, seen_titles = set(), set()
    since = (datetime.now(timezone.utc) - timedelta(days=FIRST_RUN_LOOKBACK_DAYS)).isoformat()
    for sid, url in NEWS_FEEDS.items():
        key = f"{sid}|feed"
        cur = ((cursors.get(key) or {}).get("cursor") or {}).get("published_after") or since
        with PoliteClient(sid) as c:
            try:
                r = c.get(url, use_cache=False)
            except SourceBlocked as exc:
                stats[sid] = {"blocked": str(exc)}
                continue
            if r.status_code != 200:
                stats[sid] = {"error": f"HTTP {r.status_code}"}
                continue
            items = parse_feed(r.content, sid)
            new = [d for d in items if newer_than(d.published_at, cur)]
            gap = bool(items) and len(new) == len(items) and cur != since
            latest = max((d.published_at for d in items if d.published_at), default=None)
            proposed[key] = {"cursor": advance((cursors.get(key) or {}).get("cursor"),
                                               published_after=latest.isoformat() if latest else None), "queue": []}
            stats[sid] = {"in_feed": len(items), "after_cursor": len(new), "cursor": cur,
                          **({"note": "every feed item is newer than the cursor: older items may be missed"} if gap else {})}
            fresh += new
    fresh, dup_feeds = dedupe_new(fresh, seen_urls, seen_titles)
    known = {r["source_url"] for r in conn.execute("select source_url from raw.document where source_url = any(%s)",
                                                   ([d.source_url for d in fresh],)).fetchall()} if fresh else set()
    new_docs = [d for d in fresh if d.source_url not in known]
    # full text for new summary-only items (polite, robots obeyed), then the cheap keyword pre-filter
    pages = 0
    for d in new_docs:
        if d.kind != "summary":
            continue
        if pages >= FULLTEXT_CAP:
            budget.queue("fulltext", d.source_url, f"full-text cap {FULLTEXT_CAP}")
            continue
        with PoliteClient(d.source_id) as c:
            fetch_fulltext(c, d)
        pages += 1
    send = [d for d in new_docs if passes(d.source_id, d.title, d.body_text)[0]]
    as_rows = [{"title": d.title, "body_text": d.body_text} for d in send]
    model, fit, total = xcl.PRODUCTION_MODEL, [], 0.0
    for d, row in zip(send, as_rows):
        est, _ = estimate_usd(model, [row], False)
        if budget.llm_estimated + est > budget.llm_cap:
            budget.queue("extraction", d.source_url, f"LLM budget ${budget.llm_cap:.2f}")
            continue
        budget.llm_estimated += est
        fit.append(d)
    roundups = [d.source_url for d in send if is_roundup(d.source_url, d.title, d.body_text)]
    stats["dedupe"] = {"duplicates_across_feeds": dup_feeds, "already_in_database": len(fresh) - len(new_docs),
                       "new_documents": len(new_docs), "pass_prefilter": len(send), "fit_llm_budget": len(fit),
                       "roundup_articles": len(roundups), "llm_estimate_usd": round(budget.llm_estimated, 4),
                       "full_text_fetched": pages}
    return fit, stats


def discover_courtlistener(conn, budget: Budget, cursors: dict, proposed: dict, log=print) -> tuple[list, dict]:
    """New bankruptcy dockets filed since the cursor (minus an overlap), one search page per query."""
    from src.common.config import MissingSetting
    from src.common.http import RateLimited, SourceBlocked
    from src.discover.courts import courtlistener as clmod
    key = "courtlistener|search"
    cur = ((cursors.get(key) or {}).get("cursor") or {}).get("filed_after")
    start = (date.fromisoformat(cur) - timedelta(days=OVERLAP_DAYS)) if cur else \
        date.today() - timedelta(days=FIRST_RUN_LOOKBACK_DAYS)
    if any(q["why"].startswith(("RateLimited", "SourceBlocked")) for q in budget.queued):
        budget.queue("courtlistener_search", start.isoformat(), "CourtListener stopped earlier in this run: next run")
        return [], {"skipped": "CourtListener stopped earlier in this run (rate limit / blocked)"}
    left = budget.cl_left()
    need = 1 + len(clmod.QUERIES)                       # court ids + one page per query
    if left < need:
        budget.queue("courtlistener_search", start.isoformat(), f"needs {need} requests, {left} left")
        return [], {"skipped": f"budget: {left} requests left, search needs {need}"}
    try:
        client = clmod.make_client(ledger=budget.ledger)
    except MissingSetting:
        budget.queue("courtlistener_search", start.isoformat(), "no token")
        return [], {"skipped": "COURTLISTENER_TOKEN not set"}
    cases, notes, before = {}, [], budget.cl_used
    with client:
        try:
            courts = " ".join(clmod.bankruptcy_court_ids(client))
            for q in clmod.QUERIES:
                r = client.get(f"{clmod.API}/search/", params={"type": "r", "q": f'"{q}"', "filed_after": start.isoformat(),
                                                             "order_by": "dateFiled desc", "court": courts},
                               use_cache=False)
                if r.status_code != 200:
                    notes.append(f"{q}: HTTP {r.status_code}")
                    continue
                data = r.json()
                for hit in data.get("results") or []:
                    case = clmod.docket_to_case(hit) if clmod.is_bankruptcy(hit) else None
                    if case and case.courtlistener_id:
                        cases.setdefault(case.courtlistener_id, case)
                if data.get("next"):
                    budget.queue("courtlistener_search", q, "more than one page of new dockets")
        except (SourceBlocked, clmod.RequestCapReached, RateLimited) as exc:
            notes.append(f"{type(exc).__name__}: {exc}")
            budget.queue("courtlistener_search", start.isoformat(), f"{type(exc).__name__}: next run")
            return [], {"filed_after": start.isoformat(), "stopped": notes[-1], "requests": budget.cl_used - before}
    known = {str(r["courtlistener_id"]) for r in conn.execute(
        "select courtlistener_id from stg.court_case where courtlistener_id = any(%s)", (list(cases),)).fetchall()} \
        if cases else set()
    new = [c for k, c in cases.items() if k not in known]
    proposed[key] = {"cursor": advance((cursors.get(key) or {}).get("cursor"), filed_after=date.today().isoformat()),
                     "queue": []}
    return new, {"filed_after": start.isoformat(), "dockets_found": len(cases), "new_dockets": len(new),
                 "requests": budget.cl_used - before, **({"notes": notes} if notes else {})}


# ── roundup rule on the shown buyers ────────────────────────────────────────────────────────────────────────────
def roundup_buyers(conn, lead_ids: list[str]) -> dict[str, str]:
    """{lead_id: article url} for shown buyers whose public source is a multi-property roundup article."""
    rows = conn.execute(
        "select v.lead_id::text lid, v.buyer_source_url u, d.title, d.body_text from app.v_lead_list v "
        "left join raw.document d on d.source_url = v.buyer_source_url "
        "where v.lead_id = any(%s::uuid[]) and v.buyer_source_url is not null and v.buyer is not null "
        "and v.buyer <> 'NOT FOUND'", (lead_ids,)).fetchall()
    return {r["lid"]: r["u"] for r in rows if is_roundup(r["u"], r["title"], r["body_text"])}


# ── gates ───────────────────────────────────────────────────────────────────────────────────────────────────────
def run_tests() -> tuple[bool, str]:
    r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"], cwd=PROJECT_ROOT,
                       capture_output=True, text=True, timeout=900)
    tail = (r.stdout.strip().splitlines() or ["no output"])[-1]
    return r.returncode == 0, tail


def gates(cand: dict, prev: dict | None, run_tests_too: bool = True) -> dict:
    out = {}
    out["tests"] = run_tests() if run_tests_too else (True, "skipped (--skip-tests)")
    from src.pipeline import preflight
    pf = preflight.problems()
    out["preflight"] = (not pf, "exclusion list, term list and Render key present" if not pf else "; ".join(pf))
    out["leak_scan"] = leak_scan(cand, str(preflight.terms_path()))
    out["source_links"] = links_ok(cand)
    out["lead_count"] = lead_count_ok((prev or {}).get("lead_count"), len(cand.get("items") or []))
    size = packed_size(cand)
    out["snapshot_size"] = (size < SNAPSHOT_MAX_BYTES, f"{size:,} bytes (limit {SNAPSHOT_MAX_BYTES:,})")
    from src.web.snapshot import funnel_problems
    fp = funnel_problems(cand)
    out["consistency"] = (not fp, "counts agree" if not fp else "; ".join(fp))
    return {k: {"pass": bool(v[0]), "detail": v[1]} for k, v in out.items()}


# ── portability ─────────────────────────────────────────────────────────────────────────────────────────────────
def portability() -> list[str]:
    """Files outside Supabase + the network that a run still reads (blocking in a fresh cloud checkout)."""
    from src.pipeline import preflight
    out = [f"pre-flight: {p}" for p in preflight.problems()]
    out += [
        "exclusion list, term list and Render key: read from the environment (EXCLUDE_FACILITIES, PRIVATE_TERMS_PATH, "
        "RENDER_API_KEY) or the laptop files; a publish run stops before any work when one is missing or empty",
        "output/INTERNAL_watch_*.csv: the snapshot's Explore (watch list) reads the latest CSV; a cloud run must run "
        "`python -m src.score` export in the same job first",
        "output/site_snapshot.uploaded.b64: baseline for the first live run only (afterwards raw.refresh_runs.state)",
        "output/changelog_baseline_state.json: the Nov 11 list the FIRST change log is compared with (laptop only; "
        "without it the first check compares with the last published list)",
    ]
    return out


# ── main ────────────────────────────────────────────────────────────────────────────────────────────────────────
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m src.pipeline.daily3", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--live", action="store_true")
    mode.add_argument("--record-publish", action="store_true",
                      help="after a publish made by hand from the last dry run: store that check in the run log, so "
                           "the next run counts on from it and compares with what is live")
    ap.add_argument("--publish", action="store_true", help="live only: upload when every gate passes")
    ap.add_argument("--skip-tests", action="store_true", help="debug only: the tests gate then reports 'skipped'")
    ap.add_argument("--roundup-unverified", action="store_true",
                    help="apply the roundup rule (buyer from a roundup article -> Unverified, leaves List A). "
                         "OFF by default: it changes the published counts (Nov 13 dry run: 3 shown buyers affected)")
    ap.add_argument("--publish-changelog", action="store_true",
                    help="include the change log in the published snapshot. OFF by default: the change log is built "
                         "and written to the run output, but the site does not show it")
    a = ap.parse_args(argv)
    if a.publish and not a.live:
        ap.error("--publish needs --live")
    if a.record_publish:
        return record_publish()
    from src.pipeline import changelog as cl
    from src.pipeline import preflight
    pf = preflight.check()
    print("Pre-flight")
    for r in pf:
        print(f"   {'PASS' if r['pass'] else 'FAIL'}  {r['name']:15} {r['detail']}")
    if a.publish and not all(r["pass"] for r in pf):
        print("Publish: NO (blocked by pre-flight; the run stops before any work)")
        return 2
    from src.common.db import connect
    started = datetime.now(timezone.utc)
    run_id = str(uuid.uuid4())
    budget = Budget()
    log = print
    log(f"daily3 {'DRY RUN' if a.dry_run else 'LIVE'} {started:%Y-%m-%d %H:%M} UTC  run {run_id[:8]}")
    with connect() as conn:
        if a.dry_run:
            conn.execute("set session characteristics as transaction read only")
            conn.commit()
            log("   database: read-only session (no writes possible)")
        store = Store(conn, a.dry_run)
        cursors, cur_src = store.load_cursors()
        prev, prev_src = store.last_state()
        from src.discover.courts.courtlistener import CallLedger
        hist, hist_src = store.cl_history()
        budget.ledger = CallLedger("daily3", run_id, hist, CL_HOUR_CAP, CL_DAY_CAP)
        cl_before = {"hour": budget.ledger.used(1), "day": budget.ledger.used(24)}
        log(f"   CourtListener before this run: {cl_before['hour']}/{CL_HOUR_CAP} in the rolling hour, "
            f"{cl_before['day']}/{CL_DAY_CAP} in 24 h (from {json.dumps(hist_src)}); "
            f"this run may use {budget.cl_left()}")
        log(f"   cursors from: {cur_src}; baseline: {prev_src} ({(prev or {}).get('label', '-')})")
        for sid, why in SKIPPED.items():
            log(f"   skip {sid}: {why}")
        ok, why = gnews_allowed()
        log(f"   {GNEWS} live robots check: {why}")
        proposed: dict = {}

        changes, human, ft = follow_through(conn, budget, cursors, proposed, log)
        log(f"   dockets {ft['dockets']} (read {ft['dockets_read']}), Epiq pages {ft['epiq_pages']}, "
            f"sale steps in new entries {ft['entries_with_sale_step']}, findings {ft['findings']}")
        for f in ft["finding_list"]:
            log(f"   finding  {f['property'][:44]:44} {f['step']} ({f['date'] or 'undated'}) "
                f"buyer={f['buyer'] or '-'} {f['url']}")
        if ft["findings"] and not changes:
            log("   -> no stage change: no finding is a court step beyond the lead's current stage")
        for c in changes:
            log(f"   {c.kind:14} {c.property[:44]:44} {c.old_stage} -> {c.new_stage or '-'} "
                f"{c.source_url} {c.note}".rstrip())
        for h in human:
            log(f"   needs_human_click  {h['property'][:44]:44} {h['url']}  ({h['why']})")

        log("A. discover")
        new_cases, cl_stats = discover_courtlistener(conn, budget, cursors, proposed, log)
        log(f"   courtlistener: {json.dumps(cl_stats)}")
        for c in new_cases[:10]:
            log(f"     new docket {c.court} {c.docket_no} {c.case_name} {c.source_url}")
        docs, news_stats = discover_news(conn, budget, cursors, proposed, log)
        for k, v in news_stats.items():
            log(f"   {k}: {json.dumps(v, default=str)}")

        if a.dry_run:
            # candidate = the list as the current code builds it from the database (read only), written to the
            # dry-run folder only; tonight's findings are then applied in memory
            from src.web import snapshot as websnap
            DRYRUN_DIR.mkdir(parents=True, exist_ok=True)
            websnap.main(["--out", str(DRYRUN_DIR / "candidate_snapshot.json")])
            base = json.loads((DRYRUN_DIR / "candidate_snapshot.json").read_text())
            log(f"   LLM: dry run makes no API calls; {len(docs)} documents would be sent "
                f"(estimate ${budget.llm_estimated:.4f} of ${budget.llm_cap:.2f})")
        else:
            base = live_rebuild(conn, docs, new_cases, changes, budget, log, a.roundup_unverified)
        # roundup rule (opt-in): snapshot.main applies it in a live build; the dry run patches the published base.
        # Off: only reported, never applied.
        unverified = roundup_buyers(conn, [r["lead_id"] for r in base.get("items") or []])
        cand = apply_changes(base, changes, unverified if a.roundup_unverified else {}) if a.dry_run else base
        cstate = snapshot_state(cand)
        # confidential exclusions are taken out of every list tonight is compared with (the hand-over baseline too):
        # such a home never shows as a change and is never counted as removed
        hidden = store.excluded_lead_ids()
        prev = cl.without(prev, hidden)
        d = diff_states(prev, cstate)
        # change log (public set only; a lead that left the list is a count, never a row)
        history, hist_src = store.changelog_history()
        run_no = cl.next_number(history)
        cl_prev, cl_prev_src = store.changelog_baseline(history, prev)
        cl_prev = cl.without(cl_prev, hidden)
        handover = cl.without(store.handover_state()[0], hidden)
        base_n = (handover or {}).get("lead_count")
        day = started.astimezone(cl.ET).date().isoformat()
        cl_rows, cl_removed = cl.rows_between(cl_prev, cand, day, run_no)
        checked = {"dockets_read": ft.get("dockets_read"), "epiq_pages": ft.get("epiq_pages"),
                   "sale_steps_found": ft.get("findings"), "new_dockets": len(new_cases), "new_articles": len(docs),
                   "courtlistener_calls": budget.cl_used}
        n_leads = len(cand.get("items") or [])
        cand["changelog"] = cl.build(history, cl.run_entry(run_no, started, "pending", cl_rows, cl_removed, {},
                                                           checked, n_leads), cl_rows, cand, started, base_n)
        g = gates(cand, prev, not a.skip_tests)
        # auto-publish rule: the top-20 order must be exactly the last published one; otherwise HOLD for a person
        same_top = not prev or list(prev.get("top20") or []) == list(cstate["top20"])
        g["top20_order"] = {"pass": bool(same_top), "detail": "unchanged" if same_top else
                            "changed: HOLD, not published; a person reviews and publishes by hand"}
        all_pass = all(v["pass"] for v in g.values())
        applied = [c for c in changes if c.kind == "stage_changed"]
        # rule: a new top-20 lead or a stage change goes out unreviewed; so does a changed top-20 buyer or a
        # superseded override (conservative additions)
        reviewed = not (d["top20_entrants"] or d["stage_changed"] or d["top20_buyer_changed"]
                        or any(c.supersedes_override for c in applied))
        publish = a.live and a.publish and all_pass
        status = "blocked" if not all_pass else ("published" if publish else
                                                 "no_change" if not (d["new"] or d["dropped"] or d["stage_changed"]
                                                                     or d["buyer_changed"]) else
                                                 "dry_run" if a.dry_run else "ready")
        entry = cl.run_entry(run_no, started, "published" if publish else "passed" if all_pass else "blocked",
                             cl_rows, cl_removed, g, checked, n_leads)
        cand["changelog"] = cl.build(history, entry, cl_rows, cand, started, base_n)
        for name, res in (("leak_scan", leak_scan(cand, str(preflight.terms_path()))),      # re-check with the final block
                          ("snapshot_size", (packed_size(cand) < SNAPSHOT_MAX_BYTES, f"{packed_size(cand):,} bytes (limit {SNAPSHOT_MAX_BYTES:,})"))):
            g[name] = {"pass": bool(res[0]), "detail": res[1]}
        if not all(v["pass"] for v in g.values()):
            all_pass, publish, status = False, False, "blocked"
        cl_file = (DRYRUN_DIR if a.dry_run else OUTPUT_DIR) / "daily3_changelog.json"
        cl_file.parent.mkdir(parents=True, exist_ok=True)
        cl_file.write_text(json.dumps(cand["changelog"], ensure_ascii=False, indent=1, default=str))
        if a.dry_run:                                   # local preview file: the candidate with its change log
            from src.web.snapshot import packed
            (DRYRUN_DIR / "candidate_snapshot.b64").write_bytes(packed(cand))
        if publish:
            from src.web import publish as pub
            pub_rc = pub.main(["--confirm"] + (["--roundup-unverified"] if a.roundup_unverified else [])
                              + (["--changelog", str(cl_file)] if a.publish_changelog else []))
            status = "published" if pub_rc == 0 else "failed"
        run = {"run_id": run_id, "mode": "dry_run" if a.dry_run else "live", "status": status,
               "started_at": started.isoformat(), "finished_at": datetime.now(timezone.utc).isoformat(),
               "new_leads": len(d["new"]), "stage_changed": len(d["stage_changed"]), "dropped": len(d["dropped"]),
               "lead_count": cstate["lead_count"], "gates": g,
               "budget": {"courtlistener_requests": budget.cl_used, "courtlistener_hour_cap": CL_HOUR_CAP,
                          "courtlistener_day_cap": CL_DAY_CAP, "courtlistener_used_before": cl_before,
                          "courtlistener_used_after": {"hour": budget.ledger.used(1), "day": budget.ledger.used(24)},
                          "courtlistener_by_endpoint": _by_endpoint(budget.ledger.calls),
                          "courtlistener_calls": budget.ledger.rows(),
                          "llm_usd_spent": round(budget.llm_used, 4), "llm_usd_estimated": round(budget.llm_estimated, 4),
                          "llm_cap_usd": budget.llm_cap},
               "changes": [asdict(c) for c in changes], "queued": budget.queued, "reviewed": reviewed,
               "published": status == "published", "state": cstate,
               "changelog": cl.history_after(history, entry, cl_rows, started),
               "note": json.dumps({"needs_human_click": human, "diff": d, "roundup_buyers_unverified": unverified,
                                   "follow_through": ft, "courtlistener_search": cl_stats, "news": news_stats,
                                   "portability_blockers": portability()}, default=str)}
        cur_out = {k: v for k, v in cursors.items()}
        written = store.save(cur_out, proposed, run, budget.ledger.rows() if a.dry_run else budget.ledger.calls)

    log("Gates")
    for k, v in g.items():
        log(f"   {'PASS' if v['pass'] else 'FAIL'}  {k:14} {v['detail']}")
    log(f"Diff vs {d['baseline'] or 'nothing'}: new {len(d['new'])}, dropped {len(d['dropped'])}, "
        f"stage changed {len(d['stage_changed'])}, buyer changed {len(d['buyer_changed'])} "
        f"(top 20: {len(d['top20_buyer_changed'])}), top-20 entrants {len(d['top20_entrants'])}")
    log(f"Roundup rule ({'ON' if a.roundup_unverified else 'OFF, report only'}): {len(unverified)} shown buyers come "
        f"from a roundup article" + (" -> Unverified" if a.roundup_unverified else "; nothing changed"))
    log(f"Budget: CourtListener {budget.cl_used} calls this run, all logged {json.dumps(_by_endpoint(budget.ledger.calls))}; "
        f"rolling hour {budget.ledger.used(1)}/{CL_HOUR_CAP}, 24 h {budget.ledger.used(24)}/{CL_DAY_CAP}; "
        f"LLM ${budget.llm_used:.2f} spent, "
        f"${budget.llm_estimated:.4f} estimated (cap ${budget.llm_cap:.2f}); queued {len(budget.queued)}")
    for q in budget.queued[:10]:
        log(f"   queued {q['kind']}: {q['key']} ({q['why']})")
    c = cand["changelog"]
    log(f"Change log: {c['updated_et']} · Daily check #{c['check_no']}; leads {c['leads']} ({c['leads_delta']:+d} vs "
        f"{c['baseline']['label']}); buyers {c['buyers_named']} named, {c['buyers_inferred']} inferred; this check: "
        f"{entry['stages_changed']} stages changed, {entry['buyers_changed']} buyer changes, {entry['new']} new, "
        f"{entry['removed']} removed; rows shown {len(c['rows'])}; compared with {cl_prev_src}; history {hist_src}; "
        f"{'INCLUDED in the publish' if a.publish_changelog else 'not published (needs --publish-changelog)'}")
    log(f"Publish: {'yes' if status == 'published' else 'NO'} ({status}); reviewed={str(reviewed).lower()}")
    log("Portability (files a run still needs besides Supabase + network):")
    for p in portability():
        log(f"   - {p}")
    log("Written: " + ", ".join(written))
    return 0 if all_pass or a.dry_run else 1


def record_publish() -> int:
    """Store the last dry run as the published check (only when the live upload carries exactly that check)."""
    from src.common.db import connect
    from src.web.server import load_snapshot
    p = DRYRUN_DIR / "run_log.json"
    if not p.exists() or not PUBLISHED.exists():
        print("record-publish: needs the last dry run's run_log.json and the uploaded snapshot")
        return 2
    run = json.loads(p.read_text())
    live = (load_snapshot(str(PUBLISHED)).get("changelog") or {})
    mine = (run.get("changelog") or [{}])[-1]
    if not live or live.get("check_no") != mine.get("no") or live.get("updated_at") != mine.get("at"):
        print("record-publish: the uploaded snapshot does not carry the last dry run's change log; nothing stored")
        return 2
    run.update(mode="live", status="published", published=True,
               note=json.dumps({"published": "by hand from the laptop (python -m src.web.publish --confirm --changelog)"}))
    with connect() as conn:
        store = Store(conn, False)
        if not store.table_ok:
            print("record-publish: raw.refresh_runs is missing (apply the daily3 migration first)")
            return 2
        if conn.execute("select 1 from raw.refresh_runs where run_id = %s", (run["run_id"],)).fetchone():
            print("record-publish: already stored")
            return 0
        store.insert_run(run)
    print(f"record-publish: Daily check #{mine.get('no')} stored as published ({run['lead_count']} leads)")
    return 0


def _by_endpoint(calls: list[dict]) -> dict:
    out: dict = {}
    for c in calls:
        k = f"{c['endpoint']} {c['status']}"
        out[k] = out.get(k, 0) + 1
    return out


def live_rebuild(conn, docs, new_cases, changes, budget: Budget, log=print, roundup_unverified: bool = False) -> dict:
    """LIVE ONLY (not run in the dry run): store tonight's documents and findings, run the existing pipeline steps,
    rebuild the snapshot. Every step is an existing, re-runnable command."""
    from src.discover.courts import loader
    from src.discover.courts.base import SourceResult as CourtResult
    from src.discover.courts.sale_check import Evidence, save_check
    from src.discover.news.loader import SourceResult as NewsResult
    from src.discover.news.store import store_source
    from src.extract import client as xcl
    from src.extract import run as xrun
    from src.extract import split
    # 1. court findings -> core.sale_check (existing table; never downgrades); stage rules then apply in scoring
    for c in changes:
        if c.kind == "stage_changed" and c.family_id is not None:
            stage = "sale closed" if c.sale_status["status"] == "closed" else "sale order"
            save_check(conn, c.family_id, "courtlistener", found=True,
                       ev=Evidence(stage, c.note or stage, c.source_url, c.buyer, date.fromisoformat(c.sale_status["date"])),
                       source_url=c.source_url, items=None, requests=0, note="daily3 follow-through")
        elif c.kind == "stage_changed":
            budget.queue("finding_without_family", c.source_url, "no case family to attach to: needs a human (court_facts.yaml)")
    conn.commit()
    # 2. new dockets and articles -> raw tables (existing loaders, upsert / insert-or-skip)
    if new_cases:
        loader.load_source(conn, "courtlistener", CourtResult("courtlistener", cases=new_cases))
    by_src: dict = {}
    for d in docs:
        by_src.setdefault(d.source_id, []).append(d)
    for sid, ds in by_src.items():
        store_source(sid, NewsResult(sid, docs=ds))
    split.run(conn)
    urls = [d.source_url for d in docs]
    ids = [r["id"] for r in conn.execute("select id from raw.document where source_url = any(%s) "
                                         "or split_part(source_url, '#deal-', 1) = any(%s)", (urls, urls)).fetchall()]
    # 3. extraction of tonight's documents only, inside what is left of the LLM budget
    st = xrun.run(conn, xcl.PRODUCTION_MODEL, doc_ids=ids, budget=budget.llm_cap - budget.llm_used, log=log)
    budget.llm_used += float(st.get("api_usd") or 0)
    # 4. existing chain: case families -> enrich (courts, extraction matches) -> scoring (+ diversity) -> snapshot
    from src.discover.courts import families
    from src.enrich import cli as enrich
    from src.score import cli as score
    from src.web import snapshot
    families.main([])
    enrich.main(["--step", "courts", "--step", "extraction", "--step", "dedupe", "--step", "review"])
    score.main([])
    out = OUTPUT_DIR / "site_snapshot.json"
    snapshot.main(["--out", str(out)] + (["--roundup-unverified"] if roundup_unverified else []))
    return json.loads(out.read_text())


if __name__ == "__main__":
    sys.exit(main())
