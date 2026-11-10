"""Stretto and Epiq claims-agent case indexes (case pages only, no document crawling).

Both sites are JavaScript apps, but each feeds its own case list from a plain JSON endpoint that robots.txt allows:
  Stretto: GET https://cases.stretto.com/wp-admin/admin-ajax.php?action=case_list_data   (robots: Allow admin-ajax.php)
  Epiq:    GET https://dm.epiq11.com/api/search/getcases?showActive=false&havingClaims=false
The index has no industry field, so healthcare is matched on case / debtor names (see base.HEALTHCARE_NAME).
"""
from __future__ import annotations

import re

from .base import (HEALTHCARE_NAME, UNKNOWN_COURT, WINDOW_MONTHS, CaseRecord, SourceResult,
                   months_ago, parse_date)

STRETTO_BASE = "https://cases.stretto.com"
STRETTO_LEGACY = "https://case.stretto.com"
EPIQ_BASE = "https://dm.epiq11.com"


def _clean(v) -> str | None:
    v = (v or "").strip() if isinstance(v, str) else v
    return v or None


def stretto_case_url(row: dict) -> str | None:
    """Mirror the site's own link logic (caselist.js)."""
    url = _clean(row.get("URL"))
    vanity = _clean(row.get("VANITY_URL"))
    if row.get("IS_LEGACY") == "1" and url:
        return f"{STRETTO_LEGACY}/{url}"
    if vanity:
        return vanity if vanity.startswith("http") else f"https://{vanity}"
    return f"{STRETTO_BASE}/{url}" if url else None


def parse_stretto(payload: dict, *, today=None) -> SourceResult:
    res = SourceResult("stretto")
    cutoff = months_ago(WINDOW_MONTHS, today)
    seen = set()
    for row in payload.get("data", []):
        name = _clean(row.get("CASE_NAME"))
        debtor = _clean(row.get("DEBTOR_NAME"))
        if not (HEALTHCARE_NAME.search(name or "") or HEALTHCARE_NAME.search(debtor or "")):
            continue
        docket = _clean(row.get("DB_CASE_NO")) or _clean(row.get("CASE_NO"))
        url = stretto_case_url(row)
        filed = parse_date(row.get("DATE_FILED"))
        if not docket or not url:
            res.skipped += 1          # no docket number or page: cannot key the row, never guess
            continue
        if filed and filed < cutoff:
            continue
        court = _clean(row.get("NAME")) or UNKNOWN_COURT
        if (court, docket) in seen:
            continue
        seen.add((court, docket))
        res.cases.append(CaseRecord(
            "stretto", url, court, docket, case_name=name, filed_date=filed,
            debtor_name=debtor, claims_agent_url=url,
        ))
    return res


def fetch_stretto(client, limit=None, *, today=None) -> SourceResult:
    resp = client.get(f"{STRETTO_BASE}/wp-admin/admin-ajax.php", params={"action": "case_list_data"})
    if resp.status_code != 200:
        return SourceResult("stretto", note=f"HTTP {resp.status_code} from case index")
    res = parse_stretto(resp.json(), today=today)
    if limit:
        res.cases = res.cases[:limit]
    return res


def parse_epiq(rows: list, *, today=None) -> SourceResult:
    res = SourceResult("epiq")
    seen = set()
    for row in rows:
        name = _clean(row.get("text"))
        docket = _clean(row.get("caseNumber"))
        code = _clean(row.get("projectCode"))
        if not name or not HEALTHCARE_NAME.search(name):
            continue
        if not docket or not code:
            res.skipped += 1
            continue
        page = row.get("defaultPage") or "info"
        url = f"{EPIQ_BASE}/case/{code}/{page}"
        key = (docket, code)
        if key in seen:
            continue
        seen.add(key)
        # The index carries no court or filing date: court -> UNKNOWN sentinel, date -> NULL.
        res.cases.append(CaseRecord(
            "epiq", url, UNKNOWN_COURT, docket, case_name=name, debtor_name=name, claims_agent_url=url,
        ))
    return res


def fetch_epiq(client, limit=None, *, today=None) -> SourceResult:
    resp = client.get(f"{EPIQ_BASE}/api/search/getcases", params={"showActive": "false", "havingClaims": "false"})
    if resp.status_code != 200:
        return SourceResult("epiq", note=f"HTTP {resp.status_code} from case index")
    res = parse_epiq(resp.json(), today=today)
    if limit:
        res.cases = res.cases[:limit]
    return res
