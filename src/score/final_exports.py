"""Final Bloomfield-facing exports (public view only) for the demo.

    python -m src.score.final_exports

Reads app.v_lead_list (no licensed-only leads, no licensed facts) and the public buyer CSVs written by
`python -m src.score` / `python -m src.score.takeovers`. Writes to output/ (gitignored):
  list_A_live_deals.csv        live deals with a named buyer (sale not closed > 12 months ago)
  list_B_presale_distress.csv  distress before any sale, no buyer found yet
  list_C_repeat_buyers.csv     repeat-buyer firms (distress buyers) + assisted-living takeover firms (state rosters)
  top_20_for_handcheck.csv     the 20 highest-ranked public leads with empty check columns
Every row has a public source link; missing facts are written NOT FOUND and listed in `not_found`.
Past-deal / backtest properties (eval/private/exclude_facilities_PRIVATE.txt, if present) are left out.
"""
from __future__ import annotations

import csv
import sys
from datetime import date
from pathlib import Path

from src.common.config import OUTPUT_DIR, PROJECT_ROOT

EXCLUDE_FILE = PROJECT_ROOT / "eval" / "private" / "exclude_facilities_PRIVATE.txt"
NF = "NOT FOUND"
LEAD_COLS = ["list", "rank", "lead_id", "property", "city", "state", "care_type", "units_or_beds", "signals", "stage",
             "buyer", "buyer_status", "score", "flags", "public_source_link", "not_found"]
CHECK_COLS = ["true_owner_parent", "current_lender", "bloomfield_fit", "confidence", "checker", "notes"]
AL_TYPES = {"AL", "IL", "MC", "CCRC"}


def excluded() -> set[str]:
    if not EXCLUDE_FILE.exists():
        return set()
    return {ln.strip() for ln in EXCLUDE_FILE.read_text().splitlines() if ln.strip() and not ln.startswith("#")}


def public_leads(conn) -> list[dict]:
    from src.score.diversity import diversity_order
    rows = [dict(r) for r in conn.execute("select * from app.v_lead_list order by score desc nulls last").fetchall()]
    skip = excluded()
    rows, _ = diversity_order([r for r in rows if str(r["facility_id"]) not in skip])   # display order (buyer cap)
    for i, r in enumerate(rows, 1):
        r["rank"] = i
    return rows


def lead_row(r: dict, lst: str) -> dict:
    size = r.get("units") or r.get("beds")
    link = r.get("buyer_source_url") if lst == "A" and r.get("buyer_source_url") else (r.get("source_urls") or [None])[0]
    out = {"list": lst, "rank": r["rank"], "lead_id": r["lead_id"], "property": r["property"], "city": r["city"] or NF,
           "state": r["state"] or NF, "care_type": "/".join(r.get("care_types") or []) or NF,
           "units_or_beds": size or NF, "signals": r["distress_type"] or NF, "stage": r["stage"] or NF,
           "buyer": r["buyer"] or NF, "buyer_status": r["buyer_status"] or NF, "score": r["score"],
           "flags": ";".join(r.get("flags") or []), "public_source_link": link or NF}
    out["not_found"] = ";".join(k for k, v in out.items() if v == NF)
    return out


def list_a(leads):
    """List A in its own display order (score order with the buyer cap); rank = position in List A."""
    from src.score.diversity import diversity_order, in_list_a
    rows = sorted([r for r in leads if in_list_a(r)], key=lambda r: -(float(r["score"] or 0)))
    rows, _ = diversity_order(rows)
    return [dict(lead_row(r, "A"), rank=i) for i, r in enumerate(rows, 1)]


def list_b(leads):
    rows = sorted([r for r in leads if r["buyer"] == NF and r["stage"] != "sale"], key=lambda r: -(float(r["score"] or 0)))
    return [dict(lead_row(r, "B"), rank=i) for i, r in enumerate(rows, 1)]


def _latest(prefix: str) -> Path | None:
    files = sorted(OUTPUT_DIR.glob(f"{prefix}_*.csv"))
    return files[-1] if files else None


def list_c(skip_names: set[str]) -> list[dict]:
    from src.enrich.steps import same_party
    firms: list[dict] = []

    def add(name, kind, n, home, url):
        if home and any(s and s.upper() in home.upper() for s in skip_names):
            home = None
        f = next((f for f in firms if same_party(f["firm"], name)), None)
        if f is None:
            f = {"firm": name, "repeat_deals": 0, "al_takeover_homes": 0, "homes": [], "links": []}
            firms.append(f)
        f[kind] = max(f[kind], n)
        if home and home not in f["homes"]:
            f["homes"].append(home)
        if url and url not in f["links"]:
            f["links"].append(url)
    p = _latest("repeat_buyers")
    if p:
        for r in csv.DictReader(open(p, encoding="utf-8")):
            add(r["buyer"], "repeat_deals", int(r["deals"] or 0), (r.get("deal_homes") or "").split(";")[0].strip(),
                r.get("source_url"))
    tk = {}
    p = _latest("al_takeovers")
    if p:
        for r in csv.DictReader(open(p, encoding="utf-8")):
            tk.setdefault(r["buyer"], []).append(r)
    p = _latest("al_top_buyers")
    if p:
        for r in csv.DictReader(open(p, encoding="utf-8")):
            homes = tk.get(r["buyer"], [])[:3] or [None]
            for t in homes:
                add(r["buyer"], "al_takeover_homes", int(r["n_homes"] or 0),
                    f"{t['property']} ({t['city']}, {t['state']})" if t else None, t["source_url"] if t else None)
    firms.sort(key=lambda f: -(f["repeat_deals"] + f["al_takeover_homes"]))
    out = []
    for i, f in enumerate(firms, 1):
        row = {"list": "C", "rank": i, "firm": f["firm"], "repeat_deals_24mo": f["repeat_deals"],
               "al_takeover_homes_24mo": f["al_takeover_homes"], "example_homes": "; ".join(f["homes"][:3]) or NF,
               "public_source_link": f["links"][0] if f["links"] else NF}
        row["not_found"] = ";".join(k for k, v in row.items() if v == NF)
        out.append(row)
    return out


def write(name: str, rows: list[dict], cols: list[str]) -> Path:
    path = OUTPUT_DIR / name
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    return path


def numbers(conn, leads, a, b, c) -> dict:
    internal = conn.execute("select count(*) n from app.lead where tier = 'main'").fetchone()["n"]
    al = sum(1 for r in leads if AL_TYPES & set(r.get("care_types") or []))
    return {"public_main_list": len(leads), "internal_main_list": internal, "list_A": len(a), "list_B": len(b),
            "list_C": len(c), "al_share_public": round(al / len(leads), 3) if leads else 0}


def main() -> int:
    from src.common.db import connect
    with connect() as conn:
        leads = public_leads(conn)
        skip_names = {r["canonical_name"] for r in conn.execute(
            "select canonical_name from core.facility where facility_id = any(%s::uuid[])", (list(excluded()),)).fetchall()}
        from src.score import list_c as list_c_public
        a, b = list_a(leads), list_b(leads)
        c, _ = list_c_public.build(conn)          # Nov 11: public-only repeat buyers (36 months, 2+ acquisitions)
        top = [dict(lead_row(r, "top20"), **{k: "" for k in CHECK_COLS}) for r in leads[:20]]
        nums = numbers(conn, leads, a, b, c)
    write("list_A_live_deals.csv", a, LEAD_COLS)
    write("list_B_presale_distress.csv", b, LEAD_COLS)
    from src.score.list_c import COLS as C_COLS
    write("list_C_repeat_buyers.csv", c, C_COLS)
    write("top_20_for_handcheck.csv", top, LEAD_COLS + CHECK_COLS)
    print({**nums, "date": str(date.today())})
    return 0


if __name__ == "__main__":
    sys.exit(main())
