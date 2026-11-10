"""New York DOH adult care facilities (Adult Homes + Enriched Housing, incl. ALR/EALR/SNALR) (source_id ny_doh).

Source: health.data.ny.gov (Socrata), checked Nov 10, 2026. robots.txt allows /resource (only /browse is
disallowed, Crawl-delay 1). Attribution: New York State Department of Health.
- vn5v-hh5r "Health Facility General Information": one row per facility, with operator_name (the licensed
  operator), operator certificate number and address. We keep description in ('Adult Home', 'Enriched Housing
  Program') (~527 rows).
- 2g9y-7kqm "Health Facility Certification Information": bed counts per facility. 'Overall Capacity (AH/EHP)'
  = capacity; ALR / EALR / SNALR / ALP / Dementia beds tell us the care type.
The files hold only the current state; operator changes are found by comparing our own dated snapshots
(as_of = the Last-Modified header of the general-information file).
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date
from email.utils import parsedate_to_datetime

from src.common.normalize import zip5

from .base import LicenseRow, ParseResult, clean

SOURCE_ID = "ny_doh"
BASE = "https://health.data.ny.gov/resource/"
KEEP = ("Adult Home", "Enriched Housing Program")
_IN = "('Adult Home','Enriched Housing Program')"
GENERAL_URL = BASE + f"vn5v-hh5r.json?$limit=5000&$where=description in{_IN}&$order=fac_id"
CERT_URL = BASE + f"2g9y-7kqm.json?$limit=20000&$where=description in{_IN} and attribute_type='Bed'&$order=fac_id"
# what the lead list links to (one facility's public record)
DATASET_PAGE = "https://health.data.ny.gov/d/vn5v-hh5r"

BED_LABELS = {
    "Assisted Living Residence (ALR)": "ALR",
    "Enhanced Assisted Living Residence (EALR)": "EALR",
    "Special Needs Assisted Living Residence (SNALR)": "SNALR",
    "Assisted Living Program (ALP)": "ALP",
    "Dementia": "Dementia",
}


def _int(v) -> int | None:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def parse(general: list[dict], certs: list[dict], source_url: str, as_of: date) -> tuple[list[LicenseRow], int, dict]:
    beds = defaultdict(dict)
    for c in certs:
        beds[str(c.get("fac_id"))][c.get("attribute_value")] = _int(c.get("measure_value"))
    counts: Counter = Counter()
    rows = []
    for g in general:
        desc = g.get("description")
        counts[desc] += 1
        if desc not in KEEP or not g.get("fac_id"):
            continue
        fid = str(g["fac_id"])
        b = beds.get(fid, {})
        programs = [short for label, short in BED_LABELS.items() if b.get(label)]
        # license_type text drives care_types_from_text: 'Assisted Living' -> AL, 'Dementia' -> MC
        ltype = desc + (" - Assisted Living " + "/".join(programs) if programs else " - Assisted Living (adult care)")
        rows.append(LicenseRow(
            state="NY", license_no=fid, facility_name=clean(g.get("facility_name")),
            street=clean(g.get("address1")), city=clean(g.get("city")), zip5=zip5(g.get("fac_zip")),
            licensee=clean(g.get("operator_name")), license_type=ltype,
            capacity=b.get("Overall Capacity (AH/EHP)"), status=None, as_of=as_of, source_url=source_url,
            raw={**{k: v for k, v in g.items() if not k.startswith(":@") and k != "facility_location"},
                 "beds": b},
        ))
    return rows, len(general), dict(counts)


def fetch(client, limit: int | None = None, today: date | None = None) -> ParseResult:
    resp = client.get(GENERAL_URL)
    if resp.status_code != 200:
        raise RuntimeError(f"{GENERAL_URL} returned HTTP {resp.status_code}")
    lm = {k.lower(): v for k, v in resp.headers.items()}.get("last-modified")
    try:
        as_of, basis = parsedate_to_datetime(lm).date(), "Last-Modified header"
    except (TypeError, ValueError):
        as_of, basis = today or date.today(), "download date"
    cresp = client.get(CERT_URL)
    if cresp.status_code != 200:
        raise RuntimeError(f"{CERT_URL} returned HTTP {cresp.status_code}")
    rows, total, counts = parse(resp.json(), cresp.json(), DATASET_PAGE, as_of)
    kept = len(rows)
    if limit:
        rows = rows[:limit]
    return ParseResult(SOURCE_ID, DATASET_PAGE, as_of, basis, total, counts, rows, kept,
                       dict(Counter(r.raw.get("description") for r in rows)))
