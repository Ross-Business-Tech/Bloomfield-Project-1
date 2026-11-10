"""CourtListener API v4: bankruptcy dockets mentioning senior-housing terms + sale-related docket entries.

Auth: header `Authorization: Token <COURTLISTENER_TOKEN>` is set on the HTTP client only. Cache files are keyed by URL
and never contain request headers, so the token is never written to disk. The token is never printed.
"""
from __future__ import annotations

import re
from urllib.parse import urljoin

from src.common.config import MissingSetting, env
from src.common.http import PoliteClient

from .base import (SALE_TEXT, WINDOW_MONTHS, CaseRecord, DocRecord, SourceResult,
                   has_buyer_evidence, months_ago, parse_date, to_datetime)

SITE = "https://www.courtlistener.com"
API = f"{SITE}/api/rest/v4"
QUERIES = ["assisted living", "senior living", "memory care", "nursing home", "skilled nursing",
           "continuing care", "life plan community"]
MAX_ENTRY_PAGES = 3          # docket-entry pages (20 entries each) scanned per docket
TOKEN_MISSING = "COURTLISTENER_TOKEN not set, skipping"


MAX_REQUESTS = 1500          # whole-run cap: CourtListener is a free nonprofit service


class RequestCapReached(Exception):
    pass


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


def make_client(*, transport=None, **kw) -> CourtListenerClient:
    """Raises MissingSetting when the token is absent."""
    token = env("COURTLISTENER_TOKEN")
    return CourtListenerClient("courtlistener", transport=transport,
                               headers={"Authorization": f"Token {token}", "Accept": "application/json"}, **kw)


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
    except RequestCapReached as exc:
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


def run(limit=None, *, transport=None, today=None, search_only=False, on_cases=None, queries=None) -> SourceResult:
    try:
        client = make_client(transport=transport)
    except MissingSetting:
        return SourceResult("courtlistener", note=TOKEN_MISSING)
    with client:
        return fetch_courtlistener(client, limit, today=today, search_only=search_only, on_cases=on_cases,
                                   queries=queries)
