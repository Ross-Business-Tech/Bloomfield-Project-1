"""Licensed buyer facts -> private.licensed_buyer (never committed; schema `private` only).

    python -m src.discover.licensed.buyers [--dry-run]

Config: eval/private/licensed_buyers_PRIVATE.yaml (gitignored).
- `buyers`: family-level hints (bankruptcy family name -> buyer names; several candidates become one "A / B" hint).
- `sales_files`: per-community buyer CSVs (columns buyer, community, city, state, units, price_usd,
  buyer_type_guess, note). Each community is matched to a facility by name (+ city / state when given, + past
  licence-roster names); a hand-checked `overrides` entry wins. A community with a known state that matches
  nothing becomes a new facility (AL); one with no state that matches nothing is reported and skipped.
  Each matched community gets the buyer (status from the config) and a court-approved sale (`sale_signal`).
0, N/A or empty values = NOT FOUND (NULL).
"""
from __future__ import annotations

import argparse
import csv
import sys
from collections import Counter

import yaml
from rapidfuzz import fuzz

from src.common.config import PROJECT_ROOT
from src.common.normalize import normalize_name

from .loans import AUTO_AT, REVIEW_AT, _name_score, _nf, _num, roster_aliases

PRIVATE = PROJECT_ROOT / "eval" / "private"
FILE = PRIVATE / "licensed_buyers_PRIVATE.yaml"
UPSERT = """insert into private.licensed_buyer (hint_key, family_id, facility_id, buyer_name, status, event_date,
  evidence_text, source_label, community, city, state, price_usd, closing_deadline, buyer_type_guess, match_method,
  match_score, sale_signal) values (%(hint_key)s, %(family_id)s, %(facility_id)s, %(buyer_name)s, %(status)s,
  %(event_date)s, %(evidence_text)s, %(source_label)s, %(community)s, %(city)s, %(state)s, %(price_usd)s,
  %(closing_deadline)s, %(buyer_type_guess)s, %(match_method)s, %(match_score)s, %(sale_signal)s)
  on conflict (hint_key) do update set family_id = excluded.family_id, facility_id = excluded.facility_id,
  buyer_name = excluded.buyer_name, status = excluded.status, event_date = excluded.event_date,
  evidence_text = excluded.evidence_text, source_label = excluded.source_label, community = excluded.community,
  city = excluded.city, state = excluded.state, price_usd = excluded.price_usd,
  closing_deadline = excluded.closing_deadline, buyer_type_guess = excluded.buyer_type_guess,
  match_method = excluded.match_method, match_score = excluded.match_score, sale_signal = excluded.sale_signal,
  loaded_at = now()"""


def _family(conn, name):
    if not name:
        return None
    row = conn.execute("select family_id from core.case_family where family_name = %s", (name,)).fetchone()
    return row["family_id"] if row else None


def _match(conn, idx, aliases, all_facs, r) -> tuple[str | None, str, float | None]:
    name, city, state = r["community"], r["city"], r["state"]
    if state:
        cands = idx.candidates({"state": state, "city": city}) if city else \
            [f for f in all_facs if (f["state"] or "").upper() == state.upper()]
    else:
        cands = all_facs
    best, score = None, 0.0
    for fac in cands:
        names = {fac["name"]} | aliases.get(str(fac["facility_id"]), set())
        s = max(_name_score(name, n) for n in names if n)
        if s > score:
            best, score = fac, s
    if state and city and (best is None or score < REVIEW_AT):
        # city spelled differently ("Lake Town" vs "Town"): retry statewide, name must be near-exact
        for fac in all_facs:
            if (fac["state"] or "").upper() != state.upper():
                continue
            names = {fac["name"]} | aliases.get(str(fac["facility_id"]), set())
            s_ = max(_name_score(name, n) for n in names if n)
            if s_ >= 95 and s_ > score:
                best, score = fac, s_
    if best is None or score < REVIEW_AT:
        return None, "none", None
    if not state:    # name-only across all states: must be a clear, exact-ish name hit
        if score < 95:
            return None, "none", score
    return str(best["facility_id"]), ("fuzzy_auto" if score >= AUTO_AT else "fuzzy_needs_review"), score


def load_sales(conn, cfg: dict, dry_run: bool) -> tuple[list[dict], Counter]:
    from src.enrich.orgs import OrgResolver
    from src.enrich.steps import Batch, load_index, new_facility
    idx = load_index(conn)
    aliases = roster_aliases(conn)
    all_facs = list(idx.rows.values())
    batch = Batch(conn, OrgResolver(conn))
    out, stats = [], Counter()
    with open(PRIVATE / cfg["file"], newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    fam = _family(conn, cfg.get("family"))
    for i, r in enumerate(rows):
        community = _nf(r.get("community"))
        buyer = _nf(r.get("buyer"))
        if not community or not buyer:
            continue
        # "X + others (see APA schedule)": match the named community only
        community_name = community.split(" + ")[0].strip()
        rec = {"community": community_name, "city": _nf(r.get("city")), "state": _nf(r.get("state"))}
        ov = (cfg.get("overrides") or {}).get(community)
        fid, method, score = None, "none", None
        if ov and ov.get("xref"):
            row = conn.execute("select facility_id::text from core.facility_xref where source_id = %s and source_key = %s",
                               tuple(ov["xref"])).fetchone()
            if row:
                fid, method = row["facility_id"], "manual"
        if not fid:
            fid, method, score = _match(conn, idx, aliases, all_facs, rec)
            if method == "fuzzy_needs_review":     # a sale must sit on the right building: no guesses
                fid, method = None, "rejected_needs_review"
        if not fid and rec["state"] and not dry_run:
            fid = new_facility(batch, idx, name=community_name, city=rec["city"], state=rec["state"], care_types=["AL"])
            batch.xrefs.append([fid, "licensed_data", f"{cfg['key']}:{normalize_name(community_name)}", "manual", None, "auto"])
            method = "new"
        stats[method] += 1
        price = _num(r.get("price_usd"))
        note = _nf(r.get("note"))
        out.append({
            "hint_key": f"{cfg['key']}:{normalize_name(community)}:{normalize_name(buyer)}",
            "family_id": fam, "facility_id": fid, "buyer_name": buyer, "status": cfg.get("status", "named"),
            "event_date": cfg.get("event_date"),
            "evidence_text": (f"Court approved the sale of {community} to {buyer} on {cfg.get('event_date')}"
                              + (f" (${price / 1e6:,.2f}M)" if price else "")
                              + f"; closing deadline {cfg.get('closing_deadline')}" + (f"; {note}" if note else "")),
            "source_label": cfg["source_label"], "community": community, "city": rec["city"], "state": rec["state"],
            "price_usd": price, "closing_deadline": cfg.get("closing_deadline"),
            "buyer_type_guess": _nf(r.get("buyer_type_guess")), "match_method": method, "match_score": score,
            "sale_signal": bool(fid)})
    if not dry_run:
        batch.flush()
    return out, stats


def main(argv=None) -> int:
    from src.common.db import connect
    ap = argparse.ArgumentParser(prog="python -m src.discover.licensed.buyers")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    data = yaml.safe_load(FILE.read_text()) or {}
    with connect() as conn:
        rows = []
        for b in data.get("buyers") or []:
            rows.append({"hint_key": b["key"], "family_id": _family(conn, b.get("family")),
                         "facility_id": b.get("facility_id"), "buyer_name": " / ".join(b["names"]),
                         "status": b.get("status", "candidate"), "event_date": b.get("event_date"),
                         "evidence_text": b.get("evidence_text"), "source_label": b["source_label"],
                         "community": None, "city": None, "state": None, "price_usd": None, "closing_deadline": None,
                         "buyer_type_guess": None, "match_method": None, "match_score": None, "sale_signal": False})
        for cfg in data.get("sales_files") or []:
            sales, stats = load_sales(conn, cfg, args.dry_run)
            print(f"{cfg['file']}: {len(sales)} communities, matches {dict(stats)}")
            fac = {r["fid"]: r for r in conn.execute("select facility_id::text fid, canonical_name, city, state "
                                                       "from core.facility where facility_id = any(%s::uuid[])",
                                                       ([s["facility_id"] for s in sales if s["facility_id"]],)).fetchall()}
            for s in sales:
                print(f"   {s['community'][:45]:<45} {s['state'] or '--':<3} -> {s['match_method']:<20} "
                      f"{('%.0f' % s['match_score']) if s['match_score'] else '':>4}  buyer {s['buyer_name'][:30]}"
                      + (f"  = {fac[s['facility_id']]['canonical_name']} ({fac[s['facility_id']]['city']}, "
                         f"{fac[s['facility_id']]['state']})" if s["facility_id"] in fac else ""))
            rows += sales
        if args.dry_run:
            return 0
        keep = [r["hint_key"] for r in rows]
        conn.execute("delete from private.licensed_buyer where not (hint_key = any(%s))", (keep,))
        for r in rows:
            conn.execute(UPSERT, r)
        conn.commit()
    print(f"licensed buyer rows loaded: {len(rows)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
