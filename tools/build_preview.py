"""Build a clickable PREVIEW of the Deal Radar website with MADE-UP sample data (no database, no password, no real lead).

    python3 tools/build_preview.py            # writes preview-dist/ (gitignored)
    npm run preview:radar                     # builds it and publishes it to here.now as a test link

Every building, phone number and link below is invented (phones use the reserved 555-01xx range, links use example.org).
The real site (src/web/server.py) shows the real snapshot instead. This preview is only for looking at design and layout.
"""
from __future__ import annotations

import json
import random
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.web import snapshot  # noqa: E402

OUT = ROOT / "preview-dist"
STATIC = ROOT / "src" / "web" / "static"
STATES = ["TX", "FL", "OH", "PA", "MI", "NY", "IL", "NC", "GA", "CA", "AZ", "IN", "WI", "MO", "TN", "VA", "NJ", "MA", "WA", "CO"]
CITIES = ["Maple Heights", "Lakeview", "Cedar Falls", "Riverton", "Oak Ridge", "Fairmont", "Brookfield", "Hillcrest", "Eastport", "Westbury"]
NAMES = ["Maple Court", "Willow Gardens", "Harbor View", "Evergreen Manor", "Sunrise Terrace", "Heritage Oaks", "Birchwood Commons",
         "Stonebridge", "Lakeside Village", "Cedar Pointe", "Magnolia Place", "Pinecrest", "Autumn Glen", "Silver Creek", "Bayside"]
KINDS = ["Senior Living", "Health & Rehab", "Care Center", "Assisted Living", "Memory Care", "Nursing Center"]
SIGNALS = [["chapter_11", "court_sale_process"], ["receivership"], ["foreclosure", "loan_or_lease_default"], ["hud_loan_terminated"],
           ["warn_closure"], ["closure", "market_sale"], ["special_servicing", "maturity_default"], ["chapter_11"]]
BUYERS = ["Northgate Care Partners (demo)", "Harborline Senior Holdings (demo)", "Blue Ridge Health Group (demo)", "Cobalt Living Capital (demo)"]


def demo_rows(n: int = 64) -> list[dict]:
    rnd = random.Random(7)
    rows = []
    for i in range(n):
        sig = rnd.choice(SIGNALS)
        stage = rnd.choice(["early", "middle", "late", "sale"])
        has_buyer = i % 3 == 0
        rows.append({
            "facility_id": f"demo-{i}", "property": f"{rnd.choice(NAMES)} {rnd.choice(KINDS)} (demo {i + 1})",
            "city": rnd.choice(CITIES) if i % 17 else None, "state": rnd.choice(STATES) if i % 19 else None,
            "care_types": rnd.choice([["SNF"], ["AL"], ["AL", "MC"], ["IL"], ["CCRC"], []]),
            "units": rnd.choice([None, 60, 84, 110, 140]), "beds": rnd.choice([None, 90, 120, 160]),
            "distress_type": ",".join(sig), "stage": stage,
            "buyer": rnd.choice(BUYERS) if has_buyer else "NOT FOUND", "buyer_status": "named" if has_buyer else "NOT FOUND",
            "score": round(95 - i * 1.15 + rnd.random() * 3, 1), "flags": [],
            "source_urls": [f"https://example.org/demo-filing-{i}", f"https://news.example.org/demo-story-{i}"],
            "buyer_source_url": f"https://news.example.org/demo-buyer-{i}" if has_buyer else None,
            "first_seen": "2026-09-14", "last_updated": "2026-11-09",
            "score_breakdown": {"total": 80, "stage": 25, "buyer": 15 if has_buyer else 0, "recency": 20, "size_fit": 10,
                                "court_sale_priority": 20 if "court_sale_process" in sig else 0, "stage_from": "court_filing",
                                "latest_signal_date": "2026-10-30", "hard_events": [x for x in sig if x != "market_sale"][:2]},
        })
    return rows


def demo_contacts(rows: list[dict]) -> dict:
    """Most buildings have a phone; some have a website; a few have nothing (to show NOT FOUND)."""
    out = {}
    for i, r in enumerate(rows):
        if i % 9 == 4:
            continue
        slug = r["property"].lower().split(" (")[0].replace(" ", "-").replace("&", "and")
        out[r["facility_id"]] = {
            "facility_id": r["facility_id"], "street": f"{100 + i * 7} Demo Street", "zip5": f"{10000 + i * 137:05d}",
            "phone": f"555555{100 + i:04d}",          # (555) 555-01xx: the reserved fictional range
            "phone_label": "Main line", "phone_source_url": "https://example.org/demo-cms-listing",
            "website": f"https://www.example.org/{slug}" if i % 3 else None,
            "website_source_url": "https://example.org/demo-listing" if i % 3 else None, "contact_checked": "2026-11-10"}
    return out


def main() -> int:
    from src.common.config import thresholds  # reads config/thresholds.yaml only
    rows = demo_rows()
    snap = snapshot.build(rows, set(), demo_contacts(rows))
    rnd = random.Random(11)
    explore = []
    for i in range(40):
        sig = rnd.sample(["market_sale", "hud_delinquency", "warn_closure", "sff_status", "occupancy_drop"], rnd.choice([1, 2]))
        explore.append({"property": f"{rnd.choice(NAMES)} {rnd.choice(KINDS)} (demo watch {i + 1})", "city": rnd.choice(CITIES),
                        "state": rnd.choice(STATES), "care_types": rnd.choice([["SNF"], ["AL"], ["IL"]]), "units": rnd.choice([None, 70, 100]),
                        "beds": None, "signals": sig, "stage": "early", "buyer": None, "score": round(40 - i * 0.6, 1),
                        "sources": [f"https://example.org/demo-watch-{i}"], "reason": "1 signal type, no hard event (main list needs 3+ or one hard event)"})
    snap["explore"] = explore
    snap["counts"]["explore"] = len(explore)
    c = snap["counts"]
    snap["funnel"] = [
        {"step": "Buildings tracked", "count": 14250, "rule": "Every senior-housing or nursing building found in the public sources, matched into one record."},
        {"step": "With a distress signal", "count": c["leads"] + 7225, "rule": "At least one dated distress signal from a public source in the last 24 months."},
        {"step": "Watch list", "count": 7225, "rule": "Distress below the bar: fewer than 3 signal types and no hard event (see Explore)."},
        {"step": "Main list", "count": 640, "rule": "3+ different distress signal types, or one hard event (bankruptcy, receivership, foreclosure ...)."},
        {"step": "Shown on Deal Radar", "count": c["leads"], "rule": "Main-list leads whose evidence is public, each with its source links."},
        {"step": "List A", "count": c["list_a"], "rule": "Live deal with a buyer named in a source.", "list": "A"},
        {"step": "List B", "count": c["list_b"], "rule": "Distress before any sale; no buyer found yet.", "list": "B"},
        {"step": "List C", "count": c["list_c"], "rule": "Buyers named on 2+ homes in the list.", "list": "C"},
    ]
    snap["sources_table"] = [
        {"source": "CMS skilled-nursing enrollments", "provides": "Every Medicare-certified nursing home", "records": 14250, "unit": "homes", "refresh": "monthly"},
        {"source": "CMS Provider Information", "provides": "Main phone line of each nursing home", "records": 14100, "unit": "phone numbers", "refresh": "monthly"},
        {"source": "CourtListener", "provides": "Federal bankruptcy dockets", "records": 310, "unit": "cases", "refresh": "by hand (daily limit)"},
    ]
    snap["rules"] = snapshot.rules_block() if _has_rules() else {}
    snap["demo"] = True

    if OUT.resolve() != ROOT.resolve() / "preview-dist":
        raise ValueError("Preview output must stay in the project's preview-dist directory")
    if OUT.exists():
        shutil.rmtree(OUT)
    (OUT / "static").mkdir(parents=True)
    (OUT / "api").mkdir()
    shutil.copytree(STATIC / "vendor", OUT / "static" / "vendor")
    for name in ("app.js", "app.css", "states-albers-10m.json"):
        shutil.copy(STATIC / name, OUT / "static" / name)
    keys = ("generated_at", "counts", "summary", "items", "list_c", "explore", "funnel", "sources_table", "rules")
    (OUT / "api" / "snapshot.json").write_text(json.dumps({k: snap.get(k) for k in keys}, ensure_ascii=False, default=str), encoding="utf-8")
    page = (STATIC / "index.html").read_text(encoding="utf-8")
    banner = ('<div style="background:#c9a66b;color:#0b1f3a;text-align:center;font:600 13px Inter,system-ui,sans-serif;padding:7px 12px">'
              'PREVIEW with made-up sample data. Names, phone numbers and links are invented.</div>')
    (OUT / "index.html").write_text(page.replace("<body>", "<body>\n  " + banner, 1), encoding="utf-8")
    print(f"preview built in {OUT} ({len(rows)} sample leads)")
    return 0


def _has_rules() -> bool:
    try:
        from src.common.config import thresholds
        thresholds()["scoring"]["hard_events"]
        return True
    except Exception:
        return False


if __name__ == "__main__":
    sys.exit(main())
