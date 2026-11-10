"""Iowa DIAL "Assisted Living Programs in Iowa" (source_id ia_dial).

Source: data.iowa.gov dataset 562 (CC BY 4.0, attribution: Iowa Department of Inspections, Appeals, and
Licensing), checked Nov 10, 2026: https://idh-be.iowa.gov/api/v1/datasets/562/rows.csv (redirects to a signed
file). ~345 rows: certification_no, program_name, dementia_specific, occupancy, administrator, address.
There is NO licensee/operator column, so this source adds buildings only and cannot show operator turnover
(licensee stays NULL; never guessed from the administrator).
"""
from __future__ import annotations

import csv
import io
from collections import Counter
from datetime import date
from email.utils import parsedate_to_datetime

from src.common.normalize import zip5

from .base import LicenseRow, ParseResult, clean

SOURCE_ID = "ia_dial"
FILE_URL = "https://idh-be.iowa.gov/api/v1/datasets/562/rows.csv"
DATASET_PAGE = "https://data.iowa.gov/d/562"


def parse(text: str, source_url: str, as_of: date) -> tuple[list[LicenseRow], int, dict]:
    rows, counts, total = [], Counter(), 0
    for rec in csv.DictReader(io.StringIO(text)):
        total += 1
        lic = clean(rec.get("certification_no"))
        if not lic:
            counts["no_number"] += 1
            continue
        dementia = (rec.get("dementia_specific") or "").strip().lower() == "yes"
        counts["dementia_specific" if dementia else "assisted_living"] += 1
        occ = (rec.get("occupancy") or "").strip()
        rows.append(LicenseRow(
            state="IA", license_no=lic, facility_name=clean(rec.get("program_name")),
            street=clean(rec.get("facility_address")), city=clean(rec.get("facility_city")),
            zip5=zip5(rec.get("facility_zip")), licensee=None,
            license_type="Assisted Living Program" + (" - Dementia specific" if dementia else ""),
            capacity=int(occ) if occ.isdigit() else None, status=None, as_of=as_of, source_url=source_url,
            # contact e-mails and administrator names are personal data: not stored
            raw={k: v for k, v in rec.items() if k not in ("email", "admin_first_name", "admin_last_name",
                                                           "phone_number", "fax_number")},
        ))
    return rows, total, dict(counts)


def fetch(client, limit: int | None = None, today: date | None = None) -> ParseResult:
    resp = client.get(FILE_URL)
    if resp.status_code != 200:
        raise RuntimeError(f"{FILE_URL} returned HTTP {resp.status_code}")
    lm = {k.lower(): v for k, v in resp.headers.items()}.get("last-modified")
    try:
        as_of, basis = parsedate_to_datetime(lm).date(), "Last-Modified header"
    except (TypeError, ValueError):
        as_of, basis = today or date.today(), "download date"
    rows, total, counts = parse(resp.content.decode("utf-8-sig", errors="replace"), DATASET_PAGE, as_of)
    kept = len(rows)
    if limit:
        rows = rows[:limit]
    return ParseResult(SOURCE_ID, DATASET_PAGE, as_of, basis, total, counts, rows, kept,
                       dict(Counter(r.license_type for r in rows)))
