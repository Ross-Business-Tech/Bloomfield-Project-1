"""Pennsylvania DHS Licensed Personal Care Homes (source_id pa_dhs).

Source: data.pa.gov dataset pqf4-d4xn (Socrata, "Public Domain U.S. Government", updated weekly), checked
Nov 10, 2026: ~989 homes. robots.txt blocks only /browse. NO license number and NO licensee column, so this
source adds buildings only (licensee NULL, never guessed) and cannot show operator turnover. The key is the
dataset's objectid (assumption: stable between weekly releases). Assisted Living Residences are not in any
bulk file (Human Services Provider Directory is a search form only).
"""
from __future__ import annotations

from collections import Counter
from datetime import date

from src.common.normalize import zip5

from .base import LicenseRow, ParseResult, clean
from .xlsx_util import MIN_CAPACITY, as_of_from, to_int

SOURCE_ID = "pa_dhs"
API_URL = "https://data.pa.gov/resource/pqf4-d4xn.json?$limit=50000&$order=objectid"
DATASET_PAGE = "https://data.pa.gov/d/pqf4-d4xn"


def parse(data: list[dict], source_url: str, as_of: date) -> tuple[list[LicenseRow], int, dict]:
    rows, counts = [], Counter()
    for d in data:
        key = clean(d.get("objectid"))
        if not key:
            continue
        cap = to_int(d.get("totcapcty"))
        if cap is not None and cap < MIN_CAPACITY:
            counts["small (<13 beds)"] += 1
            continue
        counts[clean(d.get("typservtxt")) or "?"] += 1
        rows.append(LicenseRow(
            state="PA", license_no=key, facility_name=clean(d.get("facname")), street=clean(d.get("street")),
            city=clean(d.get("city")), zip5=zip5(d.get("zip")), licensee=None,
            license_type="Personal Care Home (assisted living)", capacity=cap, status=None, as_of=as_of,
            source_url=source_url,
            raw={k: d.get(k) for k in ("objectid", "typservtxt", "totcapcty", "facname", "street", "city", "state",
                                       "zip", "pgmofftxt", "lat", "lon")}))
    return rows, len(data), dict(counts)


def fetch(client, limit: int | None = None, today: date | None = None) -> ParseResult:
    resp = client.get(API_URL)
    if resp.status_code != 200:
        raise RuntimeError(f"{API_URL} returned HTTP {resp.status_code}")
    as_of, basis = as_of_from(resp, today)
    rows, total, counts = parse(resp.json(), DATASET_PAGE, as_of)
    kept = len(rows)
    rows = rows[:limit] if limit else rows
    return ParseResult(SOURCE_ID, DATASET_PAGE, as_of, basis, total, counts, rows, kept, {"personal care home": len(rows)})
