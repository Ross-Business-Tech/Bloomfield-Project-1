"""Static snapshot of the PUBLIC lead list for the website (no database or key at runtime).

    python -m src.web.snapshot [--out output/site_snapshot.json]

Reads app.v_lead_list (the partner-facing view: no licensed-only leads, licensed parts masked) with the local
pipeline connection, leaves out past-deal homes (eval/private/exclude_facilities_PRIVATE.txt), keeps only the
allowlisted fields below, and writes one JSON file (plus CSVs next to it). The file is NOT committed (output/ is
gitignored); it is uploaded to Render as a Secret File (docs/DEPLOY_RENDER.md).

Lists: A = live deals with a named buyer; B = pre-sale distress, no buyer; C = buyers that appear as the named buyer
of 2+ homes in this public list (built from public-view fields only; the fuller repeat-buyer / takeover list uses
internal tables and is not included).
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from src.common.config import OUTPUT_DIR, PROJECT_ROOT

EXCLUDE_FILE = PROJECT_ROOT / "eval" / "private" / "exclude_facilities_PRIVATE.txt"
NF = "NOT FOUND"
# allowlist: nothing else from the view reaches the file (no score_breakdown, no ids, no review fields)
FIELDS = ["property", "city", "state", "care_types", "units", "beds", "distress_type", "stage", "buyer",
          "buyer_status", "score", "flags", "source_urls", "buyer_source_url", "first_seen", "last_updated",
          "score_breakdown"]
# score_breakdown keys the website may show (scores, stage source, dates, buyer type); anything else is dropped,
# e.g. other buyers seen or dropped debtor names
BREAKDOWN_KEYS = ["total", "stage", "buyer", "recency", "size_fit", "court_sale_priority", "closed_sale_penalty",
                  "hud_exit", "market_sale", "buyer_chain_quality", "stage_from", "latest_signal_date", "hard_events",
                  "bankruptcy_family", "buyer_date", "buyer_date_basis", "buyer_type", "buyer_type_source",
                  "buyer_parent", "buyer_parent_basis"]


def buyer_group(r: dict) -> str:
    """Website filter group: confirmed (named in a source), candidate (stalking horse / proposed), inferred (parent
    matched by brand, or a shell / stale buyer), none (NOT FOUND)."""
    st, bd = r.get("buyer_status"), r.get("breakdown") or {}
    if st == "named" and bd.get("buyer_parent_basis") != "brand_match":
        return "confirmed"
    if st == "candidate":
        return "candidate"
    if st in ("named", "shell", "stale"):
        return "inferred"
    return "none"


def item(r: dict, lists: list[str]) -> dict:
    """One lead for the website (public-view fields only)."""
    bd = r.get("breakdown") or {}
    sources = list(dict.fromkeys([u for u in [r.get("buyer_source_url"), *(r.get("source_urls") or [])] if public_link(u)]))
    d = lambda v: str(v)[:10] if v else None       # noqa: E731
    out = {"rank": r["rank"], "property": r["property"], "city": r.get("city"), "state": r.get("state"),
           "care_types": r.get("care_types") or [], "units": r.get("units"), "beds": r.get("beds"),
           "signals": [x.strip() for x in (r.get("distress_type") or "").split(",") if x.strip()],
           "stage": r.get("stage"), "buyer": r.get("buyer") if r.get("buyer") not in (None, NF) else None,
           "buyer_status": r.get("buyer_status") or NF, "buyer_group": buyer_group(r), "score": r.get("score"),
           "flags": r.get("flags") or [], "sources": sources, "lists": lists,
           "breakdown": {k: bd[k] for k in BREAKDOWN_KEYS if k in bd},
           "deal_homes": (bd.get("deal") or {}).get("homes"), "first_seen": d(r.get("first_seen")),
           "last_updated": d(r.get("last_updated"))}
    out["not_found"] = [k for k, v in (("city", out["city"]), ("state", out["state"]), ("units", out["units"] or out["beds"]),
                                       ("care_type", out["care_types"]), ("buyer", out["buyer"]),
                                       ("source", out["sources"])) if not v]
    return out
LEAD_COLS = ["list", "rank", "property", "city", "state", "care_type", "units_or_beds", "signals", "stage", "buyer",
             "buyer_status", "score", "flags", "public_source_link", "not_found"]


def excluded() -> set[str]:
    if not EXCLUDE_FILE.exists():
        return set()
    return {ln.strip() for ln in EXCLUDE_FILE.read_text().splitlines() if ln.strip() and not ln.startswith("#")}


def public_link(url) -> str | None:
    return url if isinstance(url, str) and url.startswith(("http://", "https://")) else None


def lead_row(r: dict, lst: str) -> dict:
    size = r.get("units") or r.get("beds")
    links = [u for u in (r.get("source_urls") or []) if public_link(u)]
    link = public_link(r.get("buyer_source_url")) if lst == "A" else None
    out = {"list": lst, "rank": r["rank"], "property": r["property"], "city": r.get("city") or NF,
           "state": r.get("state") or NF, "care_type": "/".join(r.get("care_types") or []) or NF,
           "units_or_beds": size or NF, "signals": r.get("distress_type") or NF, "stage": r.get("stage") or NF,
           "buyer": r.get("buyer") or NF, "buyer_status": r.get("buyer_status") or NF,
           "score": float(r["score"]) if r.get("score") is not None else None,
           "flags": ";".join(r.get("flags") or []), "public_source_link": link or (links[0] if links else NF)}
    out["not_found"] = ";".join(k for k, v in out.items() if v == NF)
    return out


def build(rows: list[dict], skip: set[str]) -> dict:
    """rows: app.v_lead_list rows in score order (facility_id used only to leave out excluded homes)."""
    kept = []
    for i, r in enumerate(rows, 1):
        if str(r.get("facility_id")) in skip:
            continue
        x = {k: r.get(k) for k in FIELDS}
        x["breakdown"] = {k: v for k, v in (x.pop("score_breakdown") or {}).items() if k in BREAKDOWN_KEYS}
        x["rank"] = i
        x["score"] = float(x["score"]) if x["score"] is not None else None
        x["source_urls"] = [u for u in (x["source_urls"] or []) if public_link(u)]
        x["buyer_source_url"] = public_link(x["buyer_source_url"])
        kept.append(x)
    a = [lead_row(r, "A") for r in kept if r["buyer_status"] == "named"
         and "sale_closed_over_12m" not in (r["flags"] or [])]
    b = [lead_row(r, "B") for r in kept if r["buyer"] == NF and r["stage"] != "sale"]
    by_buyer = defaultdict(list)
    for r in kept:
        if r["buyer_status"] == "named" and r["buyer"] and r["buyer"] != NF:
            by_buyer[r["buyer"]].append(r)
    c = []
    for buyer, homes in sorted(by_buyer.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        if len(homes) < 2:
            continue
        link = next((h["buyer_source_url"] for h in homes if h["buyer_source_url"]), None) or \
            next((h["source_urls"][0] for h in homes if h["source_urls"]), None)
        c.append({"list": "C", "rank": len(c) + 1, "buyer": buyer, "homes_in_list": len(homes),
                  "example_homes": "; ".join(f"{h['property']} ({h['city'] or NF}, {h['state'] or NF})" for h in homes[:3]),
                  "public_source_link": link or NF})
    in_a = {r["rank"] for r in a}
    in_b = {r["rank"] for r in b}
    items = [item(r, [x for x, ok in (("A", r["rank"] in in_a), ("B", r["rank"] in in_b)) if ok]) for r in kept]
    return {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "items": items,
            "source": "app.v_lead_list (public view)", "counts": {"leads": len(kept), "list_a": len(a),
                                                                   "list_b": len(b), "list_c": len(c)},
            "leads": [lead_row(r, "all") for r in kept], "list_a": a, "list_b": b, "list_c": c,
            "top20": [lead_row(r, "top20") for r in kept[:20]]}


def write(snap: dict, out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(snap, ensure_ascii=False, default=str))
    for key, cols in (("list_a", LEAD_COLS), ("list_b", LEAD_COLS),
                      ("list_c", ["list", "rank", "buyer", "homes_in_list", "example_homes", "public_source_link"])):
        with open(out.with_name(f"site_{key}.csv"), "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
            w.writeheader()
            w.writerows(snap[key])


def main(argv=None) -> int:
    from src.common.db import connect
    ap = argparse.ArgumentParser(prog="python -m src.web.snapshot")
    ap.add_argument("--out", type=Path, default=OUTPUT_DIR / "site_snapshot.json")
    a = ap.parse_args(argv)
    with connect() as conn:
        rows = [dict(r) for r in conn.execute("select * from app.v_lead_list order by score desc nulls last").fetchall()]
    snap = build(rows, excluded())
    write(snap, a.out)
    print(f"snapshot: {snap['counts']} -> {a.out} (gitignored; upload as a Render Secret File)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
