"""python -m src.discover.licenses [--source mi_lara|fl_ahca|nc_dhsr|tx_hhsc|ny_doh|ia_dial|pa_dhs] [--limit N] [--dry-run] [--refresh]"""
from __future__ import annotations

import argparse
import sys

from src.common.http import PoliteClient, SourceBlocked

from . import florida, iowa, michigan, new_york, north_carolina, pennsylvania, texas
from .base import ManualSource, save

SOURCES = {"mi_lara": michigan, "fl_ahca": florida, "nc_dhsr": north_carolina, "tx_hhsc": texas,
           "ny_doh": new_york, "ia_dial": iowa, "pa_dhs": pennsylvania}


def report(res) -> None:
    print(f"[{res.source_id}] file: {res.source_url}")
    print(f"  as_of: {res.as_of} (from {res.as_of_basis})")
    print(f"  total rows in file: {res.total_rows}")
    print(f"  by license type (whole file): {dict(sorted(res.type_counts_total.items()))}")
    print(f"  rows kept after type filter: {res.kept_before_limit}")
    print(f"  rows in this run (after --limit): {len(res.rows)}  by type: {dict(sorted(res.type_counts_kept.items()))}")
    for r in res.rows[:3]:
        print("  sample:", {k: getattr(r, k) for k in (
            "state", "license_no", "facility_name", "street", "city", "zip5", "licensee",
            "license_type", "capacity", "status")})


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m src.discover.licenses")
    ap.add_argument("--source", choices=sorted(SOURCES))
    ap.add_argument("--limit", type=int)
    ap.add_argument("--dry-run", action="store_true", help="download + parse only; no database")
    ap.add_argument("--refresh", action="store_true", help="ignore the on-disk cache")
    ap.add_argument("--closures", action="store_true", help="load the Texas ALF closures list only")
    args = ap.parse_args(argv)
    if args.closures:
        with PoliteClient(texas.CLOSURES_SOURCE_ID, use_cache=not args.refresh) as client:
            if args.dry_run:
                print("tx closures (dry run):", texas.load_closures(None, client, dry_run=True))
                return 0
            from src.common.db import connect
            with connect() as conn:
                print("tx closures:", texas.load_closures(conn, client))
        return 0

    code = 0
    for sid in ([args.source] if args.source else list(SOURCES)):
        mod = SOURCES[sid]
        with PoliteClient(sid, use_cache=not args.refresh) as client:
            try:
                res = mod.fetch(client, args.limit)
            except ManualSource as exc:
                print(f"[{sid}] MANUAL: {exc}")
                continue
            except SourceBlocked as exc:
                print(f"[{sid}] BLOCKED, mark source manual: {exc}", file=sys.stderr)
                code = 2
                continue
        report(res)
        if args.dry_run:
            print(f"  dry run: nothing written to the database")
            continue
        from src.common.db import connect
        with connect() as conn:
            print("  saved:", save(conn, res))
    return code


if __name__ == "__main__":
    sys.exit(main())
