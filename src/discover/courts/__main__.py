"""python -m src.discover.courts [--source ID] [--limit N] [--dry-run]"""
import argparse
import sys

from .loader import SOURCES, run_all


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m src.discover.courts")
    ap.add_argument("--source", choices=SOURCES, help="run one source (default: all)")
    ap.add_argument("--limit", type=int, help="max cases (CourtListener, Stretto, Epiq) / pages (ElevenFlo)")
    ap.add_argument("--dry-run", action="store_true", help="fetch, parse, validate; print counts + samples; no database")
    ap.add_argument("--search-only", action="store_true",
                    help="courtlistener: case list only (court ids + searches), no docket-entry fetches; saves per page")
    ap.add_argument("--query", action="append",
                    help="courtlistener --search-only: exact-phrase debtor search instead of the standard queries (repeatable)")
    a = ap.parse_args(argv)
    if a.query and not (a.source == "courtlistener" and a.search_only):
        ap.error("--query needs --source courtlistener --search-only")
    return run_all([a.source] if a.source else list(SOURCES), a.limit, a.dry_run, a.search_only, a.query)


if __name__ == "__main__":
    sys.exit(main())
