"""Source attribution for the published lead list (read-only).

    python -m src.pipeline.source_impact [--snapshot output/site_snapshot.uploaded.b64]

For the leads in the published snapshot, per source:
  first_signal   leads where this source gave the earliest-dated public signal (ties at the same date: every tied
                 source is credited; undated signals only count when a lead has no dated one)
  buyer          leads whose shown buyer comes from this source (app.v_lead_list.buyer_source_url, the public view)
  top20 / top50  rows in the top 20 / 50 that this source touches (a signal, the buyer, or a link the site shows)
  overrides_corrected  of the Nov 11 hand-check overrides (config/manual_overrides.yaml), how many corrected a field
                 that this source had supplied (buyer, sale status, debtor link, a scored signal or a wrong link).
                 Worked out by rebuilding the signals WITHOUT the overrides, in memory, in a read-only session.
Also: leads whose stage label rests on a portfolio-level sale (flag portfolio_sale_inferred).
Decision rule (nightly refresh): a daily3 candidate source with 0 first signals AND 0 buyers is skipped.

Writes output/source_impact.csv and output/source_impact_overrides.csv (gitignored). Licensed tags never count.
Opens the database read-only (SET SESSION ... READ ONLY): any accidental write fails instead of landing.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

from src.common.config import OUTPUT_DIR

from .sources import DAILY3_CANDIDATES, source_of

FIELDS = ["source", "first_signal", "buyer", "top20", "top50", "overrides_corrected", "public_signals",
          "daily3_candidate", "daily3_decision"]


def load_published(path: Path) -> dict:
    from src.web.server import load_snapshot
    return load_snapshot(str(path))


def read_only(conn) -> None:
    conn.execute("set session characteristics as transaction read only")
    conn.commit()
    assert conn.execute("show transaction_read_only").fetchone()["transaction_read_only"] == "on"


def first_sources(sigs: list[dict]) -> set[str]:
    """Sources of the earliest public signal of one lead (all sources tied at that date)."""
    pub = [s for s in sigs if source_of(s["source_url"])]
    if not pub:
        return set()
    dated = [s for s in pub if s["event_date"]]
    if dated:
        first = min(s["event_date"] for s in dated)
        return {source_of(s["source_url"]) for s in dated if s["event_date"] == first}
    first = min(s["created_at"] for s in pub)
    return {source_of(s["source_url"]) for s in pub if s["created_at"] == first}


def norm(name: str | None) -> str:
    import re
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def override_corrections(conn, today: date) -> list[dict]:
    """[{override, field, source}] one row per (override, corrected field, source of the replaced value)."""
    from src.score import overrides, rules, signals
    real_apply = overrides.apply
    overrides.apply = lambda conn, s, h, fids=None: (s, h, {})       # build the pre-override state
    try:
        sigs, hints, _ = signals.build(conn, today)
    finally:
        overrides.apply = real_apply
    by_fac, hint_fac = defaultdict(list), defaultdict(list)
    for s in sigs:
        by_fac[s.facility_id].append(s)
    for h in hints:
        hint_fac[h.facility_id].append(h)
    out = []
    for i, o in enumerate(rules.manual_overrides(), 1):
        fid = overrides._facility(conn, o["match"])
        label = o["match"].get("name") or f"ccn {o['match'].get('ccn')}"
        if not fid:
            out.append({"override": i, "property": label, "field": "-", "source": "(not matched)"})
            continue
        fs, hs = by_fac.get(fid, []), hint_fac.get(fid, [])
        found: set[tuple[str, str]] = set()
        if "buyer" in o:
            b = o["buyer"]
            new = norm(b["name"]) if isinstance(b, dict) else None
            for h in hs:
                if new is None or norm(h.name) != new or (isinstance(b, dict) and h.status != b.get("status", "named")):
                    found.add(("buyer", source_of(h.source_url) or "licensed (not counted)"))
            if not hs:
                found.add(("buyer", "(no prior buyer: gap filled)"))
        if o.get("drop_family"):
            for s in fs:
                if s.family_id is not None:
                    found.add(("debtor link", source_of(s.source_url) or "licensed (not counted)"))
        for s in fs:
            if s.signal_type in (o.get("zero_signals") or []):
                found.add(("scored signal", source_of(s.source_url) or "licensed (not counted)"))
            for old in (o.get("replace_links") or {}):
                if str(s.source_url).startswith(old):
                    found.add(("source link", source_of(s.source_url)))
        if o.get("sale_status"):
            prior = [s for s in fs if s.sale_status]
            wrong = [s for s in prior if s.sale_status != o["sale_status"]]
            for s in wrong:
                found.add(("sale status", source_of(s.source_url) or "licensed (not counted)"))
            if not prior:
                stage_src = {source_of(s.source_url) for s in fs if s.stage == "sale" and source_of(s.source_url)}
                for src in stage_src or {"(no prior stage source)"}:
                    found.add(("sale status", src))      # the stage was 'in a sale' from this source; now approved/closed
        if not found:
            found.add(("flags only", "(no source corrected)"))
        out += [{"override": i, "property": label, "field": f, "source": s} for f, s in sorted(found)]
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m src.pipeline.source_impact", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--snapshot", type=Path, default=OUTPUT_DIR / "site_snapshot.uploaded.b64")
    ap.add_argument("--out", type=Path, default=OUTPUT_DIR / "source_impact.csv")
    a = ap.parse_args(argv)
    from src.common.db import connect
    snap = load_published(a.snapshot)
    items = snap["items"]
    ids = [r["lead_id"] for r in items]
    rank = {r["lead_id"]: r["rank"] for r in items}
    with connect() as conn:
        read_only(conn)
        sig_rows = conn.execute(
            "select e.lead_id::text lead_id, s.source_url, s.event_date, s.created_at from app.lead_evidence e "
            "join core.signal s using (signal_id) where e.lead_id = any(%s::uuid[])", (ids,)).fetchall()
        buyer_rows = conn.execute("select lead_id::text lead_id, buyer_source_url from app.v_lead_list "
                                  "where lead_id = any(%s::uuid[])", (ids,)).fetchall()
        corrections = override_corrections(conn, date.today())
    by_lead = defaultdict(list)
    for r in sig_rows:
        by_lead[r["lead_id"]].append(r)
    first, buyer, sigs_n = Counter(), Counter(), Counter()
    touch: dict[str, set] = defaultdict(set)
    for lid, rows in by_lead.items():
        for src in first_sources(rows):
            first[src] += 1
        for r in rows:
            src = source_of(r["source_url"])
            if src:
                sigs_n[src] += 1
                touch[src].add(lid)
    shown_buyer = {r["lead_id"] for r in items if r.get("buyer")}
    for r in buyer_rows:
        src = source_of(r["buyer_source_url"])
        if src and r["lead_id"] in shown_buyer:
            buyer[src] += 1
            touch[src].add(r["lead_id"])
    for r in items:                                   # links the website shows (incl. extra links from overrides)
        for u in r.get("sources") or []:
            src = source_of(u)
            if src:
                touch[src].add(r["lead_id"])
    corrected = defaultdict(set)
    for c in corrections:
        if not c["source"].startswith("("):
            corrected[c["source"]].add(c["override"])
    n_overrides = len({c["override"] for c in corrections})
    portfolio = sum(1 for r in items if "portfolio_sale_inferred" in (r.get("flags") or []))
    sources = sorted(set(first) | set(buyer) | set(touch) | set(corrected) | set(DAILY3_CANDIDATES),
                     key=lambda s: (-first[s], -buyer[s], s))
    rows = []
    for s in sources:
        cand = s in DAILY3_CANDIDATES
        rows.append({"source": s, "first_signal": first[s], "buyer": buyer[s],
                     "top20": sum(1 for l in touch[s] if rank[l] <= 20),
                     "top50": sum(1 for l in touch[s] if rank[l] <= 50),
                     "overrides_corrected": len(corrected.get(s, ())), "public_signals": sigs_n[s],
                     "daily3_candidate": "yes" if cand else "",
                     "daily3_decision": ("" if not cand else "skip (0 first signals, 0 buyers)"
                                         if first[s] == 0 and buyer[s] == 0 else "keep")})
    a.out.parent.mkdir(parents=True, exist_ok=True)
    with open(a.out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    det = a.out.with_name("source_impact_overrides.csv")
    with open(det, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["override", "property", "field", "source"])
        w.writeheader()
        w.writerows(corrections)
    # 10-line summary
    top = lambda c: ", ".join(f"{k} {v}" for k, v in c.most_common(4))      # noqa: E731
    skipped = [r["source"] for r in rows if r["daily3_decision"].startswith("skip")]
    kept = [r["source"] for r in rows if r["daily3_decision"] == "keep"]
    gap = sum(1 for c in corrections if c["source"].startswith("(no prior"))
    lines = [
        f"1. {len(items)} published leads ({snap.get('generated_at')}); {len(sig_rows)} evidence rows, licensed tags excluded.",
        f"2. First signal (earliest dated public signal): {top(first)}.",
        f"3. Buyer supplied ({sum(buyer.values())} of {len(shown_buyer)} shown buyers): {top(buyer)}.",
        f"4. Top 20 touched: " + ", ".join(f"{r['source']} {r['top20']}" for r in sorted(rows, key=lambda r: -r['top20'])[:5]) + ".",
        f"5. Top 50 touched: " + ", ".join(f"{r['source']} {r['top50']}" for r in sorted(rows, key=lambda r: -r['top50'])[:5]) + ".",
        f"6. {n_overrides} Nov 11 overrides; corrected a field from: " +
        (", ".join(f"{k} {len(v)}" for k, v in sorted(corrected.items(), key=lambda kv: -len(kv[1]))) or "none") +
        f"; {gap} filled a gap no source covered.",
        f"7. Stage labels from portfolio_sale_inferred: {portfolio} of {len(items)} leads.",
        "8. daily3 candidates: " + ", ".join(f"{r['source']} (first {r['first_signal']}, buyer {r['buyer']}, "
                                              f"top20 {r['top20']})" for r in rows if r["daily3_candidate"]) + ".",
        f"9. Decision rule: keep {', '.join(kept) or 'none'}; SKIP {', '.join(skipped) or 'none'}.",
        f"10. Written: {a.out} and {det.name} (gitignored).",
    ]
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
