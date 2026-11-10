"""CLI: python -m src.extract [--limit N] [--eval] [--dry-run] [--model M] [--sync|--batch]"""
from __future__ import annotations

import argparse

from . import client as cl


def main(argv=None) -> None:
    import sys
    sys.stdout.reconfigure(line_buffering=True)  # progress shows up live when output goes to a log file
    p = argparse.ArgumentParser(prog="python -m src.extract", description=__doc__)
    p.add_argument("--limit", type=int, help="only the first N unprocessed documents")
    p.add_argument("--eval", action="store_true", help="score the 20 labeled eval cases instead of the full run")
    p.add_argument("--dry-run", action="store_true", help="count + cost estimate only; no API calls, no writes")
    p.add_argument("--model", default=cl.DEFAULT_MODEL)
    p.add_argument("--budget", type=float, default=15.0, help="abort if the estimate exceeds this many USD")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--batch", dest="use_batch", action="store_true", default=None, help="force Message Batches")
    g.add_argument("--sync", dest="use_batch", action="store_false", help="force standard (synchronous) calls")
    a = p.parse_args(argv)

    if a.eval:
        from .evaluate import run_eval
        run_eval(a.model)
        return

    from src.common.db import connect

    from .run import run
    with connect() as conn:
        run(conn, model=a.model, limit=a.limit, dry_run=a.dry_run, use_batch=a.use_batch, budget=a.budget)
        mb = conn.execute("select pg_database_size(current_database())/1048576 as mb").fetchone()["mb"]
        print(f"database size: {mb} MB" + ("  WARNING: over 400 MB (free plan limit 500 MB)" if mb > 400 else ""))


if __name__ == "__main__":
    main()
