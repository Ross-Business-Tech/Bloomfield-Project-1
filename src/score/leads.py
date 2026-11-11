"""Step 7: scoring (BUILD_SPEC step 7) → app.lead + app.lead_evidence.

A facility becomes a lead when it has at least one DISTRESS signal inside the lead window (market_sale alone
never creates a lead). score = stage + court-sale priority + recency + buyer + buyer chain quality + size fit
+ HUD exit + market-sale bonus; every component is written to score_breakdown.

One bankruptcy family must not flood the top of the list (Genesis' parent entities link to ~200 facilities):
within a family only the best `family_top_n` leads keep the family's full points; the others keep
`family_overflow_weight` of them and get the flag 'family_capped'.
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

from psycopg.types.json import Jsonb

from src.common.bulk import bulk_upsert

from . import rules
from .buyers import Buyer
from .rules import Sig


_PUBLIC_HOSPITAL_BUYER = re.compile(r"hospital (district|authority)|county .*hospital|hospital .*county|"
                                   r"healthcare district|hospital service district", re.I)


@dataclass
class Lead:
    facility_id: str
    stage: str
    distress_type: str
    score: float
    breakdown: dict
    flags: list[str]
    buyer: Buyer
    signal_keys: list[str]
    tier: str = "main"                               # Gate 3 decision 2d: weak early-only leads go to the watch list
    families: dict = field(default_factory=dict)     # family_id -> family-derived points before capping
    fit: bool = False                                # size fits a $3-30M loan (scoring.size_fit)
    severity: tuple = ()                             # (best stage index, number of distress signal types)
    proof: dict = field(default_factory=dict)        # family_id -> strength of the court link (PROOF_RANK)


# Strength of the proof that a bankruptcy case is this building (Gate 3: deal rows order homes by it last).
PROOF_RANK = {"human": 4, "petition": 3, "claims_agent": 3, "cms_operator": 2, "cms_ownership": 1, None: 0}


def score_facility(fac: dict, sigs: list[Sig], buyer: Buyer, today: date,
                   capped_families: frozenset = frozenset()) -> Lead | None:
    current = [s for s in sigs if s.distress and rules.in_lead_window(s, today)]
    if not current:
        return None
    sc = rules.cfg()["scoring"]
    overflow = rules.wave3("family_overflow_weight")

    def factor(s: Sig) -> float:
        return overflow if s.family_id is not None and s.family_id in capped_families else 1.0

    def pts(s: Sig) -> float:
        return s.stage_points() * factor(s)

    best_pts = max(pts(s) for s in current)
    carriers = [s for s in current if abs(pts(s) - best_pts) < 1e-9]
    best = max(carriers, key=lambda s: (rules.STAGES.index(s.stage), s.link_status == "auto",
                                        s.event_date.toordinal() if s.event_date else 0))
    court = [s for s in current if s.signal_type == "court_sale_process"]
    court_pts = max((rules.wave3("court_sale_priority") * s.weight * factor(s) for s in court), default=0.0)
    dated = [s.event_date for s in current if s.event_date]
    latest = max(dated) if dated else None
    size_pts, size_known = rules.size_fit_points(fac.get("units"), fac.get("beds"))
    ctf = rules.cfg()["scoring"].get("care_type_fit") or {}
    care_pts = float(ctf.get("points", 0)) if set(fac.get("care_types") or []) & set(ctf.get("types") or []) else 0.0
    mega = buyer.status != "NOT FOUND" and buyer.mega_platform
    if mega:
        size_pts = 0.0      # a buyer with 100+ homes borrows at platform level, not per building (assumption)
    buyer_pts = rules.buyer_points(buyer.status)
    hospital_buyer = buyer.buyer_type == "hospital/public" or \
        bool(buyer.name and _PUBLIC_HOSPITAL_BUYER.search(buyer.name))
    if buyer.family_id is not None and buyer.family_id in capped_families:
        buyer_pts = round(buyer_pts * overflow, 2)       # a family-wide sale buyer counts fully for top_n leads only
    chain_pts = rules.chain_quality_points(buyer.chain["avg_overall_rating"]) if buyer.chain else 0.0
    hud_pts = float(sc["hud_exit_bonus"]) if buyer.prior_hud else 0.0
    if hospital_buyer:
        # Gate 3 decision 1b: a public hospital district holds the license for Medicaid supplemental payments;
        # it is not a borrower Bloomfield would lend to, so the buyer earns no points (the lead itself stays).
        buyer_pts = chain_pts = hud_pts = 0.0
    elif buyer.buyer_type:
        # Gate 3: full buyer points for operators and PE/investors, low for public companies / REITs, 0 for lenders
        tw = rules.buyer_type_weight(buyer.buyer_type)
        buyer_pts, chain_pts, hud_pts = (round(x * tw, 2) for x in (buyer_pts, chain_pts, hud_pts))
    if mega:
        buyer_pts = 0.0     # Nov 11 (2): a mega-platform buyer earns no buyer-named bonus (lead stays listed, flag kept)
    # Gate 3: a sale that closed more than N months ago is old news for a lender (large penalty)
    pen = rules.cfg()["scoring"].get("closed_sale_penalty") or {"after_months": 12, "points": 60}
    closed_age = rules.months_between(buyer.event_date, today) if (
        buyer.event_date and buyer.status in ("named", "stale", "shell")) else None
    closed_pts = -float(pen["points"]) if (closed_age is not None and closed_age > pen["after_months"]
                                          and best.stage == "sale") else 0.0
    if closed_pts:
        closed_pts -= court_pts            # a sale that closed long ago is no longer an open court sale either
    market = [s for s in sigs if s.signal_type == "market_sale" and rules.in_lead_window(s, today)]
    market_pts = float(rules.wave3("market_sale_bonus")) if market else 0.0
    breakdown = {
        "stage": round(best_pts, 2),
        "stage_from": f"{best.signal_type} ({best.stage}, weight {best.weight:g}"
                      + (f", family capped x{overflow:g}" if factor(best) != 1.0 else "") + ")",
        "court_sale_priority": round(court_pts, 2),
        "recency": rules.recency_points(latest, today),
        "latest_signal_date": latest.isoformat() if latest else None,
        "buyer": buyer_pts,
        "buyer_chain_quality": chain_pts,
        "size_fit": size_pts,
        "care_type_fit": care_pts,
        "hud_exit": hud_pts,
        "market_sale": market_pts,
        "closed_sale_penalty": closed_pts,
    }
    total = round(sum(v for k, v in breakdown.items() if isinstance(v, (int, float))), 2)
    # Gate 3 top-5 check: a bankruptcy link resting on the facility NAME alone (no address / CCN / CMS-operator
    # confirmation) is capped; score_all also keeps it out of the top N.
    name_only = any("court_name_only" in s.flags for s in current if s.family_id is not None)
    if name_only:
        cap = float(rules.court_name_only_cfg()["score_cap"])
        if total > cap:
            breakdown["court_name_only_cap"] = f"{total:g} capped to {cap:g}: court link by name only"
            total = cap
    breakdown["total"] = total
    families = {}
    for s in current:
        if s.family_id is not None:
            fam_pts = s.stage_points() + (rules.wave3("court_sale_priority") * s.weight
                                          if s.signal_type == "court_sale_process" else 0)
            families[s.family_id] = max(families.get(s.family_id, 0), fam_pts)
    if families:
        breakdown["bankruptcy_family"] = sorted({s.family_name for s in current if s.family_id is not None})

    flags = []
    if name_only:
        flags.append("court_name_only")
    if name_only or all(s.link_status == "needs_review" for s in carriers) or \
            (buyer.status != "NOT FOUND" and buyer.link_status == "needs_review"):
        if rules.cfg()["matching"].get("unconfirmed_lead_label", True):
            flags.append("unconfirmed")
    if closed_pts:
        flags.append("sale_closed_over_12m")
    if buyer.status in ("shell", "stale"):
        flags.append(f"buyer_{buyer.status}")
    if buyer.status == "NOT FOUND":
        flags.append("buyer_not_found")
    elif not buyer.chain:
        flags.append("buyer_chain_perf_missing")
    if hospital_buyer:
        flags.append("buyer_hospital_district")
    if buyer.status != "NOT FOUND" and buyer.possible_insider:
        flags.append("buyer_possible_insider")    # same brand as the debtor (inferred)
    if mega:
        flags.append("buyer_mega_platform")
        breakdown["buyer_facilities"] = buyer.facilities
        breakdown["buyer_facilities_basis"] = buyer.facilities_basis
    if buyer.own_operator:
        flags.append("buyer_is_own_operator")
        breakdown["buyer_dropped_own_operator"] = buyer.own_operator   # the home's own operator: not a buyer
    if buyer.dropped_debtor:
        breakdown["buyer_dropped_debtor"] = buyer.dropped_debtor   # named the debtor / its affiliate: never a buyer
    # Wave 3.5 deliverable rule: licensed (private) facts never reach Bloomfield-facing output.
    #   licensed_only    = no distress signal from a public source -> excluded from app.v_lead_list
    #   licensed_private = some licensed content -> public view masks it (public distress types, buyer NOT FOUND
    #                      when the buyer is licensed, no score_breakdown)
    lic = [x for x in sigs if str(x.source_url).startswith("licensed:")]
    licensed_buyer = str(buyer.source_url or "").startswith("licensed:")
    if lic or licensed_buyer:
        flags.append("licensed_private")
        public_sigs = [x for x in sigs if not str(x.source_url).startswith("licensed:")]
        public_types = []
        for x in sorted(public_sigs, key=lambda x: -pts(x)):
            if x.signal_type not in public_types and x.signal_type not in rules.NON_DISTRESS:
                public_types.append(x.signal_type)
        # licensed-only = the public signals alone would not put the home on the main list
        public_hard = set(public_types) & set(rules.hard_events())
        if len(public_types) < rules.wave3("early_main_min_signals") and not public_hard:
            flags.append("licensed_only")
        breakdown["public_distress_type"] = ", ".join(public_types)
        breakdown["public_stage"] = max((x.stage for x in public_sigs), key=rules.STAGES.index, default=None)
        breakdown["licensed_buyer"] = licensed_buyer     # Texas/Wyoming-style public hospital license holder (UPL), not a lender's borrower
    if not size_known:
        flags.append("size_unknown")
    if latest is None:
        flags.append("event_date_unknown")
    if not fac.get("city") or not fac.get("state"):
        flags.append("location_incomplete")
    if fac.get("state_source") == "inferred":
        flags.append("state_inferred")         # state deduced from the city / portfolio, not stated by the source
    for f in ("senior_housing_maybe", "portfolio_link"):
        if f in best.flags:
            flags.append(f)
    for f in ("borrower_name_mismatch", "address_unconfirmed"):   # evidence-level flags from hand-checked public events
        if any(f in s.flags for s in current) and f not in flags:
            flags.append(f)
    if (fac.get("beds") or 0) > 300 and "senior_housing_maybe" in flags:
        flags.append("size_check")              # very large + maybe not senior housing: check by hand (no score change)
    if factor(best) != 1.0 or any(factor(s) != 1.0 for s in court):
        flags.append("family_capped")
    types = []
    for s in sorted(current, key=lambda s: -pts(s)):
        if s.signal_type not in types:
            types.append(s.signal_type)
    # Gate 3 decision 2d: an early-stage-only lead stays on the main list only with >= N different signals
    # Gate 3 recall audit: main list = 3+ different distress signals OR one hard event (bankruptcy, receivership,
    # foreclosure, closure, HUD claim, loan default, court sale, ...). Everything else goes to the watch list.
    tier = "main"
    min_types = rules.wave3("early_main_min_signals")
    distress_types = [t for t in types if t not in rules.NON_DISTRESS]
    hard = sorted(set(distress_types) & set(rules.hard_events()))
    if len(distress_types) < min_types and not hard:
        tier = "watch"
        breakdown["tier_reason"] = (f"{len(distress_types)} signal type(s) and no hard event; main list needs "
                                    f"{min_types}+ signals or one hard event")
    elif hard:
        breakdown["hard_events"] = hard
    proof = {}
    for s in current:
        if s.family_id is not None:
            proof[s.family_id] = max(proof.get(s.family_id, 0), PROOF_RANK.get(s.confirmed_by, 0))
    severity = (max(rules.STAGES.index(s.stage) for s in current), len([t for t in types if t != "market_sale"]))
    return Lead(fac["facility_id"], best.stage, ", ".join(types), total, breakdown, flags, buyer,
                [s.source_key for s in sigs], tier, families, size_pts > 0, severity, proof)


def score_all(facs: dict, sigs_by_fac: dict, buyers: dict, today: date) -> list[Lead]:
    """Two passes: provisional scores, then cap each bankruptcy family to its best `family_top_n` leads."""
    leads = {}
    for fid, sigs in sigs_by_fac.items():
        lead = score_facility(facs[fid], sigs, buyers.get(fid, Buyer()), today)
        if lead:
            leads[fid] = lead
    top_n = rules.wave3("family_top_n")
    members = defaultdict(list)
    for fid, lead in leads.items():
        for fam in lead.families:
            members[fam].append(lead)
    capped = defaultdict(set)
    deal_of = {}                       # facility -> (family_id, rank in family, deal score before capping)
    for fam, lst in members.items():
        # Gate 3: homes in one bankruptcy are not ranked against each other by score; the family is one deal and
        # its homes are ordered by size fit ($3-30M) -> distress severity -> proof strength.
        lst.sort(key=lambda l: (not l.fit, tuple(-x for x in l.severity), -l.proof.get(fam, 0), l.facility_id))
        for l in lst[top_n:]:
            capped[l.facility_id].add(fam)
    for fid, lead in leads.items():
        if lead.families:              # a home in two bankruptcies belongs to the deal that gives it the most points
            fam = max(lead.families, key=lambda f: (lead.families[f], -f))
            deal_of[fid] = (fam, [l.facility_id for l in members[fam]].index(fid) + 1, lead.score,
                            lead.proof.get(fam, 0))
    for fid, fams in capped.items():
        leads[fid] = score_facility(facs[fid], sigs_by_fac[fid], buyers.get(fid, Buyer()), today, frozenset(fams))
    for fid, (fam, rank, deal_score, proof) in deal_of.items():
        leads[fid].breakdown["deal"] = {"family_id": fam, "rank_in_family": rank, "home_score_uncapped": deal_score,
                                        "homes": len(members[fam]), "proof": proof}
    _keep_name_only_out_of_top(leads.values())
    return sorted(leads.values(), key=lambda l: (-l.score, l.facility_id))


def _licensed(url) -> bool:
    return str(url or "").startswith("licensed:")


def attach_public(ranked: list[Lead], facs: dict, sigs_by_fac: dict, hints_by_fac: dict, buyers: dict, resolver,
                  today: date, pub_facs: dict | None = None) -> int:
    """Deliverable rule (Nov 10, 2026): the Bloomfield-facing list shows what the PUBLIC sources alone give. For every
    lead with licensed content, the whole list is scored again without licensed signals / buyers (same family caps and
    top-N rules), and the public score, tier, buyer and distress types are stored in score_breakdown['public'].
    A lead whose public twin is missing or not on the main list is 'licensed_only'."""
    for l in ranked:                      # a licensed size alone is licensed content too
        if facs[l.facility_id].get("size_licensed"):
            l.breakdown["size_licensed"] = True
            if "licensed_private" not in l.flags:
                l.flags.append("licensed_private")
    affected = {l.facility_id for l in ranked if "licensed_private" in l.flags}
    facs = pub_facs or facs               # the public twin never sees licensed sizes
    if not affected:
        return 0
    pub_sigs = {fid: [s for s in sigs if not _licensed(s.source_url)] for fid, sigs in sigs_by_fac.items()}
    pub_sigs = {fid: v for fid, v in pub_sigs.items() if v}
    pub_buyers = dict(buyers)
    for fid in affected:
        if _licensed(buyers.get(fid, Buyer()).source_url):
            pub_buyers[fid] = resolver.resolve(fid, [h for h in hints_by_fac.get(fid, []) if not _licensed(h.source_url)],
                                               facs[fid]["canonical_name"], today)
    twins = {l.facility_id: l for l in score_all(facs, pub_sigs, pub_buyers, today)}
    for l in ranked:
        if l.facility_id not in affected:
            continue
        t = twins.get(l.facility_id)
        if t is None or t.tier != "main":
            if "licensed_only" not in l.flags:
                l.flags.append("licensed_only")
            if t is None:
                continue
        elif "licensed_only" in l.flags:
            l.flags.remove("licensed_only")
        b = t.buyer
        found = b.status != "NOT FOUND"
        l.breakdown["public"] = {"score": t.score, "tier": t.tier, "distress_type": t.distress_type, "stage": t.stage,
                                 "buyer": b.name if found else "NOT FOUND", "buyer_status": b.status,
                                 "buyer_source_url": b.source_url if found else None, "buyer_chain": b.chain}
    return len(affected)


def keep_name_only_out_of_public_top(leads: list) -> int:
    """Gate 3 court-only rule on the Bloomfield-facing ranking: the public list hides licensed-only leads and shows
    public twin scores, so a name-only court lead can climb into the public top N even when it is capped internally.
    Its public score is lowered to just under the N-th confirmed public lead. Returns the number capped."""
    n = int(rules.court_name_only_cfg()["keep_out_of_top"])

    def pub_score(l):
        p = l.breakdown.get("public") if "licensed_private" in l.flags else None
        return float(p["score"]) if p else float(l.score)
    visible = [l for l in leads if l.tier == "main" and "licensed_only" not in l.flags]
    ok = sorted((pub_score(l) for l in visible if "court_name_only" not in l.flags), reverse=True)
    if len(ok) < n:
        return 0
    floor, capped = round(ok[n - 1] - 0.01, 2), 0
    for l in visible:
        if "court_name_only" in l.flags and pub_score(l) > floor:
            capped += 1
            if "licensed_private" in l.flags and l.breakdown.get("public"):
                l.breakdown["public"]["score"] = floor
            else:
                l.breakdown["court_name_only_cap"] = (l.breakdown.get("court_name_only_cap", "") +
                                                      f"; kept out of the public top {n} ({l.score:g} -> {floor:g})").lstrip("; ")
                l.score = l.breakdown["total"] = floor
    return capped


def _keep_name_only_out_of_top(leads) -> None:
    """No main-list lead whose court link rests on a name match alone may rank in the top N (Gate 3 top-5 check):
    its score is lowered to just under the N-th confirmed main-list lead when the cap alone is not enough."""
    n = int(rules.court_name_only_cfg()["keep_out_of_top"])
    ok = sorted((l.score for l in leads if l.tier == "main" and "court_name_only" not in l.flags), reverse=True)
    if len(ok) < n:
        return
    floor = round(ok[n - 1] - 0.01, 2)
    for l in leads:
        if "court_name_only" in l.flags and l.score > floor:
            l.breakdown["court_name_only_cap"] = (l.breakdown.get("court_name_only_cap", "") +
                                                  f"; kept out of the top {n} ({l.score:g} -> {floor:g})").lstrip("; ")
            l.score = l.breakdown["total"] = floor


LEAD_COLS = ["facility_id", "distress_type", "stage", "buyer_org_id", "buyer_status", "score", "score_breakdown",
             "flags", "last_updated", "buyer_name", "buyer_source_url", "buyer_chain", "tier"]


def lead_breakdown(l: Lead) -> dict:
    """score_breakdown as stored: score parts + how the buyer was found, typed and dated."""
    b = l.buyer
    bd = dict(l.breakdown)
    if b.status != "NOT FOUND":
        bd["buyer_origin"] = b.origin
        bd["buyer_date"] = b.event_date.isoformat() if b.event_date else None
        bd["buyer_date_basis"] = b.date_basis
        bd["buyer_type"] = b.buyer_type
        bd["buyer_type_basis"] = b.type_basis
        bd["buyer_type_source"] = b.type_source
        if b.parent:
            bd["buyer_parent"] = b.parent
            bd["buyer_parent_basis"] = b.parent_basis
            bd["buyer_entity"] = b.entity
        if b.alternatives:
            bd["other_buyers_seen"] = b.alternatives
    return bd


def lead_row(l: Lead, fac: dict, urls: list[str]) -> dict:
    """A lead in the same shape as an app.v_lead_list row (used for the CSV-only watch list)."""
    b = l.buyer
    found = b.status != "NOT FOUND"
    return {"lead_id": None, "property": fac["canonical_name"], "city": fac["city"], "state": fac["state"],
            "care_types": fac.get("care_types") or [], "units": fac["units"], "beds": fac["beds"],
            "size_licensed": bool(fac.get("size_licensed")),
            "distress_type": l.distress_type, "stage": l.stage, "buyer": b.name if found else "NOT FOUND",
            "buyer_status": b.status, "score": l.score, "score_breakdown": lead_breakdown(l), "flags": l.flags,
            "source_urls": sorted(set(urls)), "facility_id": l.facility_id, "ccn": fac.get("ccn"),
            "unconfirmed": "unconfirmed" in l.flags, "buyer_source_url": b.source_url if found else None,
            "buyer_chain": b.chain, "tier": l.tier}


def write_leads(conn, leads: list[Lead], *, prune: bool) -> dict:
    """Writes the MAIN list to app.lead (Gate 3: the watch list is CSV-only to save database space)."""
    rows = []
    for l in leads:
        b = l.buyer
        bd = lead_breakdown(l)
        rows.append([l.facility_id, l.distress_type, l.stage, b.org_id, b.status, l.score, Jsonb(bd), l.flags,
                     datetime.now(timezone.utc), b.name if b.status != "NOT FOUND" else None,
                     b.source_url if b.status != "NOT FOUND" else None, Jsonb(b.chain) if b.chain else None, l.tier])
    n = bulk_upsert(conn, "app.lead", LEAD_COLS, rows, ["facility_id"])
    ids = {r["facility_id"]: r["lead_id"] for r in conn.execute(
        "select facility_id::text, lead_id::text from app.lead where facility_id = any(%(f)s::uuid[])",
        {"f": [l.facility_id for l in leads]}).fetchall()}
    sig_ids = {r["source_key"]: r["signal_id"] for r in conn.execute(
        "select source_key, signal_id::text from core.signal where facility_id = any(%(f)s::uuid[])",
        {"f": [l.facility_id for l in leads]}).fetchall()}
    conn.execute("delete from app.lead_evidence where lead_id = any(%(l)s::uuid[])", {"l": list(ids.values())})
    ev = [[ids[l.facility_id], sig_ids[k]] for l in leads for k in l.signal_keys if k in sig_ids]
    bulk_upsert(conn, "app.lead_evidence", ["lead_id", "signal_id"], ev, ["lead_id", "signal_id"], update=False)
    pruned = 0
    if prune:
        pruned = conn.execute("delete from app.lead where review_status = 'new' and not (facility_id = any(%(f)s::uuid[]))",
                              {"f": [l.facility_id for l in leads]}).rowcount
    conn.commit()
    return {"leads_upserted": n, "evidence_rows": len(ev), "leads_pruned": pruned}
