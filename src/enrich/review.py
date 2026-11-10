"""Apply a person's match decisions to core.facility_xref. Usage:
python -m src.enrich.review eval/reports/top5_match_check.csv

The CSV needs columns ccn, source_id, source_key, decision. decision = yes -> 'confirmed', no -> 'rejected',
blank -> unchanged. Confirmed/rejected links are never re-matched by later runs. Re-run `python -m src.score`
afterwards so the 'unconfirmed' flag is recomputed."""
from __future__ import annotations

import csv
import sys

from src.common.db import connect

DECISION = {"yes": "confirmed", "y": "confirmed", "confirmed": "confirmed",
            "no": "rejected", "n": "rejected", "rejected": "rejected"}


def apply(path: str) -> dict:
    out = {"confirmed": 0, "rejected": 0, "skipped": 0, "not_found": []}
    with connect() as conn:
        for r in csv.DictReader(open(path, encoding="utf-8")):
            status = DECISION.get((r.get("decision") or "").strip().lower())
            if not status:
                out["skipped"] += 1
                continue
            n = conn.execute(
                "update core.facility_xref x set review_status = %s from core.facility f "
                "where f.facility_id = x.facility_id and f.ccn = %s and x.source_id = %s and x.source_key = %s",
                (status, r["ccn"].strip(), r["source_id"].strip(), r["source_key"].strip())).rowcount
            if n:
                out[status] += n
            else:
                out["not_found"].append(f"{r['ccn']} {r['source_id']} {r['source_key']}")
        conn.commit()
    return out


if __name__ == "__main__":
    print(apply(sys.argv[1]))
