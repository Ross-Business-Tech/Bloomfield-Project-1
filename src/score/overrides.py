"""Hand-check overrides (config/manual_overrides.yaml), applied after the signals and buyer hints are built.

Each override names one building (CMS CCN, or name + city + state), carries a public source_url and a date, and may:
  drop_family: true            drop the building's bankruptcy-case signals and court-sale buyer hints
  zero_signals: [types]        keep those signals as evidence but give them no score weight
  replace_links: {old: new}    replace a source link (prefix match) that belongs to another property
  buyer: {name, status, parent, role}   the buyer a public filing names (replaces every other hint);
  buyer: NOT FOUND             remove a buyer no source supports
  sale_status: approved|closed stage "Sale approved (date)" / "Closed / sold (date)"
  flags: [..]                  copied onto the lead
Nothing here is guessed: an override without a public link and a date is rejected (rules.manual_overrides).
"""
from __future__ import annotations

from datetime import date

from . import rules
from .signals import BuyerHint

FAC: dict = {}          # facility_id -> {"sale_status": {...}, "flags": [...]} from the last apply()


def _facility(conn, m: dict) -> str | None:
    if m.get("ccn"):
        row = conn.execute("select facility_id::text f from core.facility where ccn = %s", (str(m["ccn"]),)).fetchone()
    else:
        row = conn.execute("select facility_id::text f from core.facility where upper(canonical_name) = upper(%s) "
                           "and upper(coalesce(city, '')) = upper(%s) and coalesce(state, '') = %s "
                           "order by facility_id limit 1", (m["name"], m.get("city") or "", m.get("state") or "")).fetchone()
    return row["f"] if row else None


def apply(conn, sigs: list, hints: list, fids=None) -> tuple[list, list, dict]:
    FAC.clear()
    stats = {"applied": 0, "not_matched": []}
    for o in rules.manual_overrides():
        fid = _facility(conn, o["match"])
        if not fid:
            stats["not_matched"].append(str(o["match"]))
            continue
        if fids is not None and fid not in fids:
            continue
        stats["applied"] += 1
        when = date.fromisoformat(str(o["date"]))
        extra = FAC.setdefault(fid, {"flags": []})
        if o.get("drop_family"):
            sigs = [s for s in sigs if not (s.facility_id == fid and s.family_id is not None)]
            hints = [h for h in hints if not (h.facility_id == fid and (h.family_id is not None or h.origin == "court_sale"))]
        for s in sigs:
            if s.facility_id != fid:
                continue
            if s.signal_type in (o.get("zero_signals") or []):
                s.weight = 0.0
                s.evidence_text = (s.evidence_text or "") + " [not scored: the link between this building and the debtor is unverified]"
            for old, new in (o.get("replace_links") or {}).items():
                if str(s.source_url).startswith(old):
                    s.source_url = new
        if "buyer" in o:
            hints = [h for h in hints if h.facility_id != fid]
            b = o["buyer"]
            if isinstance(b, dict):
                hints.append(BuyerHint(fid, b["name"], b.get("status", "named"), b.get("source_url") or o["source_url"],
                                       "manual", date.fromisoformat(str(b["date"])) if b.get("date") else when,
                                       None, "auto", 1.0, None, b.get("parent"), True))
                if b.get("role"):
                    extra["flags"].append("buyer_" + b["role"].replace(" ", "_").replace("-", "_"))
        if o.get("sale_status"):
            extra["sale_status"] = {"status": o["sale_status"], "date": str(o.get("sale_date") or o["date"]),
                                    "source_url": o.get("sale_source_url") or o["source_url"], "basis": "hand check"}
        extra["flags"] += list(o.get("flags") or [])
    return sigs, hints, stats
