"""Licensed distress / buyer fact rows -> private.licensed_fact (never committed; schema `private` only).

    python -m src.discover.licensed.facts [--file NAME] [--label "..."] [--dry-run]

Input: a gitignored CSV in eval/private/ with one row per fact (debtor, chapter, filed date, court case, property,
city, state, units, care type, distress types, stage, buyer, buyer status, lender, amounts, list change, flags ...).
Personal links in the file (mail / document links) are not stored.

Links (anything unclear stays NOT FOUND = NULL):
- bankruptcy family: the case number matches a docket we hold, else the debtor name is the same party as a family;
- facility: property name + state (+ city) >= 90 on the loader name score (80-89 = unclear -> NOT FOUND); with no
  state, a name-only hit must be >= 95 and the only one; a named property with a known city + state that matches
  nothing becomes a new facility;
- organizations: debtor and buyer linked only when an organization has the same party key (no fuzzy guesses).
Signals and buyer hints are built from this table by src.score.signals.licensed_fact_signals.
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
from collections import Counter

from src.common.config import PROJECT_ROOT
from src.common.normalize import normalize_name

from .loans import _name_score, _nf, roster_aliases

PRIVATE = PROJECT_ROOT / "eval" / "private"
AUTO_AT, NAME_ONLY_AT = 90, 95
COLS = ["fact_key", "article_date", "debtor", "chapter", "filed_date", "court_case", "property_name", "city", "state",
        "units", "care_type", "distress_types", "stage_text", "buyer_name", "buyer_status", "buyer_type", "lender",
        "key_amounts", "key_dates", "fit_note", "list_change", "flags", "facility_id", "family_id", "debtor_org_id",
        "buyer_org_id", "link_method", "link_score", "source_label"]
_STATE = re.compile(r"^[A-Z]{2}$")
_DOCKET = re.compile(r"(\d{2})-(?:bk-)?(\d{4,5})")


def nf(v):
    """_nf plus the file's own NOT FOUND markers (e.g. "NOT FOUND (see docket)", "NOT FOUND (several homes)")."""
    v = _nf(v)
    return None if v is None or v.upper().startswith("NOT FOUND") else v


def _date(v):
    v = nf(v)
    m = re.match(r"(\d{4})-(\d{2})(?:-(\d{2}))?$", v or "")
    if not m:
        return None
    from datetime import date
    return date(int(m[1]), int(m[2]), int(m[3] or 1))


def docket_key(v) -> str | None:
    m = _DOCKET.search(v or "")
    return f"{m[1]}-{m[2].zfill(5)}" if m else None


def care_types(text) -> list[str]:
    t = (text or "").upper()
    out = [c for c in ("IL", "AL", "MC", "SNF") if re.search(rf"\b{c}\b", t)]
    if "CCRC" in t and not out:
        out = ["IL", "AL", "SNF"]
    return out or (["SNF"] if "SNF" in t else ["AL"])


def first_int(v):
    m = re.match(r"\s*(\d+)", nf(v) or "")
    return int(m[1]) if m else None


def _families(conn):
    from src.enrich.steps import party_key
    by_docket, by_name = {}, {}
    for r in conn.execute("select m.family_id, sc.docket_no, sc.debtor_name, sc.case_name from core.case_family_member m "
                          "join stg.court_case sc on sc.id = m.stg_court_case_id").fetchall():
        k = docket_key(r["docket_no"])
        if k:
            by_docket.setdefault(k, r["family_id"])
        for n in (r["debtor_name"], r["case_name"]):     # every debtor in the family (jointly administered cases)
            if party_key(n):
                by_name.setdefault(party_key(n), r["family_id"])
    for r in conn.execute("select family_id, family_name from core.case_family").fetchall():
        by_name.setdefault(party_key(r["family_name"]), r["family_id"])
    return by_docket, by_name


def _orgs(conn):
    from src.enrich.steps import party_key
    out = {}
    for r in conn.execute("select org_id::text, name from core.organization").fetchall():
        k = party_key(r["name"])
        if k:
            out.setdefault(k, r["org_id"])
    return out


def match_facility(all_facs, aliases, name, city, state):
    def score(fac):
        names = {fac["name"]} | aliases.get(str(fac["facility_id"]), set())
        return max(_name_score(name, n) for n in names if n)
    if state:
        cands = [f for f in all_facs if (f["state"] or "") == state]
        if city:
            in_city = [f for f in cands if normalize_name(f["city"] or "") == normalize_name(city)]
            cands = in_city or cands
        scored = sorted(((score(f), f) for f in cands), key=lambda x: -x[0])
        if scored and scored[0][0] >= AUTO_AT:
            return str(scored[0][1]["facility_id"]), "name_city_state", scored[0][0]
        return None, ("unclear" if scored and scored[0][0] >= 80 else "none"), (scored[0][0] if scored else None)
    hits = [(s, f) for f in all_facs if (s := score(f)) >= NAME_ONLY_AT]
    if len(hits) == 1:
        return str(hits[0][1]["facility_id"]), "name_only_unique", hits[0][0]
    return None, ("unclear" if hits else "none"), None


def build_rows(conn, path, label, dry_run):
    from src.enrich.orgs import OrgResolver
    from src.enrich.steps import Batch, load_index, new_facility, party_key
    idx = load_index(conn)
    aliases = roster_aliases(conn)
    all_facs = list(idx.rows.values())
    by_docket, by_name = _families(conn)
    orgs = _orgs(conn)
    batch = Batch(conn, OrgResolver(conn))
    from src.score.signals import _family_case_links          # facility -> the families our court data link it to
    fam_of_fac = {}
    for fam_id, links in _family_case_links(conn).items():
        for l in links:
            if l["review_status"] in ("auto", "confirmed"):
                fam_of_fac.setdefault(l["facility_id"], set()).add(fam_id)
    op_homes = {}                     # party key of a CMS operator / licensee -> {(facility_id, state)}
    for r in conn.execute("select r.facility_id::text fid, f.state, o.name from core.facility_role r "
                          "join core.organization o using (org_id) join core.facility f using (facility_id) "
                          "where r.role in ('operator', 'licensee')").fetchall():
        if party_key(r["name"]):
            op_homes.setdefault(party_key(r["name"]), set()).add((r["fid"], r["state"]))
    with open(path, newline="", encoding="utf-8-sig") as f:
        src = list(csv.DictReader(f))
    rows, stats = [], Counter()
    for i, r in enumerate(src):
        debtor, prop = nf(r.get("company_debtor")), nf(r.get("property_name"))
        state = (nf(r.get("state")) or "").strip()
        state = state if _STATE.match(state) else None          # "XX (region)", "NOT FOUND (region)" -> unclear
        city = nf(r.get("city"))
        city = None if city and " area" in city.lower() else city
        dk = docket_key(r.get("court_case"))
        fam = by_docket.get(dk) if dk else None
        # "Debtor A OpCo LLC (Debtor B OpCo LLC)": try each debtor named
        for d in ([x.strip(" )") for x in re.split(r"\(", debtor)] if debtor else []):
            fam = fam or by_name.get(party_key(d))
        fid, method, score = (None, "none", None)
        if prop:
            fid, method, score = match_facility(all_facs, aliases, prop, city, state)
            # the debtor's family already holds a home in this city under another name ("A & B" short name vs the
            # CMS / roster name): unclear, never a duplicate building
            fam_city = fam and any(fam in fams and (idx.rows.get(f) or {}).get("state") == state and
                                   normalize_name((idx.rows.get(f) or {}).get("city") or "") == normalize_name(city or "")
                                   for f, fams in fam_of_fac.items())
            # the debtor is the Medicare / state operator of exactly one home in that state: that home
            if not fid and debtor and state:
                homes = {h for d in [x.strip(" )") for x in re.split(r"\(", debtor)]
                         for h in op_homes.get(party_key(d), ()) if h[1] == state}
                if len({h[0] for h in homes}) == 1:
                    fid, method, score = next(iter(homes))[0], "cms_operator", None
            if not fid and fam_city:
                method = "unclear"
            if not fid and method == "none" and city and state and not dry_run:
                fid = new_facility(batch, idx, name=prop, city=city, state=state,
                                   care_types=care_types(r.get("care_type")), units=first_int(r.get("units_beds")))
                batch.xrefs.append([fid, "licensed_data", f"fact:{normalize_name(debtor or prop)}:{normalize_name(prop)}",
                                    "manual", None, "auto"])
                method = "new"
        elif fam:
            method = "family_only"
        if not fam and fid and len(fam_of_fac.get(fid, ())) == 1:   # the debtor's own home sits in one family
            fam = next(iter(fam_of_fac[fid]))
        stats[method] += 1
        buyer = nf(r.get("buyer_new_operator"))
        buyer_clean = re.split(r"\s+\(", buyer)[0].strip() if buyer else None
        rows.append({
            "fact_key": f"{normalize_name(debtor or '')}:{normalize_name(prop or '')}:{i}",
            "article_date": _date(r.get("article_date")), "debtor": debtor, "chapter": nf(r.get("chapter")),
            "filed_date": _date(r.get("filed_date")), "court_case": nf(r.get("court_case")),
            "property_name": prop, "city": city, "state": state, "units": first_int(r.get("units_beds")),
            "care_type": nf(r.get("care_type")),
            "distress_types": [d.strip() for d in (r.get("distress_type") or "").split(";") if nf(d)],
            "stage_text": nf(r.get("stage")), "buyer_name": buyer, "buyer_status": nf(r.get("buyer_status")),
            "buyer_type": nf(r.get("buyer_type")), "lender": nf(r.get("lender")), "key_amounts": nf(r.get("key_amounts")),
            "key_dates": nf(r.get("key_dates")), "fit_note": nf(r.get("bloomfield_fit")),
            "list_change": nf(r.get("lead_list_change")),
            "flags": [x.strip() for x in (r.get("flags") or "").split(",") if nf(x) and x.strip().lower() != "none"],
            "facility_id": fid, "family_id": fam, "debtor_org_id": orgs.get(party_key(debtor)) if debtor else None,
            "buyer_org_id": orgs.get(party_key(buyer_clean)) if buyer_clean else None,
            "link_method": method, "link_score": score, "source_label": label})
    if not dry_run:
        batch.flush()
    return rows, stats


def main(argv=None) -> int:
    from src.common.db import connect
    ap = argparse.ArgumentParser(prog="python -m src.discover.licensed.facts")
    ap.add_argument("--file", default="licensed_facts_PRIVATE.csv")
    ap.add_argument("--label", default="licensed bankruptcy research")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    with connect() as conn:
        rows, stats = build_rows(conn, PRIVATE / a.file, a.label, a.dry_run)
        print(f"{a.file}: {len(rows)} rows; facility links {dict(stats)}; "
              f"families {sum(1 for r in rows if r['family_id'])}; debtor orgs {sum(1 for r in rows if r['debtor_org_id'])}; "
              f"buyer orgs {sum(1 for r in rows if r['buyer_org_id'])}")
        if a.dry_run:
            return 0
        conn.execute("delete from private.licensed_fact where source_label = %s", (a.label,))
        ph = ", ".join(f"%({c})s" for c in COLS)
        for r in rows:
            conn.execute(f"insert into private.licensed_fact ({', '.join(COLS)}) values ({ph})", r)
        conn.commit()
    print(f"licensed fact rows loaded: {len(rows)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
