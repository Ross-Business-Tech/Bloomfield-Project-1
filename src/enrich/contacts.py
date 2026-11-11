"""Property contact information: load a building's main phone line and public website into core.facility_contact.

    python -m src.enrich.contacts cms  --csv path/to/NH_ProviderInfo.csv      # CMS Provider Information (phone by CCN)
    python -m src.enrich.contacts manual --csv config/contacts_manual.csv     # hand-entered, each with a public link
    add --dry-run to check a file without writing anything.

Rules: business contact points only (main phone, public website); no personal names or e-mail addresses; every row
keeps the public page it came from. Re-running is safe (the same value for the same building is never duplicated).
Manual CSV columns: ccn or facility_id, kind (phone|website), value, label (optional), source_url (required, http/https).
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit

from src.common.normalize import normalize_phone

CMS_SOURCE = ("cms_provider_info", "https://data.cms.gov/provider-data/topics/nursing-homes")
MANUAL_SOURCE = "contact_manual"
# CMS has renamed columns between releases; accept the known spellings.
CCN_COLS = ("CMS Certification Number (CCN)", "Federal Provider Number", "Provider Number", "CCN", "ccn")
PHONE_COLS = ("Telephone Number", "Provider Phone Number", "Phone", "phone")
LABELS = {"phone": "Main line", "website": "Website"}


def clean_website(value) -> str | None:
    """Only a plain http(s) address with a real host; query strings and fragments (tracking) are dropped."""
    v = str(value or "").strip()
    if v and "://" not in v and "." in v and " " not in v:
        v = "https://" + v
    parts = urlsplit(v)
    if parts.scheme not in ("http", "https") or "." not in (parts.hostname or "") or parts.username or parts.password:
        return None
    return f"{parts.scheme}://{parts.netloc.lower()}{parts.path.rstrip('/')}"


def clean_value(kind: str, value) -> str | None:
    if kind == "phone":
        return normalize_phone(value)
    if kind == "website":
        return clean_website(value)
    return None


def _pick(row: dict, names) -> str | None:
    for n in names:
        if row.get(n) not in (None, ""):
            return str(row[n]).strip()
    return None


def parse_cms(path: Path) -> tuple[list[dict], dict]:
    """CMS Provider Information csv -> [{ccn, kind, value, label, source_id, source_url}], stats."""
    out, stats = [], {"rows": 0, "no_ccn": 0, "no_phone": 0, "bad_phone": 0}
    with open(path, encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            stats["rows"] += 1
            ccn = _pick(r, CCN_COLS)
            if not ccn:
                stats["no_ccn"] += 1
                continue
            raw_phone = _pick(r, PHONE_COLS)
            if not raw_phone:
                stats["no_phone"] += 1
                continue
            phone = normalize_phone(raw_phone)
            if not phone:
                stats["bad_phone"] += 1
                continue
            out.append({"ccn": ccn.zfill(6), "kind": "phone", "value": phone, "label": LABELS["phone"],
                        "source_id": CMS_SOURCE[0], "source_url": CMS_SOURCE[1]})
    return out, stats


def parse_manual(path: Path) -> tuple[list[dict], list[str]]:
    """Hand-entered rows. Returns (good rows, problems in plain words). A row without a public link is refused."""
    out, problems = [], []
    with open(path, encoding="utf-8-sig", newline="") as f:
        for i, r in enumerate(csv.DictReader(f), start=2):
            kind = (r.get("kind") or "").strip().lower()
            if kind not in ("phone", "website"):
                problems.append(f"line {i}: kind must be phone or website (personal e-mail addresses are never stored)")
                continue
            value = clean_value(kind, r.get("value"))
            if not value:
                problems.append(f"line {i}: '{r.get('value')}' is not a valid {kind}")
                continue
            link = (r.get("source_url") or "").strip()
            if not re.match(r"https?://", link):
                problems.append(f"line {i}: source_url must be a public web link")
                continue
            ccn, fid = (r.get("ccn") or "").strip(), (r.get("facility_id") or "").strip()
            if not (ccn or fid):
                problems.append(f"line {i}: give a ccn or a facility_id")
                continue
            out.append({"ccn": ccn.zfill(6) if ccn else None, "facility_id": fid or None, "kind": kind, "value": value,
                        "label": (r.get("label") or "").strip() or LABELS[kind], "source_id": MANUAL_SOURCE,
                        "source_url": link})
    return out, problems


def write(conn, rows: list[dict]) -> dict:
    """Upsert rows. A building is found by CCN (core.facility.ccn) or facility_id; unknown buildings are skipped."""
    done, unmatched = 0, 0
    for r in rows:
        if r.get("facility_id"):
            fac = conn.execute("select facility_id from core.facility where facility_id = %s::uuid",
                               (r["facility_id"],)).fetchone()
        else:
            fac = conn.execute("select facility_id from core.facility where ccn = %s", (r["ccn"],)).fetchone()
        if not fac:
            unmatched += 1
            continue
        conn.execute(
            """insert into core.facility_contact (facility_id, kind, value, label, source_id, source_url)
               values (%s, %s, %s, %s, %s, %s)
               on conflict (facility_id, kind, value) do update
                 set verified_at = current_date, source_url = excluded.source_url, label = excluded.label""",
            (fac["facility_id"], r["kind"], r["value"], r["label"], r["source_id"], r["source_url"]))
        done += 1
    conn.commit()
    return {"written": done, "building_not_found": unmatched}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m src.enrich.contacts", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source", choices=["cms", "manual"])
    ap.add_argument("--csv", type=Path, required=True)
    ap.add_argument("--dry-run", action="store_true", help="check the file, write nothing")
    a = ap.parse_args(argv)
    if a.source == "cms":
        rows, stats = parse_cms(a.csv)
        print(f"read {stats['rows']} rows: {len(rows)} usable phone numbers "
              f"(no CCN {stats['no_ccn']}, no phone {stats['no_phone']}, invalid phone {stats['bad_phone']})")
    else:
        rows, problems = parse_manual(a.csv)
        for p in problems:
            print("  skipped:", p)
        print(f"{len(rows)} usable rows, {len(problems)} skipped")
    if a.dry_run:
        return 0
    from src.common.db import connect
    with connect() as conn:
        print(write(conn, rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
