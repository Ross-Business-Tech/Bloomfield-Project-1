"""Change log for the site: what each daily check changed on the PUBLIC lead list, and which checks ran.

Built by src.pipeline.daily3 and carried in the snapshot under the key "changelog":

    updated_at / updated_et / check_no   "Updated <run time ET> · Daily check #N"
    baseline                             the hand-over snapshot the lead count is compared with (Nov 11, 2026)
    leads, leads_delta                   tile: Leads (change vs the baseline)
    buyers_named, buyers_inferred        tile: Buyers, always two counts
    runs                                 one entry per daily check in the last 30 days: time, result, gates, what was
                                         read, how many stages changed, how many leads left the list (a count only)
    rows                                 property · change · reason code · source · date, for leads on the list today

Rules: public set only (rows are built from snapshot items, which hold public-view fields only). A lead that left the
list is never named: it is a number on its run, and its earlier rows are dropped. Nothing here reads a private file.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

NF = "NOT FOUND"
ET = ZoneInfo("America/New_York")
BASELINE = {"label": "Nov 11, 2026", "date": "2026-11-11", "leads": 485}     # hand-over snapshot (published 485)
KEEP_DAYS = 30
REASONS = {
    "new_lead": "a building met the bar for the lead list",
    "stage_sale_approved": "a public source shows a court order approving the sale",
    "stage_sale_closed": "a public source reports the sale closed",
    "stage_changed": "the distress stage changed",
    "buyer_named": "a public source now names a buyer for this property",
    "buyer_candidate": "a proposed or stalking-horse bidder was named",
    "buyer_changed": "a public source names a different buyer",
    "buyer_inferred_portfolio": "the buyer is known only from a portfolio-wide sale report that does not list this "
                                "property: shown as inferred, not named",
    "buyer_unverified_roundup": "the buyer was read from a multi-property roundup article: shown as unverified",
    "buyer_status_changed": "the buyer's status changed",
}
STAGE_TEXT = {"early": "Early distress", "middle": "Deepening", "late": "Late stage", "sale": "In a sale",
              "done:approved": "Sale approved", "done:closed": "Closed / sold"}
STATUS_TEXT = {"named": "Named", "candidate": "Candidate", "inferred_portfolio": "Inferred · portfolio sale",
               "Unverified": "Unverified", "stale": "Named · older ownership change", "shell": "Shell entity", NF: NF}


def et_text(when: datetime) -> str:
    """'Nov 12, 2026, 9:05 PM ET'."""
    t = when.astimezone(ET)
    return f"{t:%b} {t.day}, {t.year}, {t.hour % 12 or 12}:{t:%M} {'AM' if t.hour < 12 else 'PM'} ET"


def stage_key(item: dict) -> str:
    s = (item.get("sale_status") or {}).get("status")
    return f"{item.get('stage')}:{s}" if s else str(item.get("stage"))


def state_of(snap: dict) -> dict:
    """What the next run compares against (ids, stages, buyers: no names of homes)."""
    items = snap.get("items") or []
    return {"lead_count": len(items), "stages": {r["lead_id"]: stage_key(r) for r in items},
            "buyers": {r["lead_id"]: f"{r.get('buyer') or NF}|{r.get('buyer_status') or NF}" for r in items}}


def without(state: dict | None, hidden_ids) -> dict | None:
    """The same state with confidential exclusions taken out. A past-deal home is removed from EVERY list a run
    compares with (the hand-over baseline included), so it never shows as a change and is never counted as removed."""
    hidden = set(hidden_ids or [])
    if not state or not hidden:
        return state
    out = dict(state)
    for k in ("stages", "buyers"):
        out[k] = {i: v for i, v in (state.get(k) or {}).items() if i not in hidden}
    out["lead_count"] = len(out["stages"])
    if "top20" in out:
        out["top20"] = [i for i in out["top20"] if i not in hidden]
    return out


def _public(url) -> str | None:
    return url if isinstance(url, str) and url.startswith(("http://", "https://")) else None


def _stage_reason(new: str) -> str:
    return {"done:approved": "stage_sale_approved", "done:closed": "stage_sale_closed"}.get(new, "stage_changed")


def _buyer_change(old: str, new: str) -> tuple[str, str]:
    """('reason code', 'change text') for one lead whose 'buyer|status' value changed."""
    on, _, os_ = old.rpartition("|")
    nn, _, ns = new.rpartition("|")
    if ns == "inferred_portfolio":
        return "buyer_inferred_portfolio", f"Buyer status: {STATUS_TEXT.get(os_, os_)} → Inferred · portfolio sale"
    if ns == "Unverified":
        return "buyer_unverified_roundup", f"Buyer status: {STATUS_TEXT.get(os_, os_)} → Unverified"
    if on in ("", NF) and nn not in ("", NF):
        return ("buyer_candidate" if ns == "candidate" else "buyer_named"), f"Buyer: {NF} → {nn}"
    if on != nn:
        return "buyer_changed", f"Buyer: {on or NF} → {nn or NF}"
    return "buyer_status_changed", f"Buyer status: {STATUS_TEXT.get(os_, os_)} → {STATUS_TEXT.get(ns, ns)}"


def rows_between(prev: dict | None, cand: dict, day: str, run_no: int) -> tuple[list[dict], int]:
    """(rows, removed). prev: state_of() of the earlier public list; cand: tonight's snapshot. A lead that left the
    list gives no row, only +1 in `removed`."""
    if not prev:
        return [], 0
    ps, pb = prev.get("stages") or {}, prev.get("buyers") or {}
    rows = []
    for r in cand.get("items") or []:
        lid = r["lead_id"]
        sale = r.get("sale_status") or {}
        first = next((u for u in r.get("sources") or [] if _public(u)), None)
        base = {"d": day, "no": run_no, "id": lid, "p": r.get("property"), "c": r.get("city"), "s": r.get("state")}
        if lid not in ps:
            rows.append(dict(base, change="New on the list", reason="new_lead", src=first))
            continue
        new_stage = stage_key(r)
        if ps[lid] != new_stage:
            rows.append(dict(base, reason=_stage_reason(new_stage), src=_public(sale.get("source_url")) or first,
                             change=f"Stage: {STAGE_TEXT.get(ps[lid], ps[lid])} → {STAGE_TEXT.get(new_stage, new_stage)}"))
        new_buyer = f"{r.get('buyer') or NF}|{r.get('buyer_status') or NF}"
        if lid in pb and pb[lid] != new_buyer:
            reason, change = _buyer_change(pb[lid], new_buyer)
            src = _public(sale.get("source_url")) if reason == "buyer_inferred_portfolio" else None
            rows.append(dict(base, change=change, reason=reason, src=src or first))
    removed = len(set(ps) - {r["lead_id"] for r in cand.get("items") or []})
    return rows, removed


def run_entry(run_no: int, when: datetime, status: str, rows: list[dict], removed: int, gates: dict,
              checked: dict, leads: int) -> dict:
    """One 'Checks run' entry. gates: {name: {pass, detail}} as daily3 builds them (details never hold a term)."""
    return {"no": run_no, "at": when.isoformat(timespec="seconds"), "et": et_text(when), "status": status,
            "leads": leads, "removed": removed, "stages_changed": sum(1 for r in rows if r["reason"].startswith("stage_")),
            "buyers_changed": sum(1 for r in rows if r["reason"].startswith("buyer_")),
            "new": sum(1 for r in rows if r["reason"] == "new_lead"),
            "gates": [{"name": k, "pass": bool(v["pass"]), "detail": str(v["detail"])} for k, v in gates.items()],
            "checked": {k: v for k, v in checked.items() if isinstance(v, (int, float, str))}}


def next_number(history: list[dict]) -> int:
    return max((int(e.get("no") or 0) for e in history), default=0) + 1


def build(history: list[dict], entry: dict, rows: list[dict], cand: dict, when: datetime,
          baseline_leads: int | None = None) -> dict:
    """The snapshot's "changelog" block: earlier runs (last 30 days) + tonight's. history: [{run entry + 'rows'}].
    baseline_leads: size of the hand-over list after confidential exclusions are taken out (default: as published)."""
    since = (when - timedelta(days=KEEP_DAYS)).isoformat()
    past = [e for e in history if str(e.get("at") or "") >= since and e.get("no") != entry["no"]]
    shown = {r["lead_id"] for r in cand.get("items") or []}
    all_rows = [r for e in past for r in e.get("rows") or []] + rows
    all_rows = [r for r in all_rows if r.get("id") in shown]            # a lead that left the list is never named
    all_rows.sort(key=lambda r: (-int(r["no"]), str(r.get("p") or "")))
    groups: dict = {}
    for r in cand.get("items") or []:
        groups[r.get("buyer_group")] = groups.get(r.get("buyer_group"), 0) + 1
    n = len(cand.get("items") or [])
    runs = sorted([{k: v for k, v in e.items() if k != "rows"} for e in past] + [entry], key=lambda e: -int(e["no"]))
    return {"updated_at": when.isoformat(timespec="seconds"), "updated_et": et_text(when), "check_no": entry["no"],
            "baseline": dict(BASELINE, leads=baseline_leads or BASELINE["leads"]),
            "leads": n, "leads_delta": n - (baseline_leads or BASELINE["leads"]),
            "buyers_named": groups.get("confirmed", 0), "buyers_inferred": groups.get("inferred_portfolio", 0),
            "reasons": {k: v for k, v in REASONS.items() if any(r["reason"] == k for r in all_rows)},
            "runs": runs, "rows": all_rows}


def history_after(history: list[dict], entry: dict, rows: list[dict], when: datetime) -> list[dict]:
    """What a live run stores for the next one (30 days, tonight's rows attached to tonight's entry)."""
    since = (when - timedelta(days=KEEP_DAYS)).isoformat()
    keep = [e for e in history if str(e.get("at") or "") >= since and e.get("no") != entry["no"]]
    return keep + [dict(entry, rows=rows)]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def main(argv=None) -> int:
    """python -m src.pipeline.changelog --baseline-from output/site_snapshot.prev.b64
    Writes output/changelog_baseline_state.json (gitignored): ids, stages and buyers of the hand-over list, so the
    first daily check can show what changed since then. Refuses a snapshot whose lead count is not the baseline's."""
    import argparse
    import json

    from src.common.config import OUTPUT_DIR
    from src.web.server import load_snapshot
    ap = argparse.ArgumentParser(prog="python -m src.pipeline.changelog")
    ap.add_argument("--baseline-from", required=True)
    a = ap.parse_args(argv)
    snap = load_snapshot(a.baseline_from)
    st = dict(state_of(snap), label=BASELINE["label"], generated_at=snap.get("generated_at"))
    if st["lead_count"] != BASELINE["leads"]:
        print(f"refused: this snapshot has {st['lead_count']} leads, the baseline has {BASELINE['leads']}")
        return 2
    out = OUTPUT_DIR / "changelog_baseline_state.json"
    out.write_text(json.dumps(st))
    print(f"baseline state: {st['lead_count']} leads, snapshot generated {st['generated_at']} -> {out}")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
