"""Assisted-living takeover list: operator changes on state licence rosters (TX + NC) in the last 24 months.

    python -m src.score.takeovers [--months 24] [--min-beds 20]

One row per home per change (a licensee change and a management-company change seen in the same snapshot are one
row). Left out: same-family restructurings (see same_family) and homes under --min-beds. For each change: old / new
licensee and manager, both detection dates (last snapshot with the old party, first with the new), capacity, and any
distress at the home in the 24 months before the change. Roll-up: top buyers (the new operator = the new management
company when Texas lists one, else the new licensee) by homes taken over, with buyer_type, flagged when the buyer
also appears in a main-list distress lead (as that lead's buyer, or as licensee / manager of a main-list home).
Writes output/al_takeovers_<date>.csv and output/al_top_buyers_<date>.csv (output/ is gitignored).
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
from collections import defaultdict
from datetime import date

from src.common.config import OUTPUT_DIR
from src.enrich.steps import party_key, same_party

from . import rules

# words that say nothing about who the family is
_GENERIC = set("""senior seniors living life care healthcare health opco propco tenant holdings holding management mgmt
manager services service group partners partner company communities community properties property operations
operating operator assisted memory residence residences of at the and texas tx north carolina nc llc inc lp ltd corp
ii iii iv v vi i 1 2 3 4 5 trs sh home homes house village place center centre retirement""".split())
# known corporate families whose entity names share no word (sources: public 10-K / company history)
_FAMILIES = [{"brookdale", "emeritus", "bkd", "esc", "arc"}]
DISTRESS_TYPES = ("closure", "hud_delinquency", "special_servicing", "lender_takeback", "maturity_default",
                  "low_occupancy", "negative_noi", "sff_or_candidate", "staffing_drop", "chapter_11", "chapter_7",
                  "receivership", "foreclosure", "deed_in_lieu", "loan_or_lease_default", "hud_loan_terminated",
                  "warn_closure", "court_sale_process")


def _words(name) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", (name or "").lower()) if w not in _GENERIC and len(w) > 2}


def _initials(name) -> str:
    return "".join(w[0] for w in re.findall(r"[a-z]+", (name or "").lower()) if w not in ("of", "at", "the", "and",
                                                                                          "llc", "inc"))


def same_family(a, b) -> bool:
    """Restructuring inside one corporate family: same party, a shared distinctive word ("Darsea I" ->
    "Darsea Holdings"), an acronym of the other name ("PSL Tanglewood" <- "Pegasus Senior Living"), or a known
    family (Brookdale / Emeritus / BKD)."""
    if not a or not b or same_party(a, b):
        return True
    wa, wb = _words(a), _words(b)
    if wa & wb:
        return True
    ia, ib = _initials(a), _initials(b)
    if (len(ia) >= 3 and ia in {w for w in re.findall(r"[a-z]+", b.lower())}) or \
       (len(ib) >= 3 and ib in {w for w in re.findall(r"[a-z]+", a.lower())}):
        return True
    return any((wa | set(re.findall(r"[a-z]+", a.lower()))) & f and (wb | set(re.findall(r"[a-z]+", b.lower()))) & f
               for f in _FAMILIES)


def build(conn, today: date, months: int = 24, min_beds: int = 20) -> tuple[list[dict], list[dict]]:
    events = conn.execute(
        "select e.facility_id::text fid, e.event_type, e.effective_date d, e.prior_seen_date p, e.source_key, "
        "e.source_url from core.ownership_event e where e.event_type in ('license_change','management_change') "
        "and e.prior_seen_date is not null and split_part(e.source_key, ':', 2) in ('TX','NC')").fetchall()
    snaps = {}
    for r in conn.execute("select state, license_no, as_of, facility_name, licensee, management_co, capacity, city "
                          "from stg.state_license where state in ('TX','NC')").fetchall():
        snaps[(r["state"], r["license_no"], r["as_of"])] = r
    facs = {r["fid"]: r for r in conn.execute(
        "select facility_id::text fid, canonical_name, city, state, coalesce(units, beds) size from core.facility "
        "where facility_id in (select facility_id from core.ownership_event where event_type in "
        "('license_change','management_change'))").fetchall()}
    distress = defaultdict(list)
    for r in conn.execute("select facility_id::text fid, signal_type, event_date from core.signal "
                          "where signal_type = any(%s) and event_date is not null "
                          # Bloomfield-facing: public sources only
                          "and source_url ~ '^https?://'", (list(DISTRESS_TYPES),)).fetchall():
        distress[r["fid"]].append(r)
    rows: dict = {}
    for e in events:
        if rules.months_between(e["d"], today) > months:
            continue
        _, st, lic, _ = e["source_key"].split(":", 3)
        old, new = snaps.get((st, lic, e["p"])), snaps.get((st, lic, e["d"]))
        if not old or not new:
            continue
        size = new["capacity"] or (facs.get(e["fid"]) or {}).get("size")
        if not size or size < min_beds:
            continue
        lic_chg = not same_party(old["licensee"], new["licensee"])
        mgr_chg = not same_party(old["management_co"], new["management_co"])
        if not (lic_chg or mgr_chg):
            continue
        fam = (not lic_chg or same_family(old["licensee"], new["licensee"])) and \
              (not mgr_chg or same_family(old["management_co"], new["management_co"]))
        if fam:
            continue
        k = (e["fid"], e["d"])
        before = sorted({f"{x['signal_type']} {x['event_date']}" for x in distress.get(e["fid"], [])
                         if x["event_date"] <= e["d"] and rules.months_between(x["event_date"], e["d"]) <= 24})
        buyer = new["management_co"] if (new["management_co"] and party_key(new["management_co"])) else new["licensee"]
        rows[k] = {"state": st, "licence_no": lic, "property": new["facility_name"], "city": new["city"],
                   "capacity": size, "old_seen_until": e["p"], "new_seen_from": e["d"],
                   "old_licensee": old["licensee"], "new_licensee": new["licensee"],
                   "old_manager": old["management_co"], "new_manager": new["management_co"],
                   "licensee_changed": lic_chg, "manager_changed": mgr_chg, "buyer": buyer,
                   "distress_before_change": " | ".join(before), "facility_id": e["fid"], "source_url": e["source_url"]}
    takeovers = sorted(rows.values(), key=lambda r: (r["new_seen_from"], r["state"], r["property"] or ""), reverse=True)

    # roll-up by buyer (spelling variants merged with same_party)
    known = {}
    from src.common.normalize import normalize_name
    for b in rules.buyer_orgs():
        known[normalize_name(b["name"])] = b
    groups: list[dict] = []
    for t in takeovers:
        g = next((g for g in groups if same_party(g["buyer"], t["buyer"])), None)
        if g is None:
            g = {"buyer": t["buyer"], "homes": set(), "units": 0, "states": set(), "with_prior_distress": 0,
                 "latest": t["new_seen_from"]}
            groups.append(g)
        if t["facility_id"] not in g["homes"]:
            g["homes"].add(t["facility_id"])
            g["units"] += t["capacity"] or 0
            g["with_prior_distress"] += bool(t["distress_before_change"])
        g["states"].add(t["state"])
        g["latest"] = max(g["latest"], t["new_seen_from"])
    # buyers that also appear in a main-list distress lead
    lead_names = [r["n"] for r in conn.execute(
        # Bloomfield-facing: public view only (licensed-only leads and licensed buyers left out)
        "select distinct buyer n from app.v_lead_list where buyer <> 'NOT FOUND' "
        "union select distinct o.name from app.lead l join core.facility_role fr on fr.facility_id = l.facility_id "
        "join core.organization o on o.org_id = fr.org_id where l.tier = 'main' and not ('licensed_only' = any(l.flags)) "
        "and fr.role in ('licensee','operator','manager') "
        # management company / licensee of main-list homes on the latest state roster snapshot
        "union select distinct coalesce(s.management_co, s.licensee) from app.lead l "
        "join core.facility_xref x on x.facility_id = l.facility_id and x.source_id in ('tx_hhsc','nc_dhsr') "
        "join lateral (select management_co, licensee from stg.state_license s where s.license_no = x.source_key "
        "  and s.state = case x.source_id when 'tx_hhsc' then 'TX' else 'NC' end order by as_of desc limit 1) s on true "
        "where l.tier = 'main' and not ('licensed_only' = any(l.flags))").fetchall() if party_key(r["n"])]
    lead_keys = {party_key(n): n for n in lead_names}
    for g in groups:
        g["n_homes"] = len(g["homes"])
        g["buyer_type"], g["buyer_type_basis"] = rules.buyer_type(g["buyer"], known)
        hit = lead_keys.get(party_key(g["buyer"])) or next((n for n in lead_names if same_party(n, g["buyer"])), None)
        g["also_in_distress_lead"] = hit or ""
    groups.sort(key=lambda g: (-g["n_homes"], -g["units"], g["buyer"]))
    return takeovers, groups


def main(argv=None) -> int:
    from src.common.db import connect
    ap = argparse.ArgumentParser(prog="python -m src.score.takeovers")
    ap.add_argument("--months", type=int, default=24)
    ap.add_argument("--min-beds", type=int, default=20)
    ap.add_argument("--top", type=int, default=20)
    args = ap.parse_args(argv)
    today = date.today()
    with connect() as conn:
        takeovers, groups = build(conn, today, args.months, args.min_beds)
    p1 = OUTPUT_DIR / f"al_takeovers_{today:%Y-%m-%d}.csv"
    p2 = OUTPUT_DIR / f"al_top_buyers_{today:%Y-%m-%d}.csv"
    if takeovers:
        with open(p1, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(takeovers[0]))
            w.writeheader()
            w.writerows(takeovers)
    cols = ["rank", "buyer", "buyer_type", "buyer_type_basis", "n_homes", "units", "states", "with_prior_distress",
            "latest", "also_in_distress_lead"]
    with open(p2, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for i, g in enumerate(groups[:args.top], 1):
            w.writerow([i, g["buyer"], g["buyer_type"], g["buyer_type_basis"], g["n_homes"], g["units"],
                        "/".join(sorted(g["states"])), g["with_prior_distress"], g["latest"], g["also_in_distress_lead"]])
    print(f"takeovers: {len(takeovers)} rows ({len({t['facility_id'] for t in takeovers})} homes), "
          f"{sum(1 for t in takeovers if t['distress_before_change'])} with distress before the change -> {p1}")
    print(f"buyers: {len(groups)} -> top {args.top} in {p2}")
    for i, g in enumerate(groups[:args.top], 1):
        print(f"  {i:>2}. {g['buyer'][:48]:<48} {g['buyer_type']:<14} homes {g['n_homes']:>2}  units {g['units']:>5}  "
              f"{'/'.join(sorted(g['states'])):<5} prior-distress {g['with_prior_distress']}"
              + (f"  [in lead: {g['also_in_distress_lead'][:40]}]" if g["also_in_distress_lead"] else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
