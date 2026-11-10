"""Wave 4 backtest: would the pipeline have caught past deals, and in time?

    python -m eval.backtest [--file eval/private/past_deals.csv] [--out eval/private/backtest_report_PRIVATE.md]

Input (local, gitignored, never committed or loaded into the database): one row per past deal with columns
    name, street, city, state, deal_date          (required; deal_date = YYYY-MM-DD)
    zip, buyer                                    (optional)
Read-only on the database. Licensed signals count (internal ranking; decision Nov 10, 2026).

For each deal:
  1. match the building to core.facility (same scoring as the loaders: name + street + city/zip; >= 90 auto,
     75-89 needs review and reported);
  2. now: main list = caught, watch list = partial (reported, not a pass), neither = missed;
  3. replay at 6 and 12 months before the deal: only signals dated on or before that day, inside the lead window,
     with the main-list rule (3+ distress signal types OR one hard event). Pass mark (decision Nov 10, 2026):
     on the main list 6 months before the deal;
  4. buyer check when the file names one (same_party against our buyer / candidate);
  5. gap analysis for every miss: building missing / not linked / no signal / signals only after the cut-off /
     watch-only.
Limits: metric signals keep only their latest value (Gate 2 retention), so an early metric signal may be missing
from the replay; sources were collected in Nov 2026, so the replay shows what the CURRENT sources would have known.
"""
from __future__ import annotations

import argparse
import csv
import sys
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

from src.common.config import PROJECT_ROOT

DEFAULT_FILE = PROJECT_ROOT / "eval" / "private" / "past_deals.csv"
DEFAULT_OUT = PROJECT_ROOT / "eval" / "private" / "backtest_report_PRIVATE.md"
REQUIRED = ("name", "street", "city", "state", "deal_date")
REPLAY_MONTHS = (6, 12)


def read_deals(path: Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    cols = {c.strip().lower() for c in (rows[0].keys() if rows else [])}
    missing = [c for c in REQUIRED if c not in cols]
    if missing:
        raise SystemExit(f"{path.name}: missing column(s) {missing}; need {list(REQUIRED)} (+ optional zip, buyer)")
    out = []
    for r in rows:
        r = {k.strip().lower(): (v or "").strip() for k, v in r.items()}
        r["deal_date"] = date.fromisoformat(r["deal_date"])
        out.append(r)
    return out


def tier_as_of(sigs: list[dict], as_of: date) -> tuple[str, list[str]]:
    """Main-list rule on the signals known at `as_of` (event_date <= as_of, inside the lead window)."""
    from src.score import rules
    window = rules.wave3("lead_window_months")
    known = [s for s in sigs if s["event_date"] and s["event_date"] <= as_of
             and rules.months_between(s["event_date"], as_of) <= window]
    types = sorted({s["signal_type"] for s in known} - set(rules.NON_DISTRESS))
    if not types:
        return "none", []
    hard = set(types) & set(rules.hard_events())
    return ("main" if hard or len(types) >= rules.wave3("early_main_min_signals") else "watch"), types


def run(path: Path) -> list[dict]:
    from src.common.db import connect
    from src.enrich.steps import load_index, same_party
    deals = read_deals(path)
    results = []
    with connect() as conn:
        idx = load_index(conn)
        leads = {r["fid"]: r for r in conn.execute(
            "select facility_id::text fid, tier, buyer_status, coalesce(buyer_name, '') buyer, score, distress_type "
            "from app.lead").fetchall()}
        for d in deals:
            res = idx.best({"name": d["name"], "street": d["street"], "city": d["city"], "state": d["state"],
                            "zip5": d.get("zip")})
            out = {"deal": d, "match": res.status, "match_score": res.score, "facility_id": res.facility_id}
            if res.status not in ("auto", "needs_review"):
                out.update(now="missed", gap="building not in the facility hub", replay={})
                results.append(out)
                continue
            sigs = [dict(r) for r in conn.execute(
                "select signal_type, event_date, source_url, evidence_text from core.signal where facility_id = %s",
                (res.facility_id,)).fetchall()]
            lead = leads.get(res.facility_id)
            out["now"] = {"main": "caught", "watch": "partial"}.get(lead["tier"], "missed") if lead else \
                ("partial" if sigs else "missed")
            out["lead"] = lead
            out["signals"] = sorted({(s["signal_type"], s["event_date"]) for s in sigs}, key=lambda x: (x[1] or date.min))
            out["replay"] = {m: tier_as_of(sigs, d["deal_date"] - timedelta(days=round(m * 30.44)))
                             for m in REPLAY_MONTHS}
            out["passed"] = out["replay"][6][0] == "main"
            if d.get("buyer") and lead:
                out["buyer_check"] = "match" if lead["buyer"] and same_party(lead["buyer"], d["buyer"]) else \
                    f"ours: {lead['buyer'] or 'NOT FOUND'} ({lead['buyer_status']})"
            if not out["passed"]:
                if not sigs:
                    out["gap"] = "linked, but no signal at all (sources did not cover it)"
                elif all(not s["event_date"] or s["event_date"] > d["deal_date"] - timedelta(days=183) for s in sigs):
                    out["gap"] = "signals exist, but all dated after the 6-month cut-off (found too late)"
                else:
                    out["gap"] = f"only watch-level signals 6 months before ({', '.join(out['replay'][6][1]) or 'none'})"
            results.append(out)
    return results


def report(results: list[dict]) -> str:
    n = len(results)
    passed = sum(1 for r in results if r.get("passed"))
    now = Counter(r["now"] for r in results)
    lines = [f"# Backtest ({date.today()})", "",
             f"**Pass mark (main list 6 months before the deal): {passed}/{n}.** "
             f"Today: caught {now['caught']}, partial (watch) {now['partial']}, missed {now['missed']}.", "",
             "| # | Deal | Deal date | Match | Today | 12 mo before | 6 mo before | Pass | Buyer | Gap |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for i, r in enumerate(results, 1):
        d = r["deal"]
        rp = r.get("replay") or {}
        fmt = lambda m: (f"{rp[m][0]} ({', '.join(rp[m][1])})" if rp.get(m) and rp[m][1] else (rp[m][0] if rp.get(m) else "-"))  # noqa: E731
        lines.append(f"| {i} | {d['name']}, {d['city']} {d['state']} | {d['deal_date']} | {r['match']}"
                     f"{' %.0f' % r['match_score'] if r.get('match_score') else ''} | {r['now']} | {fmt(12)} | {fmt(6)} | "
                     f"{'yes' if r.get('passed') else 'no'} | {r.get('buyer_check', '-')} | {r.get('gap', '')} |")
    lines += ["", "## Signals per deal (type, date)"]
    for i, r in enumerate(results, 1):
        lines.append(f"- {i}. " + ("; ".join(f"{t} {d_}" for t, d_ in r.get("signals", [])) or "none"))
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m eval.backtest")
    ap.add_argument("--file", type=Path, default=DEFAULT_FILE)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    a = ap.parse_args(argv)
    if not a.file.exists():
        print(f"no past-deals file at {a.file}. Put it there (gitignored) with columns {list(REQUIRED)} "
              "(+ optional zip, buyer).", file=sys.stderr)
        return 2
    text = report(run(a.file))
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(text)
    print(text)
    print(f"report written to {a.out} (gitignored)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
