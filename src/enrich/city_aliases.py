"""Apply config/city_aliases.yaml to facilities already in the database (new rows get it at load time).

    python -m src.enrich.city_aliases [--dry-run]

Only core.facility.city changes, and only where an alias matches; every change is printed (old -> new) so it can be
undone by hand. Re-running is a no-op.
"""
from __future__ import annotations

import argparse
import sys

from src.common.normalize import city_alias


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m src.enrich.city_aliases")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    from src.common.db import connect
    with connect() as conn:
        rows = conn.execute("select facility_id::text f, canonical_name n, city, state from core.facility "
                            "where city is not null and state is not null").fetchall()
        changes = [(r, city_alias(r["city"], r["state"])) for r in rows]
        changes = [(r, new) for r, new in changes if new != r["city"]]
        for r, new in changes:
            print(f"{r['n']} ({r['state']}): {r['city']} -> {new}")
            if not a.dry_run:
                conn.execute("update core.facility set city = %s where facility_id = %s", (new, r["f"]))
        conn.commit()
    print(f"{len(changes)} facilities {'would change' if a.dry_run else 'changed'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
