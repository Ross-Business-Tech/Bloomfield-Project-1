"""List C: repeat buyers, PUBLIC sources only (rebuilt Nov 11, 2026).

    python -m src.score.list_c [--months 36] [--min 2]

A repeat buyer = a firm with >= 2 acquisitions within `months`, each resting on a public web link:
  * Medicare change of ownership (CMS CHOW) and Texas / North Carolina licence changes (core.ownership_event)
  * court sale records (sale order / closed sale, config/court_facts.yaml + court dockets)
  * buyers named in public news extractions
Only 'named' buyers (the sale happened; a stalking horse / proposed buyer is not an acquisition), only confirmed
(auto) facility links, never a licensed row ('licensed:' source), never a past-deal home. One acquisition = one
bankruptcy sale family, else one article / filing, else one home (a CHOW is per home). Left out: hospital / public
and lender buyers, the home's own operator re-licensing itself, and restructurings inside one corporate family
(seller and buyer of the same family). Shell buyers (OpCo / facility-named LLCs) roll up to their parent when a
source states it, else the CMS owner behind the shell, else a same-brand multi-home operator (marked inferred).
Writes output/list_C_repeat_buyers.csv (gitignored) and returns the rows for the website snapshot.
"""
from __future__ import annotations

import argparse
import csv
import sys
from collections import defaultdict
from datetime import date

import re

from src.common.config import OUTPUT_DIR, PROJECT_ROOT
from src.common.normalize import normalize_name

NF = "NOT FOUND"
COLS = ["list", "rank", "buyer", "buyer_type", "parent_basis", "acquisitions_36mo", "homes_in_list", "latest_date",
        "example_homes", "public_source_link", "all_source_links", "origins"]
PERSONAL = re.compile(r"\b(TRUST|TR|TRUSTEE|ESTATE|REVOCABLE|IRREVOCABLE|FAMILY|DECLARATION)\b", re.I)
EXCLUDE_FILE = PROJECT_ROOT / "eval" / "private" / "exclude_facilities_PRIVATE.txt"


def excluded() -> set[str]:
    from src.pipeline import preflight          # env first (CI secret), else the laptop file
    return set(preflight.exclusion_ids()[0])


def merged_count(deals: dict, deal_homes: dict) -> int:
    """Number of acquisitions once deals that share a home are merged (two articles on one sale = one)."""
    groups: list[set] = []
    for k in deals:
        homes = set(deal_homes.get(k, ()))
        hit = [g for g in groups if g & homes]
        for g in hit:
            groups.remove(g)
            homes |= g
        groups.append(homes)
    return len(groups)


def build(conn, today: date | None = None, months: int = 36, min_acq: int = 2) -> tuple[list[dict], dict]:
    from src.enrich.steps import party_key

    from . import rules, signals
    from .buyers import BuyerResolver
    from .takeovers import same_family
    today = today or date.today()
    _, hints, _ = signals.build(conn, today)
    res = BuyerResolver(conn)
    skip = excluded()
    facs = {r["f"]: r for r in conn.execute(
        "select facility_id::text f, canonical_name n, city, state from core.facility").fetchall()}
    sellers = {(r["f"], r["b"], r["d"]): r["seller"] for r in conn.execute(
        "select e.facility_id::text f, e.buyer_org_id::text b, e.effective_date d, o.name seller "
        "from core.ownership_event e join core.organization o on o.org_id = e.seller_org_id "
        "where e.buyer_org_id is not null").fetchall()}
    stats = defaultdict(int)
    firms: dict = {}
    for h in hints:
        why = None
        if h.status != "named":
            why = "not_named"
        elif not signals._http(h.source_url) or h.origin == "licensed":
            why = "not_public"
        elif h.link_status != "auto":
            why = "unconfirmed_link"
        elif not h.event_date or rules.months_between(h.event_date, today) > months:
            why = "undated_or_old"
        elif h.facility_id in skip:
            why = "past_deal_home"
        fac = facs.get(h.facility_id) or {}
        first = h.name.split(";")[0].strip() if h.name else ""
        if not why and res.own_operator(h.facility_id, first, fac.get("n")) and h.origin not in ("chow", "license_change"):
            why = "own_operator"
        seller = sellers.get((h.facility_id, h.org_id, h.event_date))
        if not why and seller and same_family(seller, first):
            why = "same_family_restructuring"
        if why:
            stats[why] += 1
            continue
        parent, basis = h.parent, "source" if h.parent else None
        known = res.known.get(normalize_name(first))
        if not parent and known:
            parent, basis = known.get("parent") or known["name"], "source"
        if not parent:
            org_id, chain_id = res.org_for(first, h.org_id)
            chain = res.chains.get(chain_id) if chain_id else None
            if chain and chain.get("chain_name"):
                parent, basis = chain["chain_name"], "cms_chain"
        if not parent and rules.is_shell_buyer(first, fac.get("n")):
            owner = res.cms_parent(h.facility_id, first)
            if owner and not PERSONAL.search(owner):       # never roll a firm up to a person's family trust
                parent, basis = owner, "cms_ownership"
            elif (brand := res.brand_parent(h.facility_id, first)):
                parent, basis = brand, "brand_match (inferred)"
        display = parent or first
        btype, _ = rules.buyer_type(display, res.known)
        if btype in ("hospital/public", "lender"):
            stats["hospital_or_lender"] += 1
            continue
        f = firms.setdefault(party_key(display), {"buyer": display, "basis": basis or "name", "type": btype,
                                                  "deals": {}, "deal_homes": {}, "homes": {}, "origins": set()})
        dkey = ("fam", h.family_id) if h.family_id is not None else \
            ("fac", h.facility_id) if h.origin in ("chow", "license_change") else ("src", h.source_url)
        d = f["deals"].setdefault(dkey, {"date": h.event_date, "url": h.source_url})
        d["date"] = max(d["date"], h.event_date)
        f["deal_homes"].setdefault(dkey, set()).add(h.facility_id)
        f["homes"].setdefault(h.facility_id, (h.event_date, h.source_url))
        f["origins"].add(h.origin)
        stats["kept_events"] += 1
    for f in firms.values():
        f["n_acq"] = merged_count(f["deals"], f["deal_homes"])
    rows = [f for f in firms.values() if f["n_acq"] >= min_acq]
    rows.sort(key=lambda f: (-f["n_acq"], -len(f["homes"]), -max(d["date"] for d in f["deals"].values()).toordinal(),
                             f["buyer"]))
    out = []
    for i, f in enumerate(rows, 1):
        deals = sorted(f["deals"].values(), key=lambda d: d["date"], reverse=True)
        homes = sorted(f["homes"].items(), key=lambda kv: kv[1][0], reverse=True)
        ex = "; ".join(f"{facs[fid]['n']} ({facs[fid]['city'] or NF}, {facs[fid]['state'] or NF})" for fid, _ in homes[:3])
        out.append({"list": "C", "rank": i, "buyer": f["buyer"], "buyer_type": f["type"] or NF,
                    "parent_basis": f["basis"], "acquisitions_36mo": f["n_acq"], "homes_in_list": len(f["homes"]),
                    "latest_date": deals[0]["date"].isoformat(), "example_homes": ex or NF,
                    "public_source_link": deals[0]["url"],
                    "all_source_links": " | ".join(dict.fromkeys(d["url"] for d in deals)),
                    "origins": ", ".join(sorted(f["origins"]))})
    stats["linked_facility_ids"] = sorted(set().union(*[set(f["homes"]) for f in rows])) if rows else []
    stats["firms_any"] = len(firms)
    stats["repeat_buyers"] = len(out)
    return out, dict(stats)


def main(argv=None) -> int:
    from src.common.db import connect
    ap = argparse.ArgumentParser(prog="python -m src.score.list_c")
    ap.add_argument("--months", type=int, default=36)
    ap.add_argument("--min", type=int, default=2)
    a = ap.parse_args(argv)
    with connect() as conn:
        rows, stats = build(conn, months=a.months, min_acq=a.min)
    path = OUTPUT_DIR / "list_C_repeat_buyers.csv"
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLS)
        w.writeheader()
        w.writerows(rows)
    print(f"List C: {len(rows)} repeat buyers -> {path}")
    print("stats:", stats)
    if len(rows) < 10:
        print(f"FLAG: fewer than 10 repeat buyers ({len(rows)}); not padded.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
