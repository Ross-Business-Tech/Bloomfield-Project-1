"""python -m src.enrich [--step STEP ...] [--limit N]

Runs the core-builder steps in order (default: all). Every step commits on its own, so a dropped
connection loses at most the current batch, and re-running inserts 0 duplicates.
"""
from __future__ import annotations

import argparse
import sys
import time

from src.common.db import connect

from .steps import STEPS, SizeLimitReached, guard_size

ORDER = ["facilities", "orgs", "chow", "hud", "licenses", "closures", "courts", "manual", "extraction", "dedupe", "metrics", "review",
         "hud_sample"]
# One-off steps that only run when named with --step (never part of the default run).
ONE_OFF = ["direction_reset", "hud_reset"]
COUNT_TABLES = ["facility", "facility_xref", "organization", "facility_role", "ownership_event", "loan",
                "legal_case", "case_facility", "metric"]


def counts(conn) -> dict:
    return {t: conn.execute(f"select count(*) as n from core.{t}").fetchone()["n"] for t in COUNT_TABLES}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m src.enrich", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--step", action="append", choices=ORDER + ONE_OFF, help="run only this step (repeatable)")
    ap.add_argument("--limit", type=int, default=None, help="process only the first N source rows per step")
    args = ap.parse_args(argv)
    steps = [s for s in ONE_OFF + ORDER if (s in args.step if args.step else s in ORDER)]
    with connect() as conn:
        try:
            guard_size(conn, "start")
            for name in steps:
                t0 = time.time()
                print(f"== {name}", flush=True)
                result = STEPS[name](conn, args.limit)
                for k, v in result.items():
                    if isinstance(v, list):          # long lists (merge report) one per line
                        print(f"  {k}: {len(v)}", flush=True)
                        for item in v:
                            print(f"    - {item}", flush=True)
                result = {k: v for k, v in result.items() if not isinstance(v, list)}
                print(f"  {result}  ({time.time() - t0:.0f}s)", flush=True)
                guard_size(conn, name)
        except SizeLimitReached as exc:
            print(f"STOPPED: {exc}", file=sys.stderr)
            return 2
        print("core row counts:", counts(conn))
    return 0
