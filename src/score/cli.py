"""python -m src.score [--step signals|buyers|score|view|export|check ...] [--limit N]

Wave 3: signal rules → buyer linking → scoring → app.lead / app.lead_evidence → CSV in output/.
Default runs signals, score (includes buyers), export. `--limit N` runs on a sample of N facilities that have
evidence (writes only those rows; no pruning). `check` fetches 20 lead source URLs (polite, robots obeyed).
Re-running inserts 0 duplicates (everything upserts on source_key / facility_id).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import defaultdict
from datetime import date

from src.common.db import connect

from . import export, leads, signals
from .buyers import BuyerResolver

SIZE_WARN_MB = 400
DEFAULT = ["signals", "score", "export"]


def db_mb(conn) -> float:
    return conn.execute("select pg_database_size(current_database()) / 1048576.0 mb").fetchone()["mb"]


def sample_facilities(conn, n: int) -> list[str]:
    """A sample that covers every kind of evidence: court families, news, HUD claims, 2-CCN, metrics."""
    picks = conn.execute(
        "(select facility_id::text f from core.case_facility order by link_method desc, facility_id limit %(k)s) union "
        "(select facility_id::text from core.facility_xref where source_id in ('shb_rss','snn_rss','shn_rss','warn') limit %(k)s) union "
        "(select facility_id::text from core.loan where status = 'terminated_claim' order by termination_date desc limit %(k)s) union "
        "(select facility_id::text from core.facility_xref where match_method='exact_ccn' group by 1 having count(*) > 1 limit %(k)s) union "
        "(select facility_id::text from core.metric where metric = 'sff_status' limit %(k)s) union "
        "(select facility_id::text from core.ownership_event where event_type='chow' order by effective_date desc limit %(k)s) union "
        "(select facility_id::text from core.metric where metric = 'net_income' and value < 0 limit %(k)s)",
        {"k": max(n // 7, 1)}).fetchall()
    return sorted({r["f"] for r in picks})[:n]


def run_score(conn, sigs, hints, fids, today, prune) -> dict:
    by_fac = defaultdict(list)
    for s in sigs:
        by_fac[s.facility_id].append(s)
    hint_by_fac = defaultdict(list)
    for h in hints:
        hint_by_fac[h.facility_id].append(h)
    facs = {r["facility_id"]: r for r in conn.execute(
        "select facility_id::text, canonical_name, city, state, units, beds, ccn, care_types, state_source from core.facility "
        "where facility_id = any(%(f)s::uuid[])", {"f": list(by_fac)}).fetchall()}
    # Licensed sizes (private tables) help the INTERNAL ranking only: the scored copy of a facility gets the size
    # when no public source has one, and is marked so every Bloomfield-facing output drops it. The public twin
    # (attach_public) scores the facility without it.
    pub_facs = facs
    facs = {fid: dict(f) for fid, f in facs.items()}
    for r in conn.execute(
            "select facility_id::text fid, max(units) units from (select facility_id, units from private.licensed_loan "
            "union all select facility_id, units from private.licensed_fact) x where facility_id = any(%(f)s::uuid[]) "
            "and units is not null group by 1", {"f": list(facs)}).fetchall():
        f = facs.get(r["fid"])
        if f and f["units"] is None and f["beds"] is None:
            f["units"], f["size_licensed"] = int(r["units"]), True
    resolver = BuyerResolver(conn)
    buyers = {fid: resolver.resolve(fid, hint_by_fac.get(fid, []), facs[fid]["canonical_name"], today)
              for fid in by_fac}
    ranked = leads.score_all(facs, by_fac, buyers, today)
    # public twin (no licensed signals / buyers) for leads with licensed content: Bloomfield-facing score and buyer
    leads.attach_public(ranked, facs, by_fac, hint_by_fac, buyers, resolver, today, pub_facs)
    leads.keep_name_only_out_of_public_top(ranked)      # court-only rule on the public ranking too
    main = [l for l in ranked if l.tier == "main"]
    stats = leads.write_leads(conn, main, prune=prune)       # watch list: CSV only (Gate 3, database space)
    urls = {fid: [s.source_url for s in by_fac[fid] if s.source_url] for fid in by_fac}
    watch = [leads.lead_row(l, facs[l.facility_id], urls[l.facility_id]) for l in ranked if l.tier == "watch"]
    stats.update(leads=len(ranked), main=len(main), watch_csv_only=len(watch))
    return stats, watch


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m src.score", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--step", action="append",
                    choices=["signals", "buyers", "score", "view", "export", "summary", "check"])
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args(argv)
    steps = args.step or DEFAULT
    today = date.today()
    with connect() as conn:
        mb = db_mb(conn)
        print(f"database size {mb:.0f} MB")
        if mb > SIZE_WARN_MB:
            print(f"STOP: database is above the {SIZE_WARN_MB} MB warning line; not writing.")
            return 2
        fids = sample_facilities(conn, args.limit) if args.limit else None
        if fids is not None:
            print(f"sample: {len(fids)} facilities")
        sigs = hints = None
        if {"signals", "buyers", "score"} & set(steps):
            t = time.time()
            sigs, hints, st = signals.build(conn, today, fids)
            print(f"built {len(sigs)} signals, {len(hints)} buyer hints in {time.time() - t:.0f}s; {st}")
        if "signals" in steps:
            print("signals:", signals.write_signals(conn, sigs, prune_metrics=fids is None))
        if "buyers" in steps and "score" not in steps:
            named = sum(1 for h in hints if h.status == "named")
            print(f"buyers: {len(hints)} hints ({named} named) on {len({h.facility_id for h in hints})} facilities")
        watch = None
        if "score" in steps:
            st, watch = run_score(conn, sigs, hints, fids, today, prune=fids is None)
            print("score:", st)
        mb = db_mb(conn)
        print(f"database size {mb:.0f} MB")
        if "view" in steps or "summary" in steps:
            print(json.dumps(export.summary(conn), default=str, indent=1))
        if "export" in steps:
            # Bloomfield-facing (deliverable rule: no licensed facts) ...
            path, n = export.export_csv(conn)
            print(f"export: {n} leads -> {path}")
            if watch is None:
                print("export: watch list is CSV-only and needs a score run in the same command; deals/repeat "
                      "buyers use the main list only")
            else:
                pub = [x for x in (export.public_row(dict(r)) for r in watch) if x is not None]
                path, n = export.export_rows(pub, prefix="watch")
                print(f"export: {n} watch-list leads -> {path}")
            path, n = export.export_deals(conn, watch or [])
            print(f"export: {n} deals -> {path}")
            path, n = export.export_repeat_buyers(conn, watch or [])
            print(f"export: top {n} repeat buyers -> {path}")
            # ... and internal (licensed facts included: ranking, backtest; never sent to Bloomfield)
            path, n = export.export_csv(conn, view="app.v_lead_list_internal", prefix="INTERNAL_leads")
            print(f"export (internal): {n} leads -> {path}")
            if watch is not None:
                path, n = export.export_rows(watch, prefix="INTERNAL_watch")
            path, n = export.export_deals(conn, watch or [], internal=True)
            print(f"export (internal): {n} deals -> {path}")
            export.export_repeat_buyers(conn, watch or [], internal=True)
        if "check" in steps:
            res = export.check_urls(conn)
            for r in res:
                print(r["result"], r["status"], r["url"], r.get("note", ""))
            print({k: sum(1 for r in res if r["result"] == k) for k in ("ok", "fail", "blocked", "error")})
    return 0


if __name__ == "__main__":
    sys.exit(main())
