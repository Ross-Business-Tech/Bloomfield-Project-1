"""Static snapshot of the PUBLIC lead list for the website (no database or key at runtime).

    python -m src.web.snapshot [--out output/site_snapshot.json]

Reads app.v_lead_list (the partner-facing view: no licensed-only leads, licensed parts masked) with the local
pipeline connection, leaves out past-deal homes (eval/private/exclude_facilities_PRIVATE.txt), keeps only the
allowlisted fields below, and writes one JSON file (plus CSVs next to it). The file is NOT committed (output/ is
gitignored); it is uploaded to Render as a Secret File (docs/DEPLOY_RENDER.md).

Lists: A = live deals with a named buyer; B = pre-sale distress, no buyer; C = buyers that appear as the named buyer
of 2+ homes in this public list (built from public-view fields only; the fuller repeat-buyer / takeover list uses
internal tables and is not included).
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from src.common.config import OUTPUT_DIR, PROJECT_ROOT

EXCLUDE_FILE = PROJECT_ROOT / "eval" / "private" / "exclude_facilities_PRIVATE.txt"
NF = "NOT FOUND"
# allowlist: nothing else from the view reaches the file (no score_breakdown, no ids, no review fields)
FIELDS = ["lead_id", "property", "city", "state", "care_types", "units", "beds", "distress_type", "stage", "buyer",
          "buyer_status", "score", "flags", "source_urls", "buyer_source_url", "first_seen", "last_updated",
          "score_breakdown", "sale_status", "ccn", "extra_links"]
# score_breakdown keys the website may show (scores, stage source, dates, buyer type); anything else is dropped,
# e.g. other buyers seen or dropped debtor names
BREAKDOWN_KEYS = ["total", "stage", "buyer", "recency", "size_fit", "care_type_fit", "buyer_diversity_cap", "court_sale_priority", "closed_sale_penalty",
                  "hud_exit", "market_sale", "buyer_chain_quality", "stage_from", "latest_signal_date", "hard_events",
                  "bankruptcy_family", "buyer_date", "buyer_date_basis", "buyer_type", "buyer_type_source",
                  "buyer_parent", "buyer_parent_basis"]


def buyer_group(r: dict) -> str:
    """Website filter group: confirmed (named in a source), candidate (stalking horse / proposed), inferred (parent
    matched by brand, or a shell / stale buyer), none (NOT FOUND)."""
    st, bd = r.get("buyer_status"), r.get("breakdown") or {}
    if "(unnamed)" in (r.get("buyer") or "").lower():          # a placeholder, never a named buyer
        return "candidate" if st in ("candidate", "named") else "inferred"
    if st == "named" and bd.get("buyer_parent_basis") != "brand_match":
        return "confirmed"
    if st == "candidate":
        return "candidate"
    if st in ("named", "shell", "stale"):
        return "inferred"
    return "none"


def contact_block(c: dict | None) -> dict:
    """Business contact points for one building (allowlist; nothing personal). Empty values stay None = NOT FOUND."""
    c = c or {}
    phone = re.sub(r"\D", "", str(c.get("phone") or ""))
    web = public_link(c.get("website"))
    street = (c.get("street") or "").strip() or None
    zip5 = (c.get("zip5") or "").strip() or None
    out = {"phone": phone if len(phone) == 10 else None, "phone_label": c.get("phone_label") or None,
           "phone_source": public_link(c.get("phone_source_url")), "website": web,
           "website_source": public_link(c.get("website_source_url")), "street": street, "zip5": zip5,
           "checked": str(c["contact_checked"])[:10] if c.get("contact_checked") else None}
    return out


def item(r: dict, lists: list[str]) -> dict:
    """One lead for the website (public-view fields only)."""
    bd = r.get("breakdown") or {}
    links = source_links(r)
    sources = [x["url"] for x in links]
    sale = r.get("sale_status")
    d = lambda v: str(v)[:10] if v else None       # noqa: E731
    g = r.get("geo") or {}
    out = {"lead_id": str(r["lead_id"]) if r.get("lead_id") else None,
           "lat": round(g["lat"], 5) if g.get("lat") is not None else NF,
           "lon": round(g["lon"], 5) if g.get("lon") is not None else NF,
           "coord_source": g.get("coord_source") or NF,
           "rank": r["rank"], "rank_a": r.get("rank_a"), "rank_b": r.get("rank_b"), "property": r["property"], "city": r.get("city"), "state": r.get("state"),
           "care_types": r.get("care_types") or [], "units": r.get("units"), "beds": r.get("beds"),
           "signals": [x.strip() for x in (r.get("distress_type") or "").split(",") if x.strip()],
           "stage": "done" if sale else r.get("stage"),
           "sale_status": {k: sale.get(k) for k in ("status", "date", "source_url", "portfolio") if sale.get(k) is not None} if sale else None,
           "source_labels": [x["label"] for x in links],
           "buyer": r.get("buyer") if r.get("buyer") not in (None, NF) else None,
           "buyer_status": r.get("buyer_status") or NF, "buyer_group": buyer_group(r), "score": r.get("score"),
           "flags": r.get("flags") or [], "sources": sources, "lists": lists,
           "breakdown": {k: bd[k] for k in BREAKDOWN_KEYS if k in bd},
           "deal_homes": (bd.get("deal") or {}).get("homes"), "first_seen": d(r.get("first_seen")),
           "last_updated": d(r.get("last_updated")), "contact": contact_block(r.get("contact"))}
    out["not_found"] = [k for k, v in (("city", out["city"]), ("state", out["state"]), ("units", out["units"] or out["beds"]),
                                       ("care_type", out["care_types"]), ("buyer", out["buyer"]),
                                       ("source", out["sources"])) if not v]
    return out
def source_links(r: dict) -> list[dict]:
    """Public links in reading order: buyer / sale evidence first, Care Compare instead of raw CMS API links."""
    from src.score import links
    sale = r.get("sale_status") or {}
    first = [u for u in (public_link(r.get("buyer_source_url")), public_link(sale.get("source_url")),
                         *(r.get("extra_links") or [])) if public_link(u)]
    return links.ordered([*(r.get("source_urls") or []), *(r.get("extra_links") or [])], first, r.get("ccn"))


LEAD_COLS = ["list", "rank", "property", "city", "state", "address", "phone", "website", "care_type", "units_or_beds",
             "signals", "stage", "buyer", "buyer_status", "score", "flags", "public_source_link", "not_found"]


def phone_text(digits) -> str | None:
    d = re.sub(r"\D", "", str(digits or ""))
    return f"({d[:3]}) {d[3:6]}-{d[6:]}" if len(d) == 10 else None


def excluded() -> set[str]:
    if not EXCLUDE_FILE.exists():
        return set()
    return {ln.strip() for ln in EXCLUDE_FILE.read_text().splitlines() if ln.strip() and not ln.startswith("#")}


def public_link(url) -> str | None:
    return url if isinstance(url, str) and url.startswith(("http://", "https://")) else None


def lead_row(r: dict, lst: str) -> dict:
    size = r.get("units") or r.get("beds")
    links = [x["url"] for x in source_links(r)]
    link = public_link(r.get("buyer_source_url")) if lst == "A" else None
    from src.score.links import stage_text
    ct = r.get("contact") or {}
    out = {"list": lst, "rank": r["rank"], "property": r["property"], "city": r.get("city") or NF,
           "state": r.get("state") or NF, "address": ", ".join(x for x in (ct.get("street"), ct.get("zip5")) if x) or NF,
           "phone": phone_text(ct.get("phone")) or NF, "website": public_link(ct.get("website")) or NF, "care_type": "/".join(r.get("care_types") or []) or NF,
           "units_or_beds": size or NF, "signals": r.get("distress_type") or NF, "stage": stage_text(r.get("stage"), r.get("sale_status")) or NF,
           "buyer": r.get("buyer") or NF, "buyer_status": r.get("buyer_status") or NF,
           "score": float(r["score"]) if r.get("score") is not None else None,
           "flags": ";".join(r.get("flags") or []), "public_source_link": link or (links[0] if links else NF)}
    out["not_found"] = ";".join(k for k, v in out.items() if v == NF)
    return out


def funnel_problems(snap: dict) -> list[str]:
    """The funnel's last stage must equal the public lead count (counts, items and summary agree)."""
    shown = next((f["count"] for f in snap.get("funnel") or [] if f.get("step") == "Shown on Deal Radar"), None)
    n = len(snap.get("items") or [])
    out = []
    if shown != n:
        out.append(f"funnel 'Shown on Deal Radar' = {shown}, public leads = {n}")
    if (snap.get("counts") or {}).get("leads") != n:
        out.append(f"counts.leads = {(snap.get('counts') or {}).get('leads')}, items = {n}")
    m = snap.get("summary")
    if m and m["list_a"] + m["list_b"] + m["other"] != n:
        out.append(f"summary A + B + other = {m['list_a'] + m['list_b'] + m['other']}, public leads = {n}")
    return out


def other_reason(r: dict) -> str | None:
    """Why a public lead is in neither List A nor List B (None when it is in one of them)."""
    from src.score.diversity import in_list_a
    no_buyer = r.get("buyer") in (None, NF)
    if in_list_a(r) or (no_buyer and r.get("stage") not in ("sale", "done")):
        return None
    st = r.get("buyer_status")
    if st == "candidate":
        return "candidate"                 # stalking-horse / proposed bidder
    if st == "stale":
        return "stale"                     # named, ownership change older than buyer_named_max_months
    if no_buyer and (r.get("sale_status") or r.get("stage") == "done"):
        return "sale_done_no_public_buyer"  # sale approved or closed; the final buyer is not public
    if no_buyer:
        return "sale_no_public_buyer"      # sale under way, buyer not public
    return "shell_or_closed"               # shell buyer, undated buyer, or a sale that closed > 12 months ago


def summary(items_rows: list[dict], list_c_rows: list, linked_leads: int) -> dict:
    """Reconciliation for the Funnel page and the deck (built from live counts)."""
    reasons = defaultdict(int)
    a = b = 0
    for r in items_rows:
        if r.get("rank_a"):
            a += 1
        elif r.get("rank_b"):
            b += 1
        else:
            reasons[other_reason(r) or "shell_or_closed"] += 1
    return {"leads": len(items_rows), "list_a": a, "list_b": b, "other": sum(reasons.values()),
            "other_breakdown": {k: reasons.get(k, 0) for k in ("candidate", "stale", "sale_no_public_buyer", "sale_done_no_public_buyer", "shell_or_closed")},
            "list_c_buyers": len(list_c_rows), "list_c_linked_leads": linked_leads}


def build(rows: list[dict], skip: set[str], contacts: dict | None = None, geo: dict | None = None) -> dict:
    """rows: app.v_lead_list rows in score order (facility_id used to leave out excluded homes and to attach contacts).
    contacts: {facility_id: app.v_facility_contact row}."""
    from src.score.diversity import display_sort, diversity_order, in_list_a, settings
    contacts = contacts or {}
    shown = display_sort([r for r in rows if str(r.get("facility_id")) not in skip])   # deterministic ties
    ordered, cap_main = diversity_order(shown)          # display order: max N leads per buyer in the top 20
    a_order, cap_a = diversity_order([r for r in shown if in_list_a(r)])
    rank_a = {id(r): i for i, r in enumerate(a_order, 1)}
    rank_b = {id(r): i for i, r in enumerate([r for r in shown if r.get("buyer") == NF and r.get("stage") != "sale"], 1)}
    top_n, per = settings()
    kept = []
    for i, r in enumerate(ordered, 1):
        rid = str(r.get("lead_id") or r.get("facility_id"))
        x = {k: r.get(k) for k in FIELDS}
        x["rank_a"], x["rank_b"] = rank_a.get(id(r)), rank_b.get(id(r))
        if rid in cap_main or rid in cap_a:
            where = " and ".join(w for w, c in (("main list", cap_main), ("List A", cap_a)) if rid in c)
            x["score_breakdown"] = dict(x.get("score_breakdown") or {}, buyer_diversity_cap=(
                f"more than {per} leads of this buyer in the top {top_n} of the {where}: shown below rank {top_n}"))
        x["breakdown"] = {k: v for k, v in (x.pop("score_breakdown") or {}).items() if k in BREAKDOWN_KEYS}
        x["rank"] = i
        x["contact"] = contacts.get(str(r.get("facility_id")))
        x["geo"] = (geo or {}).get(str(r.get("facility_id")))
        x["score"] = float(x["score"]) if x["score"] is not None else None
        x["sale_status"] = x.get("sale_status") or (r.get("score_breakdown") or {}).get("sale_status")
        x["source_urls"] = [u for u in (x["source_urls"] or []) if public_link(u)]
        x["buyer_source_url"] = public_link(x["buyer_source_url"])
        kept.append(x)
    a = sorted([dict(lead_row(r, "A"), rank=r["rank_a"]) for r in kept if r["rank_a"]], key=lambda x: x["rank"])
    b = sorted([dict(lead_row(r, "B"), rank=r["rank_b"]) for r in kept if r["rank_b"]], key=lambda x: x["rank"])
    by_buyer = defaultdict(list)
    for r in kept:
        if r["buyer_status"] == "named" and r["buyer"] and r["buyer"] != NF:
            by_buyer[r["buyer"]].append(r)
    c = []
    for buyer, homes in sorted(by_buyer.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        if len(homes) < 2:
            continue
        link = next((h["buyer_source_url"] for h in homes if h["buyer_source_url"]), None) or \
            next((h["source_urls"][0] for h in homes if h["source_urls"]), None)
        c.append({"list": "C", "rank": len(c) + 1, "buyer": buyer, "homes_in_list": len(homes),
                  "example_homes": "; ".join(f"{h['property']} ({h['city'] or NF}, {h['state'] or NF})" for h in homes[:3]),
                  "public_source_link": link or NF})
    items = [item(r, [x for x, ok in (("A", bool(r["rank_a"])), ("B", bool(r["rank_b"]))) if ok]) for r in kept]
    return {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "items": items,
            "source": "app.v_lead_list (public view)", "counts": {"leads": len(kept), "list_a": len(a),
                                                                   "list_b": len(b), "list_c": len(c)},
            "leads": [lead_row(r, "all") for r in kept], "list_a": a, "list_b": b, "list_c": c,
            "top20": [lead_row(r, "top20") for r in kept[:20]]}


# ---------- extra pages (How it works, Funnel, Explore) ----------
# Public sources shown on "How it works" (allowlist: anything not listed here, e.g. privately shared files, never
# appears). (source id, label, what it provides, how records are counted)
SOURCES = [
    ("cms_snf_enrollment", "CMS skilled-nursing enrollments", "Every Medicare-certified nursing home: name, address, operator", "xref"),
    ("cms_chow", "CMS change of ownership", "Nursing-home sales and new operators (dated)", "chow"),
    ("cms_snf_owner", "CMS ownership file", "Owners and parent companies behind each home", "roles"),
    ("cms_cost_report", "CMS cost reports", "Occupancy and net income per home", "metric:occupancy_proxy"),
    ("cms_pbj", "CMS staffing (PBJ)", "Residents and nurse staffing per quarter", "metric:avg_census"),
    ("cms_sff", "CMS Special Focus Facility list", "Homes on or near the federal watch list", "metric:sff_status"),
    ("hud_active", "HUD Section 232 loans (active)", "Federally insured senior-housing loans", "loans:active"),
    ("hud_terminated", "HUD Section 232 loans (terminated)", "Ended loans, including claims paid to the lender", "loans:terminated"),
    ("tx_hhsc", "Texas assisted-living licences", "Licence holder and manager, tracked over time", "licences:TX"),
    ("tx_hhsc_closures", "Texas closures list", "Assisted-living closures", "records"),
    ("nc_dhsr", "North Carolina adult-care licences", "Licence holder, tracked over time", "licences:NC"),
    ("mi_lara", "Michigan licensing", "Assisted-living / home-for-aged licences", "licences:MI"),
    ("ny_doh", "New York adult-care facilities", "Assisted-living homes holding a state licence", "licences:NY"),
    ("pa_dhs", "Pennsylvania personal-care homes", "Personal-care homes holding a state licence", "licences:PA"),
    ("ia_dial", "Iowa assisted-living programs", "Assisted-living programs holding a state licence", "licences:IA"),
    ("courtlistener", "CourtListener", "Federal bankruptcy dockets", "cases:courtlistener.com"),
    ("elevenflo", "ElevenFlo", "Bankruptcy case summaries and sale stages", "cases:elevenflo.com"),
    ("epiq", "Epiq", "Claims-agent case list", "cases:epiq11.com"),
    ("court_manual", "Public court filings (hand-entered)", "Filings the searches missed, each with a public link", "cases:inforuptcy.com"),
    ("shn_rss", "Senior Housing News", "Deals, bankruptcies, operator changes (read by AI)", "docs"),
    ("snn_rss", "Skilled Nursing News", "Nursing-home deals and distress (read by AI)", "docs"),
    ("shb_rss", "Seniors Housing Business", "Transactions and financings (read by AI)", "docs"),
    ("bondoro_rss", "Bondoro", "Chapter 11 filing alerts", "docs"),
    ("whatnow_rss", "WhatNow", "Bankruptcy news", "docs"),
    ("warn", "WARN notices", "Layoff / closure notices", "docs"),
]
REFRESH = {"cms": "monthly", "hud": "monthly", "tx_hhsc": "by hand (weekly file)", "tx_hhsc_closures": "by hand",
           "nc_dhsr": "by hand (monthly file)", "mi_lara": "by hand (monthly file)", "ny_doh": "by hand",
           "pa_dhs": "by hand", "ia_dial": "by hand", "courtlistener": "by hand (daily limit)", "elevenflo": "by hand",
           "epiq": "by hand", "court_manual": "by hand", "warn": "by hand"}


def source_rows(conn) -> list[dict]:
    out = []
    for sid, label, what, how in SOURCES:
        if how == "xref":
            n, unit = conn.execute("select count(*) n from core.facility_xref where source_id = %s", (sid,)).fetchone()["n"], "homes"
        elif how == "chow":
            n, unit = conn.execute("select count(*) n from core.ownership_event where event_type = 'chow'").fetchone()["n"], "ownership changes"
        elif how == "roles":
            n, unit = conn.execute("select count(*) n from core.facility_role where role in ('owner', 'indirect_owner')").fetchone()["n"], "owner links"
        elif how.startswith("metric:"):
            n, unit = conn.execute("select count(distinct facility_id) n from core.metric where metric = %s",
                                   (how[7:],)).fetchone()["n"], "homes measured"
        elif how.startswith("loans:"):
            st = "status = 'active'" if how.endswith("active") else "status like 'terminated%%'"
            n, unit = conn.execute(f"select count(*) n from core.loan where loan_source = 'hud' and {st}").fetchone()["n"], "loans"
        elif how.startswith("licences:"):
            n, unit = conn.execute("select count(distinct license_no) n from stg.state_license where state = %s",
                                   (how[9:],)).fetchone()["n"], "licences"
        elif how.startswith("cases:"):
            n, unit = conn.execute("select count(*) n from stg.court_case where source_url like %s",
                                   (f"%{how[6:]}%",)).fetchone()["n"], "cases"
        elif how == "records":
            n, unit = conn.execute("select count(*) n from stg.state_closure").fetchone()["n"], "closures"
        else:
            n, unit = conn.execute("select count(*) n from raw.document where source_id = %s", (sid,)).fetchone()["n"], "articles"
        if n:
            refresh = REFRESH.get(sid) or REFRESH.get(sid.split("_")[0]) or "daily feed, run by hand"
            out.append({"source": label, "provides": what, "records": n, "unit": unit, "refresh": refresh})
    return out


def rules_block() -> dict:
    from src.common.config import thresholds
    sc = thresholds()["scoring"]
    return {"main_min_signals": sc["early_main_min_signals"], "hard_events": sc["hard_events"],
            "lead_window_months": sc["lead_window_months"], "stage_weight": sc["stage_weight"],
            "court_sale_priority": sc["court_sale_priority"], "buyer_named_bonus": sc["buyer_named_bonus"],
            "buyer_candidate_bonus": sc["buyer_candidate_bonus"], "buyer_named_max_months": sc["buyer_named_max_months"],
            "size_fit": sc["size_fit"], "recency": sc["recency"], "closed_sale_penalty": sc["closed_sale_penalty"],
            "family_top_n": sc["family_top_n"], "name_only_top": sc["court_name_only"]["keep_out_of_top"],
            "care_type_fit": sc.get("care_type_fit"), "buyer_diversity": sc.get("buyer_diversity")}


def _latest(pattern: str) -> Path | None:
    files = sorted(OUTPUT_DIR.glob(pattern))
    return files[-1] if files else None


def watch_rows(skip_names: set[tuple]) -> list[dict]:
    """The watch list (below the main-list bar), public-source rows ONLY: any row touched by private evidence
    (flags licensed_private / licensed_only) is dropped, not masked. Read from the score run's export."""
    from src.score import rules
    p = _latest("INTERNAL_watch_*.csv")
    if not p:
        return []
    hard = set(rules.hard_events())
    out = []
    for r in csv.DictReader(open(p, encoding="utf-8")):
        flags = [f for f in (r.get("flags") or "").strip("{}[]").replace("'", "").replace('"', "").split(",") if f.strip()]
        flags = [f.strip() for f in flags]
        if "licensed_private" in flags or "licensed_only" in flags:
            continue
        if (r["property"].upper(), (r["city"] or "").upper(), r["state"] or "") in skip_names:
            continue
        sources = [u for u in re.findall(r"https?://[^\s,'\"\]\}]+", r.get("source_urls") or "")]
        if not sources:
            continue
        signals = [x.strip() for x in (r["distress_type"] or "").split(",") if x.strip()]
        distress = [x for x in signals if x != "market_sale"]
        reason = ("broker sale only (never makes a lead on its own)" if not distress else
                  f"{len(distress)} signal type{'s' if len(distress) != 1 else ''}, no hard event "
                  f"(main list needs {rules.wave3('early_main_min_signals')}+ or one hard event)")
        if set(distress) & hard:          # should not happen on the watch list; never claim a wrong reason
            continue
        buyer = r["buyer"] if r.get("buyer") not in (None, "", NF) and public_link(r.get("buyer_source_url")) else None
        care = [x for x in re.findall(r"[A-Z]+", r.get("care_types") or "")]
        out.append({"property": r["property"], "city": r["city"] or None, "state": r["state"] or None,
                    "care_types": care, "units": int(float(r["units"])) if r.get("units") else None,
                    "beds": int(float(r["beds"])) if r.get("beds") else None, "signals": signals,
                    "stage": r["stage"] or None, "buyer": buyer,
                    "score": float(r["score"]) if r.get("score") else None, "sources": sources, "reason": reason})
    out.sort(key=lambda x: -(x["score"] or 0))
    return out


def funnel(conn, snap: dict, explore_n: int) -> list[dict]:
    from src.score import rules
    tracked = conn.execute("select count(*) n from core.facility f where exists (select 1 from core.facility_xref x "
                           "where x.facility_id = f.facility_id and x.source_id <> 'licensed_data')").fetchone()["n"]
    main_all = conn.execute("select count(*) n from app.lead where tier = 'main'").fetchone()["n"]
    c = snap["counts"]
    n_main = rules.wave3("early_main_min_signals")
    return [
        {"step": "Buildings tracked", "count": tracked,
         "rule": "Every senior-housing or nursing building found in the public sources, matched into one record."},
        {"step": "With a distress signal", "count": c["leads"] + explore_n,
         "rule": "At least one dated distress signal from a public source in the last "
                 f"{rules.wave3('lead_window_months')} months."},
        {"step": "Watch list", "count": explore_n,
         "rule": f"Distress below the bar: fewer than {n_main} signal types and no hard event (see Explore)."},
        {"step": "Main list", "count": main_all,
         "rule": f"{n_main}+ different distress signal types, or one hard event (bankruptcy, receivership, "
                 "foreclosure, loan default, court sale, closure ...)."},
        {"step": "Shown on Deal Radar", "count": len(snap.get("items") or []) if "items" in snap else c["leads"],
         "rule": "Main-list leads whose evidence is public, each with its source links."},
        {"step": "List A", "count": c["list_a"], "rule": "Live deal with a buyer named in a source.", "list": "A"},
        {"step": "List B", "count": c["list_b"], "rule": "Distress before any sale; no buyer found yet.", "list": "B"},
        {"step": "List C", "count": c["list_c"], "rule": "Firms with 2+ acquisitions in 36 months (CMS ownership changes, state licence changes, court sales, news), each with a public link.", "list": "C"},
    ]


UPLOAD_KEYS = ("generated_at", "counts", "summary", "items", "list_c", "explore", "funnel", "sources_table", "rules", "daily")
UPLOAD_LIMIT = 950_000        # Render: all Secret Files of a service together must stay under 1 MB


def packed(snap: dict) -> bytes:
    """What gets uploaded to Render: the website's keys only, gzip + base64 (plain text, ~10x smaller)."""
    import base64
    import gzip
    raw = json.dumps({k: snap[k] for k in UPLOAD_KEYS if k in snap}, ensure_ascii=False, separators=(",", ":"),
                     default=str).encode("utf-8")
    return base64.b64encode(gzip.compress(raw, 9))


def write(snap: dict, out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(snap, ensure_ascii=False, default=str))
    blob = packed(snap)
    if len(blob) > UPLOAD_LIMIT:
        raise SystemExit(f"packed snapshot is {len(blob):,} bytes; Render Secret Files must stay under 1 MB")
    out.with_suffix(".b64").write_bytes(blob)
    for key, cols in (("list_a", LEAD_COLS), ("list_b", LEAD_COLS),
                      ("list_c", ["list", "rank", "buyer", "homes_in_list", "example_homes", "public_source_link"])):
        with open(out.with_name(f"site_{key}.csv"), "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
            w.writeheader()
            w.writerows(snap[key])


def fetch_geo(conn) -> dict:
    """{facility_id: {lat, lon, coord_source}} from core.facility_geo; {} before its migration."""
    try:
        return {str(r["facility_id"]): dict(r) for r in conn.execute(
            "select facility_id, lat, lon, coord_source from core.facility_geo").fetchall()}
    except Exception as exc:
        conn.rollback()
        print(f"note: no coordinates ({type(exc).__name__}); run python -m src.enrich.geo", file=sys.stderr)
        return {}


def fetch_contacts(conn) -> dict:
    """{facility_id: contact row} from app.v_facility_contact; {} when the contact migration has not been applied yet."""
    try:
        return {str(r["facility_id"]): dict(r) for r in conn.execute("select * from app.v_facility_contact").fetchall()}
    except Exception as exc:                      # undefined_table before the migration: the site just shows NOT FOUND
        conn.rollback()
        print(f"note: no contact data ({type(exc).__name__}); apply supabase/migrations/*_facility_contact.sql", file=sys.stderr)
        return {}


def main(argv=None) -> int:
    from src.common.db import connect
    ap = argparse.ArgumentParser(prog="python -m src.web.snapshot")
    ap.add_argument("--out", type=Path, default=OUTPUT_DIR / "site_snapshot.json")
    a = ap.parse_args(argv)
    with connect() as conn:
        rows = [dict(r) for r in conn.execute("select * from app.v_lead_list order by score desc nulls last").fetchall()]
        contacts = fetch_contacts(conn)
        geo = fetch_geo(conn)
        # sale status is built from public court / news sources only; the public view drops the breakdown of leads
        # that also carry private content, so it is read by flag + facility here
        sale = {str(r["f"]): r["s"] for r in conn.execute(
            "select facility_id f, score_breakdown->'sale_status' s from app.lead where score_breakdown ? 'sale_status'").fetchall()}
        from src.score import overrides
        more = overrides.extra_links(conn)
        for r in rows:
            r["extra_links"] = more.get(str(r["facility_id"]))
            if {"sale_approved", "sale_closed"} & set(r.get("flags") or []):
                r["sale_status"] = sale.get(str(r["facility_id"]))
    skip = excluded()
    snap = build(rows, skip, contacts, geo)
    with connect() as conn:
        skip_names = {(r["n"].upper(), (r["c"] or "").upper(), r["s"] or "") for r in conn.execute(
            "select canonical_name n, city c, state s from core.facility where facility_id = any(%s::uuid[])",
            (list(skip),)).fetchall()}
        explore = watch_rows(skip_names)
        snap["explore"] = explore
        snap["counts"]["explore"] = len(explore)
        from src.score import list_c as list_c_public
        c_rows, c_stats = list_c_public.build(conn)
        linked = set(c_stats.get("linked_facility_ids") or [])
        shown_ids = [str(r["facility_id"]) for r in rows if str(r.get("facility_id")) not in skip]
        snap["list_c"] = [{k: r[k] for k in ("list", "rank", "buyer", "buyer_type", "acquisitions_36mo", "homes_in_list",
                                             "latest_date", "example_homes", "public_source_link")} for r in c_rows]
        snap["counts"]["list_c"] = len(snap["list_c"])
        snap["summary"] = summary(snap["items"], snap["list_c"], sum(1 for f in shown_ids if f in linked))
        snap["funnel"] = funnel(conn, snap, len(explore))
        snap["sources_table"] = source_rows(conn)
        snap["rules"] = rules_block()
    write(snap, a.out)
    print(f"snapshot: {snap['counts']} -> {a.out}; upload {a.out.with_suffix('.b64').name} "
          f"({a.out.with_suffix('.b64').stat().st_size:,} bytes) as the Render Secret File (gitignored, never commit)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
