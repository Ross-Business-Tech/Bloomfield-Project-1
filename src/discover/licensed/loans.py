"""Licensed loan exports (shared privately) -> private.licensed_loan, matched to core.facility.

    python -m src.discover.licensed.loans [--dry-run]

Files (local only, gitignored, never committed): eval/private/licensed_hud232_flags_PRIVATE.csv and
eval/private/licensed_loans_PRIVATE.csv; their column headers are mapped in eval/private/licensed_columns.yaml
(template: config/licensed_columns.example.yaml). Rules: 0, N/A or empty = NOT FOUND (NULL). The facts
are stored only in schema `private` (no API access). Facilities are matched by name within state + city (the files
have no street); a property that matches nothing is created as a new facility (care type AL, or MC for an
Alzheimer's / memory-care name), because these are senior-housing loans.
Status history strings have the most recent month on the left.
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
from collections import Counter
from datetime import date, datetime

from rapidfuzz import fuzz

from src.common.config import PROJECT_ROOT
from src.common.normalize import normalize_name

PRIVATE = PROJECT_ROOT / "eval" / "private"
HUD_FILE = PRIVATE / "licensed_hud232_flags_PRIVATE.csv"
LOANS_FILE = PRIVATE / "licensed_loans_PRIVATE.csv"
SOURCE_ID = "licensed_data"
# column headers of the exports: private map (gitignored); the committed example has fake headers
COLUMNS_FILE = PRIVATE / "licensed_columns.yaml"
COLUMNS_EXAMPLE = PROJECT_ROOT / "config" / "licensed_columns.example.yaml"

# Deal rows come from the private files themselves (never named in code: the repo is public): the loans file's
# own `group` column; HUD-file rows in a state with 3+ loans form one "HUD 232 cluster (<state>)" row.
HUD_CLUSTER_MIN = 3
AUTO_AT, REVIEW_AT = 90, 80      # ASSUMPTION: name-only match inside the same city (no street in the files)


def _nf(v):
    """0 / N/A / empty -> None (NOT FOUND)."""
    if v is None:
        return None
    s = str(v).strip()
    return None if s.upper() in ("", "N/A", "NA", "NAN", "0", "0.0", "NONE") else s


def _num(v):
    v = _nf(v)
    try:
        return float(v) if v is not None else None
    except ValueError:
        return None


def _date(v):
    v = _nf(v)
    for fmt in ("%m/%d/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(v, fmt).date() if v else None
        except ValueError:
            pass
    return None


def load_columns(path=None) -> dict:
    """Generic field name -> the export's own column header, per file ('hud232', 'loans'). The real map is private
    (COLUMNS_FILE, gitignored); without it the fake-header example is used, so tests and dry runs still work."""
    import yaml
    p = path or (COLUMNS_FILE if COLUMNS_FILE.exists() else COLUMNS_EXAMPLE)
    with open(p, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _getter(row: dict, cols: dict):
    """g('state') -> the row's value under the mapped header (None when the field or header is not mapped)."""
    return lambda field: _nf(row.get(cols[field])) if cols.get(field) else None


def _units(v):
    n = _num(v)
    return int(n) if n else None


def read_rows(hud_file=None, loans_file=None, columns=None) -> list[dict]:
    hud_file, loans_file = hud_file or HUD_FILE, loans_file or LOANS_FILE
    cmap = columns if columns is not None else load_columns()
    hc, lc = cmap.get("hud232") or {}, cmap.get("loans") or {}
    out = []
    with open(hud_file, newline="", encoding="utf-8-sig") as f:
        hud = [_getter(r, hc) for r in csv.DictReader(f)]
    per_state = Counter((g("state") or "").upper() for g in hud)
    for g in hud:
        name = g("property_name")
        if not name:
            continue
        state = g("state")
        out.append({
            "loan_key": f"lic_hud232:{normalize_name(name)}:{(state or '').upper()}",
            "source_file": hud_file.name, "source_label": (g("source") or "licensed export").strip(),
            "loan_group": (f"HUD 232 cluster ({state.upper()})"
                           if state and per_state[state.upper()] >= HUD_CLUSTER_MIN else None),
            "property_name": name, "city": g("city"), "state": state, "units": _units(g("units")),
            "deal_name": g("deal_name"), "loan_status": None, "orig_balance": None,
            "orig_date": _date(g("orig_date")), "maturity_date": _date(g("maturity_date")),
            "transfer_reason": None, "status_history": g("status_history"),
            "months_delinquent_total": _num(g("months_delinquent_total"))})
    with open(loans_file, newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            g = _getter(r, lc)
            name = g("property_name")
            if not name:
                continue
            state = g("state")
            out.append({
                "loan_key": f"lic_loans:{normalize_name(name)}:{(state or '').upper()}",
                "source_file": loans_file.name, "source_label": (g("source") or "licensed export").strip(),
                "loan_group": g("group"), "property_name": name, "city": g("city"), "state": state,
                "units": _units(g("units")), "deal_name": None, "loan_status": g("loan_status"),
                "orig_balance": _num(g("orig_balance")), "orig_date": _date(g("orig_date")),
                "maturity_date": _date(g("maturity_date")), "transfer_reason": g("transfer_reason"),
                "status_history": g("status_history"), "months_delinquent_total": None})
    return out


_GENERIC_NAME_WORDS = set("the of at and on senior seniors living assisted care center community communities residence "
                          "residences home homes house village place memory health rehab rehabilitation llc inc".split())


def _name_score(a: str, b: str) -> float:
    na, nb = normalize_name(a), normalize_name(b)
    if not na or not nb:
        return 0.0
    # Exported names can be cut off at a fixed length ("SUNNY ACRES ASSISTED LIVING AT"): prefix match counts
    prefix = 100.0 if len(na) >= 12 and (nb.startswith(na[:-2]) or na.startswith(nb)) else 0.0
    # token_set_ratio is 100 whenever one name's words are a subset of the other's ("The Willows" vs "The Gardens at Old Mill
    # Willows"): trust it only when the shorter name has 2+ distinctive words ("Rosewood of Fairview" vs
    # "Rosewood Senior Living of Fairview" is fine)
    short = min((na, nb), key=lambda x: len(x.split()))
    distinctive = [w for w in short.split() if w not in _GENERIC_NAME_WORDS]
    tset = fuzz.token_set_ratio(na, nb) if len(distinctive) >= 2 else 0.0
    return max(tset, fuzz.token_sort_ratio(na, nb), prefix)


def roster_aliases(conn) -> dict[str, set]:
    """Every name a facility ever had on a state licence roster (buildings are renamed after a takeover,
    e.g. "Oak Hollow at Katy" -> "The Pines at Katy", same Texas Facility ID)."""
    aliases: dict[str, set] = {}
    for a in conn.execute("select distinct x.facility_id::text fid, s.facility_name from core.facility_xref x "
                          "join stg.state_license s on s.license_no = x.source_key "
                          "and x.source_id = case s.state when 'TX' then 'tx_hhsc' when 'NC' then 'nc_dhsr' "
                          "when 'MI' then 'mi_lara' when 'NY' then 'ny_doh' when 'IA' then 'ia_dial' "
                          "when 'PA' then 'pa_dhs' end where s.facility_name is not null").fetchall():
        aliases.setdefault(a["fid"], set()).add(a["facility_name"])
    return aliases


def match_all(conn, rows: list[dict], dry_run: bool = False) -> Counter:
    from src.enrich.steps import Batch, load_index, new_facility
    from src.enrich.orgs import OrgResolver
    idx = load_index(conn)
    batch = Batch(conn, OrgResolver(conn))
    stats = Counter()
    aliases = roster_aliases(conn)
    for r in rows:
        cities = {r["city"] or ""}
        cities |= {re.sub(r"\bmount\b", "Mt.", c, flags=re.I) for c in cities} | \
                  {re.sub(r"\bmt\.?(?=\s)", "Mount", c, flags=re.I) for c in cities}
        cands = {str(f["facility_id"]): f for c in cities for f in idx.candidates({"state": r["state"], "city": c})}.values()
        best, score = None, 0.0
        for fac in cands:
            names = {fac["name"]} | aliases.get(str(fac["facility_id"]), set())
            s = max(_name_score(r["property_name"], n) for n in names)
            if s > score:
                best, score = fac, s
        if best is not None and score >= REVIEW_AT:
            r["facility_id"], r["match_score"] = str(best["facility_id"]), score
            r["match_method"] = "fuzzy_auto" if score >= AUTO_AT else "fuzzy_needs_review"
            stats[r["match_method"]] += 1
            continue
        stats["new_facility"] += 1
        if dry_run:
            r["facility_id"], r["match_method"], r["match_score"] = None, "new", None
            continue
        mc = re.search(r"alzheimer|memory", r["property_name"], re.I)
        fid = new_facility(batch, idx, name=r["property_name"], city=r["city"], state=r["state"],
                           care_types=["MC"] if mc else ["AL"], units=r["units"])
        batch.xrefs.append([fid, SOURCE_ID, r["loan_key"], "manual", None, "auto"])
        r["facility_id"], r["match_method"], r["match_score"] = fid, "new", None
    if not dry_run:
        batch.flush()
    return stats


COLS = ["loan_key", "source_file", "source_label", "loan_group", "property_name", "city", "state", "units", "deal_name",
        "loan_status", "orig_balance", "orig_date", "maturity_date", "transfer_reason", "status_history",
        "months_delinquent_total", "facility_id", "match_method", "match_score"]


def load(dry_run: bool = False) -> dict:
    from src.common.bulk import bulk_upsert
    from src.common.db import connect
    rows = read_rows()
    with connect() as conn:
        stats = match_all(conn, rows, dry_run)
        if not dry_run:
            bulk_upsert(conn, "private.licensed_loan", COLS, [[r.get(c) for c in COLS] for r in rows], ["loan_key"])
            conn.commit()
    return {"rows": len(rows), **stats,
            "groups": dict(Counter(r["loan_group"] or "(single)" for r in rows)),
            "matches": [(r["property_name"], r["city"], r["state"], r["match_method"], r["match_score"]) for r in rows]}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m src.discover.licensed.loans")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    res = load(args.dry_run)
    for k, v in res.items():
        if k != "matches":
            print(f"{k}: {v}")
    for m in res["matches"]:
        print("  ", m)
    return 0


if __name__ == "__main__":
    sys.exit(main())
