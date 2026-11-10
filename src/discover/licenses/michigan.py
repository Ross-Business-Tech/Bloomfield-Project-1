"""Michigan LARA statewide Adult Foster Care / Homes for the Aged text file (source_id mi_lara)."""
from __future__ import annotations

import csv
import io
import re
from collections import Counter
from datetime import date
from email.utils import parsedate_to_datetime
from html import unescape
from urllib.parse import urljoin

from src.common.normalize import zip5

from .base import LicenseRow, ParseResult, clean

SOURCE_ID = "mi_lara"
PAGE_URL = ("https://www.michigan.gov/lara/bureau-list/bchs/adult/online-lookups/"
            "statewide-text-file-of-adult-foster-care-homes-for-the-aged-facilities")
# Fallback if the page link cannot be found; the page link is preferred.
FILE_URL = "https://documents.apps.lara.state.mi.us/bchs/afc_sw.txt"

TYPE_LABELS = {
    "AF": "Adult Foster Care Family Home",
    "AS": "Adult Foster Care Small Group Home",
    "AM": "Adult Foster Care Medium Group Home",
    "AL": "Adult Foster Care Large Group Home",
    "AG": "Adult Foster Care Congregate",
    "AI": "Adult Foster Care County Infirmary",
    "AH": "Home for the Aged",
    "XH": "Home for the Aged (exempt)",   # not in LARA's record description; see README
}
# Kept: Homes for the Aged (licensed + exempt) and the larger AFC types (capacity 13+).
KEEP_TYPES = ("AH", "XH", "AL", "AG", "AI")

# NOTE: the file puts the street in column F (supplemental/unit in E) only when both exist; when F is empty
# the street is in E. So street = F or E.
# 0-based column positions (record description letters A..AE)
C_COUNTY, C_TYPE, C_LICNO, C_NAME, C_SUPP, C_STREET, C_CITY, C_STATE, C_ZIP, C_PHONE, C_CAP = range(11)
C_EFFECTIVE, C_EXPIRES, C_STATUS = 11, 12, 13
C_LICENSEE = 22
C_LICENSEE_STATUS = 30
N_COLS = 31


def find_file_url(page_html: str) -> str | None:
    """Find the afc_sw.txt link on the LARA page (stdlib only)."""
    for href in re.findall(r'href="([^"]+)"', page_html):
        href = unescape(href)
        if re.search(r"afc_sw\.txt", href, re.I):
            return urljoin(PAGE_URL, href)
    return None


def _date_ccyymmdd(v: str) -> str | None:
    v = (v or "").strip()
    return f"{v[:4]}-{v[4:6]}-{v[6:8]}" if re.fullmatch(r"\d{8}", v) else None


def parse_text(text: str, source_url: str, as_of: date, keep=KEEP_TYPES):
    """Return (rows, total, type_counts_total). Rows with the wrong column count are skipped and counted."""
    all_counts: Counter = Counter()
    rows: list[LicenseRow] = []
    total = 0
    for rec in csv.reader(io.StringIO(text)):
        if not rec:
            continue
        total += 1
        if len(rec) != N_COLS:
            all_counts["MALFORMED"] += 1
            continue
        rec = [c.strip() for c in rec]
        code = rec[C_TYPE]
        all_counts[code] += 1
        if code not in keep or not rec[C_LICNO]:
            continue
        cap = rec[C_CAP]
        raw = {
            "county_no": rec[C_COUNTY], "type_code": code, "license_no": rec[C_LICNO],
            "facility_name": rec[C_NAME], "supplemental_address": rec[C_SUPP], "street": rec[C_STREET],
            "city": rec[C_CITY], "state": rec[C_STATE], "zip": rec[C_ZIP], "phone": rec[C_PHONE],
            "capacity": cap, "effective": _date_ccyymmdd(rec[C_EFFECTIVE]),
            "expires": _date_ccyymmdd(rec[C_EXPIRES]), "license_status": rec[C_STATUS],
            "licensee": rec[C_LICENSEE], "licensee_address": rec[24], "licensee_supplemental": rec[25],
            "licensee_city": rec[26], "licensee_state": rec[27], "licensee_zip": rec[28],
            "licensee_phone": rec[29], "licensee_status": rec[C_LICENSEE_STATUS],
            "flags_O_to_V_and_X": rec[14:22] + [rec[23]],
        }
        rows.append(LicenseRow(
            state="MI", license_no=rec[C_LICNO], facility_name=clean(rec[C_NAME]),
            street=clean(rec[C_STREET]) or clean(rec[C_SUPP]), city=clean(rec[C_CITY]), zip5=zip5(rec[C_ZIP]),
            licensee=clean(rec[C_LICENSEE]), license_type=f"{TYPE_LABELS.get(code, code)} ({code})",
            capacity=int(cap) if cap.isdigit() else None, status=clean(rec[C_STATUS]),
            as_of=as_of, source_url=source_url, raw=raw,
        ))
    return rows, total, dict(all_counts)


def fetch(client, limit: int | None = None, today: date | None = None) -> ParseResult:
    page = client.get(PAGE_URL)
    file_url = find_file_url(page.text) or FILE_URL
    resp = client.get(file_url)
    if resp.status_code != 200:
        raise RuntimeError(f"{file_url} returned HTTP {resp.status_code}")
    lm = {k.lower(): v for k, v in resp.headers.items()}.get("last-modified")
    try:
        as_of, basis = parsedate_to_datetime(lm).date(), "Last-Modified header"
    except (TypeError, ValueError):
        as_of, basis = today or date.today(), "download date"
    rows, total, counts = parse_text(resp.content.decode("utf-8", errors="replace"), file_url, as_of)
    kept = len(rows)
    if limit:
        rows = rows[:limit]
    return ParseResult(SOURCE_ID, file_url, as_of, basis, total, counts, rows, kept,
                       dict(Counter(r.raw["type_code"] for r in rows)))
