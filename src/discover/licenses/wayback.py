"""Backfill licence-roster history from Internet Archive captures (Wayback Machine).

    python -m src.discover.licenses.wayback --source nc_dhsr|tx_hhsc [--since YEAR] [--dry-run]

The state files hold only the current roster. Each distinct Wayback capture of the same file URL is parsed
with the state's own parser and saved as one dated snapshot (as_of = capture date, snapshot_origin='wayback',
source_url = the capture URL), so step_licenses can diff licensee / management company over time.
Polite: CDX index once, then the raw capture (id_) per distinct file (collapse=digest), 1 request / 2 s,
every response cached on disk. web.archive.org/robots.txt is a 404 (no rules) as of Nov 10, 2026.
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, datetime

from src.common.http import PoliteClient

from . import north_carolina, texas
from .base import ParseResult, save

PARSERS = {
    "nc_dhsr": (north_carolina.FILE_URL, north_carolina.parse, 2022),
    "tx_hhsc": (texas.FILE_URL, texas.parse, 2021),
}
CDX = "https://web.archive.org/cdx/search/cdx"


def captures(client, url: str, since: int) -> list[tuple[str, str]]:
    """[(timestamp, original_url)] for distinct 200 captures since `since` (one per file content)."""
    resp = client.get(CDX, params={"url": url, "output": "json", "filter": "statuscode:200",
                                   "collapse": "digest", "from": str(since), "fl": "timestamp,original"})
    if resp.status_code != 200:
        raise RuntimeError(f"CDX returned HTTP {resp.status_code}")
    rows = resp.json()
    return [(r[0], r[1]) for r in rows[1:]] if rows else []


def backfill(source_id: str, since: int | None = None, dry_run: bool = False) -> dict:
    url, parse, default_since = PARSERS[source_id]
    since = since or default_since
    out = {"captures": 0, "parsed": 0, "failed": [], "rows": 0}
    results = []
    with PoliteClient("wayback", min_interval=2.0, timeout=180.0) as client:
        caps = captures(client, url, since)
        out["captures"] = len(caps)
        for ts, original in caps:
            cap_url = f"https://web.archive.org/web/{ts}id_/{original}"
            as_of = datetime.strptime(ts[:8], "%Y%m%d").date()
            try:
                resp = client.get(cap_url)
                if resp.status_code != 200:
                    raise RuntimeError(f"HTTP {resp.status_code}")
                rows, total, counts = parse(resp.content, cap_url, as_of)
            except Exception as exc:                 # an old layout we cannot read: report it, never guess
                out["failed"].append(f"{ts}: {type(exc).__name__}: {str(exc)[:120]}")
                continue
            for r in rows:
                r.snapshot_origin = "wayback"
            out["parsed"] += 1
            out["rows"] += len(rows)
            results.append(ParseResult(source_id, cap_url, as_of, "wayback capture date", total, counts, rows, len(rows)))
            print(f"  {as_of}: {len(rows)} rows (licensee filled: {sum(1 for r in rows if r.licensee)})", flush=True)
    if not dry_run and results:
        from src.common.db import connect
        with connect() as conn:
            for res in results:
                save(conn, res)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m src.discover.licenses.wayback")
    ap.add_argument("--source", choices=sorted(PARSERS), required=True)
    ap.add_argument("--since", type=int)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    res = backfill(args.source, args.since, args.dry_run)
    print({k: v for k, v in res.items() if k != "failed"})
    for f in res["failed"]:
        print("  FAILED", f)
    return 0


if __name__ == "__main__":
    sys.exit(main())
