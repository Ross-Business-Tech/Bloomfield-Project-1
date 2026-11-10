"""Texas HHSC directory of licensed Assisted Living Facilities (source_id tx_hhsc).

File: https://apps.hhs.texas.gov/providers/directories/al.xlsx ("Directory of all ALFs (Excel)"), checked
Nov 10, 2026: ~2,016 active ALFs, refreshed about daily. robots.txt on apps.hhs.texas.gov disallows only *.xml.
CAVEAT: the linking page on www.hhs.texas.gov returns 403 to our crawler, so (a) we fetch only the file on
apps.hhs.texas.gov, which serves it to us, and (b) the HHSC terms page could not be read (Texas state data is
generally public information; not confirmed). If apps.hhs.texas.gov ever refuses, the loader stops (SourceBlocked).
Columns used: Facility ID (stable key), License No, Facility Name, physical address, Total Licensed Capacity,
Alzheimer Capacity, Owner_ (licensee), Management Company_, License Effective / Initial License Date.
Administrator names and e-mails are personal data and are not stored.
"""
from __future__ import annotations

from collections import Counter
from datetime import date

from src.common.normalize import zip5

from .base import LicenseRow, ParseResult, clean
from .xlsx_util import MIN_CAPACITY, as_of_from, json_safe, sheet_rows, to_int

SOURCE_ID = "tx_hhsc"
FILE_URL = "https://apps.hhs.texas.gov/providers/directories/al.xlsx"
# header spellings in 2021-2023 Wayback captures -> current spelling
ALIASES = {"Facility_ID": "Facility ID", "License Number": "License No", "County_": "County", "Region": "State Region"}
KEEP_RAW = ("Facility Name", "Facility ID", "Program Type", "County", "Service Type", "License No",
            "License Effective Date", "License Expiration Date", "Initial License Date", "Physical Address",
            "Physical Address CITY", "Physical Address State", "Physical Address Zipcode", "Total Licensed Capacity",
            "Alzheimer Capacity", "Owner_", "Type of Entity", "Owner Mailing1", "Owner Mailing City",
            "Owner Mailing STATE", "Owner Mailing Zipcode", "Management Company_")


def parse(content: bytes, source_url: str, as_of: date) -> tuple[list[LicenseRow], int, dict]:
    headers, data = sheet_rows(content, ("Facility ID", "Facility_ID"))
    col = {ALIASES.get(h, h): i for i, h in enumerate(headers) if h}
    g = lambda r, h: r[col[h]] if col.get(h) is not None and col[h] < len(r) else None  # noqa: E731
    rows, counts = [], Counter()
    for r in data:
        fid = clean(g(r, "Facility ID"))
        if not fid:
            continue
        cap = to_int(g(r, "Total Licensed Capacity"))
        stype = clean(g(r, "Service Type")) or "?"
        if cap is not None and cap < MIN_CAPACITY:
            counts["small (<13 beds)"] += 1
            continue
        counts[stype] += 1
        alz = to_int(g(r, "Alzheimer Capacity")) or 0
        rows.append(LicenseRow(
            state="TX", license_no=fid, facility_name=clean(g(r, "Facility Name")),
            street=clean(g(r, "Physical Address")), city=clean(g(r, "Physical Address CITY")),
            zip5=zip5(clean(g(r, "Physical Address Zipcode"))), licensee=clean(g(r, "Owner_")),
            license_type=f"Assisted Living Facility ({stype})" + (" - Alzheimer/memory care" if alz else ""),
            capacity=cap, status="active", as_of=as_of, source_url=source_url,
            raw={h: json_safe(g(r, h)) for h in KEEP_RAW},
            management_co=clean(g(r, "Management Company_")) or clean(g(r, "Management Company"))))
    return rows, len(data), dict(counts)


def fetch(client, limit: int | None = None, today: date | None = None) -> ParseResult:
    resp = client.get(FILE_URL)
    if resp.status_code != 200:
        raise RuntimeError(f"{FILE_URL} returned HTTP {resp.status_code}")
    as_of, basis = as_of_from(resp, today)
    rows, total, counts = parse(resp.content, FILE_URL, as_of)
    kept = len(rows)
    rows = rows[:limit] if limit else rows
    return ParseResult(SOURCE_ID, FILE_URL, as_of, basis, total, counts, rows, kept,
                       dict(Counter(r.license_type for r in rows)))


# ── closures (source_id tx_hhsc_closures) ────────────────────────────────────────────────────────────────────────
CLOSURES_SOURCE_ID = "tx_hhsc_closures"
CLOSURES_URL = "https://apps.hhs.texas.gov/providers/directories/Closures/alf_closures.xlsx"


def parse_closures(content: bytes, source_url: str, as_of: date) -> list[dict]:
    """Cumulative closure list: Owner, Facility, Facility ID, Address, City, State, ZIP, County, Date of Closure,
    Program, Service Type. One dict per row (stg.state_closure shape + raw payload)."""
    from datetime import datetime as _dt
    headers, data = sheet_rows(content, "Facility ID")
    col = {h: i for i, h in enumerate(headers) if h}
    g = lambda r, h: r[col[h]] if col.get(h) is not None and col[h] < len(r) else None  # noqa: E731
    out = []
    for r in data:
        key = clean(g(r, "Facility ID"))
        if not key:
            continue
        d = g(r, "Date of Closure")
        out.append({"source_url": source_url, "as_of": as_of, "state": "TX", "facility_key": key,
                    "facility_name": clean(g(r, "Facility")), "owner": clean(g(r, "Owner")),
                    "street": clean(g(r, "Address")), "city": clean(g(r, "City")), "zip5": zip5(clean(g(r, "ZIP"))),
                    "closure_date": d.date() if isinstance(d, _dt) else (d if isinstance(d, date) else None),
                    "program": clean(g(r, "Program")), "service_type": clean(g(r, "Service Type")),
                    "_raw": {h: json_safe(g(r, h)) for h in headers if h}})
    return out


def load_closures(conn, client, dry_run: bool = False) -> dict:
    from src.common.bulk import bulk_upsert
    from src.common.ingest_run import ingest_run
    from src.common.raw_writer import write_records

    resp = client.get(CLOSURES_URL)
    if resp.status_code != 200:
        raise RuntimeError(f"{CLOSURES_URL} returned HTTP {resp.status_code}")
    as_of, _ = as_of_from(resp)
    rows = parse_closures(resp.content, CLOSURES_URL, as_of)
    # one row per facility + closure date (the file can repeat a row)
    rows = list({(r["facility_key"], r["closure_date"]): r for r in rows}.values())
    out = {"rows": len(rows), "as_of": str(as_of),
           "since_2025": sum(1 for r in rows if r["closure_date"] and r["closure_date"] >= date(2025, 1, 1))}
    if dry_run:
        return out
    cols = ["source_url", "as_of", "state", "facility_key", "facility_name", "owner", "street", "city", "zip5",
            "closure_date", "program", "service_type"]
    with ingest_run(conn, CLOSURES_SOURCE_ID) as run:
        run.rows_in += write_records(conn, CLOSURES_SOURCE_ID, run.run_id,
                                     [(r["facility_key"], CLOSURES_URL, r["_raw"]) for r in rows])
        bulk_upsert(conn, "stg.state_closure", cols, [[r[c] for c in cols] for r in rows],
                    ["state", "facility_key", "closure_date"])
    return out
