"""Shared types and the database step for every state license loader."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date


class ManualSource(Exception):
    """The source has no polite bulk download; it must be handled by hand. Never scrape around this."""


@dataclass
class LicenseRow:
    """One stg.state_license row plus the untouched source record (goes to raw.record)."""
    state: str
    license_no: str
    facility_name: str | None
    street: str | None
    city: str | None
    zip5: str | None
    licensee: str | None
    license_type: str | None
    capacity: int | None
    status: str | None
    as_of: date
    source_url: str
    raw: dict = field(default_factory=dict)
    management_co: str | None = None      # Texas: management company (a change = operator turnover)
    snapshot_origin: str = "live"         # 'live' download or 'wayback' capture


@dataclass
class ParseResult:
    source_id: str
    source_url: str
    as_of: date
    as_of_basis: str                     # "Last-Modified header" or "download date"
    total_rows: int                      # every record in the file
    type_counts_total: dict              # license type code -> count, whole file
    rows: list[LicenseRow]               # kept rows (after type filter and --limit)
    kept_before_limit: int = 0
    type_counts_kept: dict = field(default_factory=dict)


def clean(value) -> str | None:
    """Trim; empty -> None (unknown is NULL, never guessed)."""
    if value is None:
        return None
    value = " ".join(str(value).split())
    return value or None


STG_UPSERT = """
    insert into stg.state_license
        (source_url, as_of, state, license_no, facility_name, street, city, zip5, licensee,
         license_type, capacity, status)
    values (%(source_url)s, %(as_of)s, %(state)s, %(license_no)s, %(facility_name)s, %(street)s,
            %(city)s, %(zip5)s, %(licensee)s, %(license_type)s, %(capacity)s, %(status)s)
    on conflict (state, license_no, as_of) do update set
        source_url = excluded.source_url, facility_name = excluded.facility_name,
        street = excluded.street, city = excluded.city, zip5 = excluded.zip5,
        licensee = excluded.licensee, license_type = excluded.license_type,
        capacity = excluded.capacity, status = excluded.status
"""


def save(conn, result: ParseResult) -> dict:
    """raw.record then stg.state_license, inside one ingest_run. Re-runs add no duplicates."""
    from src.common.ingest_run import ingest_run
    from src.common.raw_writer import write_records

    with ingest_run(conn, result.source_id) as run:
        new_raw = write_records(
            conn, result.source_id, run.run_id,
            [(r.license_no, r.source_url, r.raw) for r in result.rows],
        )
        run.rows_in += new_raw
        # COPY-based bulk upsert (src/common/bulk.py); same semantics as STG_UPSERT, one round trip.
        from src.common.bulk import bulk_upsert

        cols = ["source_url", "as_of", "state", "license_no", "facility_name", "street", "city",
                "zip5", "licensee", "license_type", "capacity", "status", "management_co", "snapshot_origin"]
        bulk_upsert(conn, "stg.state_license", cols,
                    [[getattr(r, k) for k in cols] for r in result.rows], ["state", "license_no", "as_of"])
    return {"raw_new": new_raw, "stg_rows_upserted": len(result.rows)}
