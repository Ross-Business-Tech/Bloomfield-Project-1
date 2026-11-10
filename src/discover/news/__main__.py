"""python -m src.discover.news [--source ID] [--limit N] [--dry-run] [--fulltext-cap N]"""
from __future__ import annotations

import argparse
import sys

from . import loader as L


def run_source(source_id: str, limit: int | None, cap: int | None, backfill: int = 0) -> L.SourceResult:
    feeds = {"shn_rss": L.SHN_FEED, "snn_rss": L.SNN_FEED, "shb_rss": L.SHB_FEED,
             "mcknights_sl_rss": L.MCK_SL_FEED, "mcknights_ltc_rss": L.MCK_LTC_FEED}
    if source_id in feeds:
        return L.load_rss(source_id, [(None, feeds[source_id])], limit=limit, fulltext_cap=cap, backfill_months=backfill)
    if source_id == "bondoro_rss":
        return L.load_rss("bondoro_rss", L.BONDORO_FEEDS, limit=limit, fulltext_cap=cap)
    if source_id == "whatnow_rss":
        return L.load_rss("whatnow_rss", [(q, L.whatnow_url(q)) for q in L.WHATNOW_QUERIES], limit=limit, fulltext_cap=cap)
    if source_id == "gdelt_doc":
        return L.load_gdelt(limit=limit, fulltext_cap=cap)
    if source_id == "gnews_rss":
        return L.load_rss("gnews_rss", [(q, L.gnews_url(q)) for q in L.GNEWS_QUERIES], limit=limit, fulltext_cap=0)
    if source_id == "warn":
        return L.load_warn(limit=limit)
    raise SystemExit(f"unknown source {source_id}")


def report(res: L.SourceResult) -> None:
    kinds = {}
    for d in res.docs:
        kinds[d.kind] = kinds.get(d.kind, 0) + 1
    print(f"\n== {res.source_id}: {len(res.docs)} unique items "
          f"(full text: {kinds.get('full', 0)}, full from feed: {kinds.get('full_feed', 0)}, "
          f"summary only: {kinds.get('summary', 0)})")
    for q, info in res.per_query.items():
        print(f"   query {q}: {info}")
    if res.manual:
        print(f"   MANUAL: {res.manual}")
    elif res.blocked:
        print(f"   BLOCKED (some requests): {res.blocked}")
    dates = [d.published_at for d in res.docs if d.published_at]
    if dates:
        print(f"   oldest: {min(dates).date()}  newest: {max(dates).date()}")
    for d in res.docs[:3]:
        print(f"   - [{d.kind}] {d.title!r} | {d.published_at} | {d.source_url}\n     {(d.body_text or '')[:140]!r}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m src.discover.news")
    ap.add_argument("--source", choices=L.SOURCES)
    ap.add_argument("--limit", type=int, default=None, help="max items per feed")
    ap.add_argument("--dry-run", action="store_true", help="fetch + parse, print counts, no database")
    ap.add_argument("--fulltext-cap", type=int, default=None, help="max full-text article fetches per source")
    ap.add_argument("--backfill-months", type=int, default=0,
                    help="walk WordPress feeds (shn_rss, snn_rss, shb_rss) back N months via ?paged=N; 0 = daily run")
    args = ap.parse_args(argv)
    sources = [args.source] if args.source else L.SOURCES

    if args.dry_run:
        for s in sources:
            report(run_source(s, args.limit, args.fulltext_cap, args.backfill_months))
        return 0

    from src.common.http import SourceBlocked

    from .store import store_source

    failed = []
    for s in sources:
        try:
            res = run_source(s, args.limit, args.fulltext_cap, args.backfill_months)   # network only
            report(res)
            n = store_source(s, res)                                                  # fresh connection
            print(f"   inserted {n} new documents")
        except SourceBlocked as exc:
            print(f"\n== {s}: marked manual ({exc})")   # expected outcome, not a failure
        except Exception as exc:
            print(f"\n== {s}: FAILED {type(exc).__name__}: {exc}", file=sys.stderr)
            failed.append(s)
    if failed:
        print(f"FAILED sources: {', '.join(failed)}", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
