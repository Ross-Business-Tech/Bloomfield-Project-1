"""North Carolina DHSR Adult Care Homes / Homes for the Aged roster (source_id nc_dhsr).

File: https://info.ncdhhs.gov/dhsr/data/Ahlist.xlsx, linked from https://info.ncdhhs.gov/dhsr/reports.htm
(checked Nov 10, 2026: ~570 homes, Last-Modified Oct 28, 2026). No robots.txt (404). Terms:
https://info.ncdhhs.gov/dhsr/disclaim.html "N.C. DHSR grants permission to copy and distribute files".
Columns used: License #, Name of Licensee Legal Name, DBA Name, Site Address/City/Zip, Bed Count, Expiry Date.
Family Care Homes (Fchlist.xlsx, 2-6 beds) are not loaded: not investable (same rule as Michigan).
Current state only: licensee changes come from comparing our dated snapshots.
"""
from __future__ import annotations

from collections import Counter
from datetime import date

from src.common.normalize import zip5

from .base import LicenseRow, ParseResult, clean
from .xlsx_util import MIN_CAPACITY, as_of_from, json_safe, sheet_rows, to_int

SOURCE_ID = "nc_dhsr"
FILE_URL = "https://info.ncdhhs.gov/dhsr/data/Ahlist.xlsx"


def parse(content: bytes, source_url: str, as_of: date) -> tuple[list[LicenseRow], int, dict]:
    headers, data = sheet_rows(content, "License #")
    col = {h: i for i, h in enumerate(headers) if h}
    g = lambda r, h: r[col[h]] if col.get(h) is not None and col[h] < len(r) else None  # noqa: E731
    rows, counts = [], Counter()
    for r in data:
        lic = clean(g(r, "License #"))
        if not lic:
            continue
        cap = to_int(g(r, "Bed Count"))
        if cap is not None and cap < MIN_CAPACITY:
            counts["small (<13 beds)"] += 1
            continue
        counts["adult care home"] += 1
        # contact person names / phones are personal data: kept out of raw
        raw = {h: json_safe(g(r, h)) for h in ("County", "License #", "Name of Licensee Legal Name", "DBA Name",
                                                 "Site Address", "Site City", "Site State", "Site Zip", "Bed Count",
                                                 "Star Rating", "Expiry Date")}
        rows.append(LicenseRow(
            state="NC", license_no=lic, facility_name=clean(g(r, "DBA Name")),
            street=clean(g(r, "Site Address")), city=clean(g(r, "Site City")), zip5=zip5(clean(g(r, "Site Zip"))),
            licensee=clean(g(r, "Name of Licensee Legal Name")),
            license_type="Adult Care Home (assisted living)", capacity=cap, status="licensed",
            as_of=as_of, source_url=source_url, raw=raw))
    return rows, len(data), dict(counts)


def fetch(client, limit: int | None = None, today: date | None = None) -> ParseResult:
    resp = client.get(FILE_URL)
    if resp.status_code != 200:
        raise RuntimeError(f"{FILE_URL} returned HTTP {resp.status_code}")
    as_of, basis = as_of_from(resp, today)
    rows, total, counts = parse(resp.content, FILE_URL, as_of)
    kept = len(rows)
    rows = rows[:limit] if limit else rows
    return ParseResult(SOURCE_ID, FILE_URL, as_of, basis, total, counts, rows, kept, {"adult care home": len(rows)})
