"""CourtListener API v4: bankruptcy dockets mentioning senior-housing terms + sale-related docket entries.

Auth: header `Authorization: Token <COURTLISTENER_TOKEN>` is set on the HTTP client only. Cache files are keyed by URL
and never contain request headers, so the token is never written to disk. The token is never printed.
"""
from __future__ import annotations

import re
from urllib.parse import urljoin

from src.common.config import MissingSetting, env
from src.common.http import PoliteClient, RateLimited

from .base import (SALE_TEXT, WINDOW_MONTHS, CaseRecord, DocRecord, SourceResult,
                   has_buyer_evidence, months_ago, parse_date, to_datetime)

SITE = "https://www.courtlistener.com"
API = f"{SITE}/api/rest/v4"
QUERIES = ["assisted living", "senior living", "memory care", "nursing home", "skilled nursing",
           "continuing care", "life plan community"]
MAX_ENTRY_PAGES = 3          # docket-entry pages (20 entries each) scanned per docket
TOKEN_MISSING = "COURTLISTENER_TOKEN not set, skipping"


MAX_REQUESTS = 1500          # whole-run cap: CourtListener is a free nonprofit service
MIN_INTERVAL = 5.0           # seconds between CourtListener calls (Nov 13, 2026: at 1/s the per-minute limit refused
                             # 10 of 32 calls in the dry run). The rolling hour / 24 h caps live in CallLedger.


class RequestCapReached(Exception):
    pass


class CallLedger:
    """Every CourtListener HTTP attempt made through a client (retries included), recorded by httpx event hooks, plus
    optional rolling-window caps that count earlier calls too (`history`: datetimes from other runs / jobs).

    A cap is checked BEFORE each request; reaching it raises RequestCapReached (the request is not sent).
    Nothing here is persisted: callers save `calls` (daily3 run log, raw.courtlistener_calls when it exists).
    """

    def __init__(self, job: str, run_id: str | None = None, history=(), hour_cap: int | None = None,
                 day_cap: int | None = None, now=None):
        from datetime import datetime, timezone
        self.job, self.run_id = job, run_id
        self.history = sorted(history)
        self.hour_cap, self.day_cap = hour_cap, day_cap
        self._now = now or (lambda: datetime.now(timezone.utc))
        self.calls: list[dict] = []

    def used(self, hours: float) -> int:
        """Calls in the rolling window ending now: earlier ones (history) and this run's."""
        from datetime import timedelta
        since = self._now() - timedelta(hours=hours)
        return sum(1 for t in self.history if t > since) + sum(1 for c in self.calls if c["at"] > since)

    def remaining(self) -> int:
        caps = [cap - self.used(h) for cap, h in ((self.hour_cap, 1), (self.day_cap, 24)) if cap is not None]
        return max(0, min(caps)) if caps else 10 ** 9

    def check(self) -> None:
        if self.hour_cap is not None and self.used(1) >= self.hour_cap:
            raise RequestCapReached(f"rolling-hour cap {self.hour_cap} reached ({self.used(1)} calls in the last hour)")
        if self.day_cap is not None and self.used(24) >= self.day_cap:
            raise RequestCapReached(f"rolling-24h cap {self.day_cap} reached ({self.used(24)} calls in 24 h)")

    def on_request(self, request) -> None:
        self.check()
        self.calls.append({"at": self._now(), "job": self.job, "run_id": self.run_id, "method": request.method,
                           "endpoint": request.url.path, "query": request.url.query.decode()[:300], "status": None})

    def on_response(self, response) -> None:
        for c in reversed(self.calls):
            if c["status"] is None and c["endpoint"] == response.request.url.path:
                c["status"] = response.status_code
                break

    def rows(self) -> list[dict]:
        """JSON-ready copy (no headers, so never the token)."""
        return [dict(c, at=c["at"].isoformat()) for c in self.calls]


class CourtListenerClient(PoliteClient):
    """PoliteClient for the token-authenticated REST API.

    USER DECISION (project owner): robots.txt governs the CourtListener website, not the token-authenticated
    REST API at /api/rest/v4/, so the robots check is skipped for that prefix ONLY. All other URLs keep it.
    Still honest User-Agent, >=1 s spacing, retry/backoff on 429, cache without the token, and 401/403 from
    the API raises SourceBlocked (source becomes manual).
    """
    requests_made = 0
    max_requests = MAX_REQUESTS

    def allowed(self, url: str) -> bool:
        if url.startswith(API + "/"):
            return True
        return super().allowed(url)

    def _fetch(self, url: str):
        if self.requests_made >= self.max_requests:
            raise RequestCapReached(f"request cap {self.max_requests} reached")
        self.requests_made += 1
        return super()._fetch(url)


def make_client(*, transport=None, ledger: CallLedger | None = None, **kw) -> CourtListenerClient:
    """Raises MissingSetting when the token is absent. With a ledger, every HTTP attempt is recorded (and capped)."""
    token = env("COURTLISTENER_TOKEN")
    kw.setdefault("min_interval", MIN_INTERVAL)          # every job: daily3, case-list downloader, sale-check
    c = CourtListenerClient("courtlistener", transport=transport,
                            headers={"Authorization": f"Token {token}", "Accept": "application/json"}, **kw)
    if ledger is not None:
        c.ledger = ledger
        c._client.event_hooks = {"request": [ledger.on_request], "response": [ledger.on_response]}
    return c


CALLS_TABLE = "raw.courtlistener_calls"


def save_calls(conn, ledger: CallLedger) -> int | None:
    """Best effort: write the ledger to raw.courtlistener_calls when that table exists (None = table missing)."""
    if not ledger.calls or not conn.execute("select to_regclass(%s) is not null ok", (CALLS_TABLE,)).fetchone()["ok"]:
        return None if ledger.calls else 0
    with conn.cursor() as cur:
        cur.executemany(f"insert into {CALLS_TABLE} (called_at, job, run_id, method, endpoint, query, status) "
                        "values (%(at)s, %(job)s, %(run_id)s, %(method)s, %(endpoint)s, %(query)s, %(status)s)",
                        ledger.calls)
    conn.commit()
    return len(ledger.calls)


def call_history(conn, hours: int = 24) -> tuple[list, dict]:
    """Datetimes of CourtListener calls in the last `hours`, from every record that exists: raw.courtlistener_calls
    (daily3, case-list downloader) and core.sale_check.requests_used (the sale-check job, one stamp per request)."""
    out, src = [], {}
    if conn.execute("select to_regclass(%s) is not null ok", (CALLS_TABLE,)).fetchone()["ok"]:
        rows = conn.execute(f"select called_at from {CALLS_TABLE} where called_at > now() - make_interval(hours => %s)",
                            (hours,)).fetchall()
        out += [r["called_at"] for r in rows]
        src[CALLS_TABLE] = len(rows)
    rows = conn.execute("select checked_at, requests_used from core.sale_check where channel = 'courtlistener' "
                        "and checked_at > now() - make_interval(hours => %s)", (hours,)).fetchall()
    n = sum(int(r["requests_used"] or 0) for r in rows)
    out += [r["checked_at"] for r in rows for _ in range(int(r["requests_used"] or 0))]
    src["core.sale_check"] = n
    return out, src


def is_bankruptcy(hit: dict) -> bool:
    """Search is already limited to bankruptcy courts; drop adversary proceedings (`X v. Y`), which are not the debtor's case."""
    if re.search(r"\bv[s]?\.?\s", hit.get("caseName") or "", re.I) and not hit.get("chapter"):
        return False
    return True


def bankruptcy_court_ids(client) -> list[str]:
    ids, url, params = [], f"{API}/courts/", {"jurisdiction": "FB", "fields": "id", "page_size": 100}
    while url:
        data = client.get(url, params=params).json()
        ids += [c["id"] for c in data.get("results", [])]
        url, params = data.get("next"), None
    return ids


def docket_to_case(hit: dict) -> CaseRecord | None:
    docket_no = (hit.get("docketNumber") or "").strip()
    path = hit.get("docket_absolute_url") or ""
    court = (hit.get("court_id") or "").strip()
    if not (docket_no and path and court):
        return None
    name = (hit.get("caseName") or "").strip() or None
    debtor = re.sub(r"^\s*in re:?\s+", "", name, flags=re.I) if name and re.match(r"\s*in re\b", name, re.I) else None
    chapter = str(hit["chapter"]).strip() if hit.get("chapter") else None
    return CaseRecord(
        "courtlistener", urljoin(SITE, path), court, docket_no, case_name=name, chapter=chapter,
        filed_date=parse_date(hit.get("dateFiled")), terminated_date=parse_date(hit.get("dateTerminated")),
        debtor_name=debtor, courtlistener_id=str(hit["docket_id"]) if hit.get("docket_id") is not None else None,
    )


def entry_to_doc(case: CaseRecord, entry: dict) -> DocRecord | None:
    """A docket entry (or search-hit recap document) -> raw.document if it concerns a sale process."""
    desc = (entry.get("description") or entry.get("short_description") or "").strip()
    docs = entry.get("recap_documents") or []
    texts = [desc]
    for d in docs:
        texts += [d.get("description") or "", d.get("short_description") or "", d.get("plain_text") or "", d.get("snippet") or ""]
    body = "\n".join(t for t in texts if t).strip()
    if not SALE_TEXT.search(body):
        return None
    num = entry.get("entry_number") or entry.get("document_number")
    url = None
    for d in docs:
        if d.get("absolute_url"):
            url = urljoin(SITE, d["absolute_url"])
            break
    if not url:
        if num is None:
            return None
        url = f"{case.source_url}#entry-{num}"
    title = f"{case.case_name or case.docket_no} - docket entry {num}" if num is not None else (case.case_name or case.docket_no)
    return DocRecord("courtlistener", url, title, body[:20000],
                     to_datetime(parse_date(entry.get("date_filed") or entry.get("entry_date_filed"))),
                     has_buyer_evidence(body))


def fetch_courtlistener(client, limit=None, *, today=None, search_only=False, on_cases=None, queries=None) -> SourceResult:
    """search_only: court-id lookup + searches only, zero docket-entry fetches. on_cases(list[CaseRecord]) is called
    after every search page with the cases first seen on it, so callers can persist as they go."""
    res = SourceResult("courtlistener")
    seen: dict[str, CaseRecord] = {}
    try:
        _fetch(client, res, limit, today, seen, search_only, on_cases, queries)
    except (RequestCapReached, RateLimited) as exc:      # whatever was found before the stop is kept
        res.note = f"stopped early: {exc}"
    res.cases = list(seen.values())
    res.note = (res.note + "; " if res.note else "") + f"requests={getattr(client, 'requests_made', '?')}"
    return res


def _fetch(client, res, limit, today, seen, search_only, on_cases, queries=None):
    courts = " ".join(bankruptcy_court_ids(client))
    after = months_ago(WINDOW_MONTHS, today).isoformat()
    doc_urls: set[str] = set()
    # queries: extra exact-phrase searches (e.g. a debtor name a tip or a news article gave us), run instead of QUERIES
    for q in (queries or QUERIES):
        url, params = f"{API}/search/", {"type": "r", "q": f'"{q}"', "filed_after": after,
                           "order_by": "dateFiled desc", "court": courts}
        while url and (not limit or len(seen) < limit):
            r = client.get(url, params=params)
            if r.status_code != 200:
                res.note = f"HTTP {r.status_code} on search '{q}'"
                break
            data = r.json()
            fresh = []
            for hit in data.get("results", []):
                if not is_bankruptcy(hit):
                    continue
                case = docket_to_case(hit)
                if not case:
                    res.skipped += 1
                    continue
                if case.courtlistener_id in seen:
                    continue
                seen[case.courtlistener_id] = case
                fresh.append(case)
                for rd in [{"recap_documents": [d], "description": d.get("description") or d.get("short_description"),
                            "document_number": d.get("document_number"), "date_filed": d.get("entry_date_filed")}
                           for d in hit.get("recap_documents") or []]:
                    doc = entry_to_doc(case, rd)
                    if doc and doc.source_url not in doc_urls:
                        doc_urls.add(doc.source_url)
                        res.docs.append(doc)
                if limit and len(seen) >= limit:
                    break
            if fresh and on_cases:
                on_cases(fresh)
            url, params = data.get("next"), None
    if search_only:
        return
    # Sale motions / bidding procedures / sale orders live in the docket entries (chapter-11 dockets first).
    for case in sorted(seen.values(), key=lambda c: c.chapter != "11"):
        if not case.courtlistener_id:
            continue
        url, params, pages = f"{API}/docket-entries/", {"docket": case.courtlistener_id, "order_by": "entry_number"}, 0
        while url and pages < MAX_ENTRY_PAGES:
            r = client.get(url, params=params)
            if r.status_code != 200:
                res.skipped += 1
                break
            data = r.json()
            for entry in data.get("results", []):
                doc = entry_to_doc(case, entry)
                if doc and doc.source_url not in doc_urls:
                    doc_urls.add(doc.source_url)
                    res.docs.append(doc)
            url, params, pages = data.get("next"), None, pages + 1


def run(limit=None, *, transport=None, today=None, search_only=False, on_cases=None, queries=None,
        ledger: CallLedger | None = None) -> SourceResult:
    try:
        client = make_client(transport=transport, ledger=ledger)
    except MissingSetting:
        return SourceResult("courtlistener", note=TOKEN_MISSING)
    with client:
        res = fetch_courtlistener(client, limit, today=today, search_only=search_only, on_cases=on_cases,
                                  queries=queries)
    if ledger is not None:
        res.note = (res.note + "; " if res.note else "") + f"logged_calls={len(ledger.calls)}"
    return res
