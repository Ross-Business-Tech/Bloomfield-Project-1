"""Hand-over files from the built snapshot (public view only): output/final/top20.csv and leads_public.csv.

    python -m src.web.final_files            # after `python -m src.web.publish` (dry run) has rebuilt the snapshot

output/ is gitignored; the files are never committed.
"""
from __future__ import annotations

import csv
import json
import sys

from src.common.config import OUTPUT_DIR

NF = "NOT FOUND"
STAGE = {"early": "Early distress", "middle": "Deepening", "late": "Late stage", "sale": "In a sale"}
BUYER = {"named": "Named", "candidate": "Candidate · not final", "stale": "Named · older ownership change",
         "shell": "Shell entity", "named (undated)": "Named · undated", "inferred_portfolio": "Inferred · portfolio sale"}
COLS = ["rank", "property", "city", "state", "care_type", "units", "beds", "stage", "stage_date", "stage_source", "buyer",
        "buyer_status", "score", "lists", "signals", "flags", "address", "phone", "phone_source", "website",
        "website_source", "source_links", "first_seen", "last_updated", "lead_id"]


def stage_label(r: dict) -> str:
    s = r.get("sale_status")
    if not s:
        return STAGE.get(r.get("stage"), r.get("stage") or NF)
    return ("Closed / sold" if s.get("status") == "closed" else "Sale approved") + (" (portfolio, inferred)" if s.get("portfolio") else "")


def row(r: dict) -> dict:
    c, s = r.get("contact") or {}, r.get("sale_status") or {}
    labels = r.get("source_labels") or []
    links = [f"{labels[i]}: {u}" if i < len(labels) and labels[i] else u for i, u in enumerate(r.get("sources") or [])]
    addr = ", ".join(x for x in (c.get("street"), r.get("city"), " ".join(x for x in (r.get("state"), c.get("zip5")) if x)) if x)
    return {"rank": r["rank"], "property": r["property"], "city": r.get("city") or NF, "state": r.get("state") or NF,
            "care_type": "/".join(r.get("care_types") or []) or NF, "units": r.get("units") or "", "beds": r.get("beds") or "",
            "stage": stage_label(r), "stage_date": (s.get("date") or NF) if s else "", "stage_source": s.get("source_url") or "",
            "buyer": r.get("buyer") or NF, "buyer_status": BUYER.get(r.get("buyer_status"), r.get("buyer_status") or NF),
            "score": r.get("score"), "lists": "/".join(r.get("lists") or []), "signals": "; ".join(r.get("signals") or []),
            "flags": "; ".join(r.get("flags") or []), "address": addr or NF, "phone": c.get("phone") or NF,
            "phone_source": c.get("phone_source") or "", "website": c.get("website") or NF,
            "website_source": c.get("website_source") or "", "source_links": " | ".join(links) or NF,
            "first_seen": r.get("first_seen") or "", "last_updated": r.get("last_updated") or "", "lead_id": r.get("lead_id")}


def main() -> int:
    snap = json.loads((OUTPUT_DIR / "site_snapshot.json").read_text())
    out = OUTPUT_DIR / "final"
    out.mkdir(parents=True, exist_ok=True)
    rows = [row(r) for r in snap["items"]]
    for name, part in (("top20.csv", rows[:20]), ("leads_public.csv", rows)):
        with open(out / name, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=COLS)
            w.writeheader()
            w.writerows(part)
    print(f"final files: top20.csv (20), leads_public.csv ({len(rows)}) -> {out}; snapshot {snap['generated_at']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
