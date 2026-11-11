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
FIELDS = ["property", "city", "state", "care_types", "units", "beds", "distress_type", "stage", "buyer",
          "buyer_status", "score", "flags", "source_urls", "buyer_source_url", "first_seen", "last_updated",
          "score_breakdown"]
# score_breakdown keys the website may show (scores, stage source, dates, buyer type); anything else is dropped,
# e.g. other buyers seen or dropped debtor names
BREAKDOWN_KEYS = ["total", "stage", "buyer", "recency", "size_fit", "court_sale_priority", "closed_sale_penalty",
                  "hud_exit", "market_sale", "buyer_chain_quality", "stage_from", "latest_signal_date", "hard_events",
                  "bankruptcy_family", "buyer_date", "buyer_date_basis", "buyer_type", "buyer_type_source",
                  "buyer_parent", "buyer_parent_basis"]


def buyer_group(r: dict) -> str:
    """Website filter group: confirmed (named in a source), candidate (stalking horse / proposed), inferred (parent
    matched by brand, or a shell / stale buyer), none (NOT FOUND)."""
    st, bd = r.get("buyer_status"), r.get("breakdown") or {}
    if st == "named" and bd.get("buyer_parent_basis") != "brand_match":
        return "confirmed"
    if st == "candidate":
        return "candidate"
    if st in ("named", "shell", "stale"):
        return "inferred"
    return "none"


def item(r: dict, lists: list[str]) -> dict:
    """One lead for the website (public-view fields only)."""
    bd = r.get("breakdown") or {}
    sources = list(dict.fromkeys([u for u in [r.get("buyer_source_url"), *(r.get("source_urls") or [])] if public_link(u)]))
    d = lambda v: str(v)[:10] if v else None       # noqa: E731
    out = {"rank": r["rank"], "property": r["property"], "city": r.get("city"), "state": r.get("state"),
           "care_types": r.get("care_types") or [], "units": r.get("units"), "beds": r.get("beds"),
           "signals": [x.strip() for x in (r.get("distress_type") or "").split(",") if x.strip()],
           "stage": r.get("stage"), "buyer": r.get("buyer") if r.get("buyer") not in (None, NF) else None,
           "buyer_status": r.get("buyer_status") or NF, "buyer_group": buyer_group(r), "score": r.get("score"),
           "flags": r.get("flags") or [], "sources": sources, "lists": lists,
           "breakdown": {k: bd[k] for k in BREAKDOWN_KEYS if k in bd},
           "deal_homes": (bd.get("deal") or {}).get("homes"), "first_seen": d(r.get("first_seen")),
           "last_updated": d(r.get("last_updated"))}
    out["not_found"] = [k for k, v in (("city", out["city"]), ("state", out["state"]), ("units", out["units"] or out["beds"]),
                                       ("care_type", out["care_types"]), ("buyer", out["buyer"]),
                                       ("source", out["sources"])) if not v]
    return out
LEAD_COLS = ["list", "rank", "property", "city", "state", "care_type", "units_or_beds", "signals", "stage", "buyer",
             "buyer_status", "score", "flags", "public_source_link", "not_found"]


def excluded() -> set[str]:
    if not EXCLUDE_FILE.exists():
        return set()
    return {ln.strip() for ln in EXCLUDE_FILE.read_text().splitlines() if ln.strip() and not ln.startswith("#")}


def public_link(url) -> str | None:
    return url if isinstance(url, str) and url.startswith(("http://", "https://")) else None


def lead_row(r: dict, lst: str) -> dict:
    size = r.get("units") or r.get("beds")
    links = [u for u in (r.get("source_urls") or []) if public_link(u)]
    link = public_link(r.get("buyer_source_url")) if lst == "A" else None
    out = {"list": lst, "rank": r["rank"], "property": r["property"], "city": r.get("city") or NF,
           "state": r.get("state") or NF, "care_type": "/".join(r.get("care_types") or []) or NF,
           "units_or_beds": size or NF, "signals": r.get("distress_type") or NF, "stage": r.get("stage") or NF,
           "buyer": r.get("buyer") or NF, "buyer_status": r.get("buyer_status") or NF,
           "score": float(r["score"]) if r.get("score") is not None else None,
           "flags": ";".join(r.get("flags") or []), "public_source_link": link or (links[0] if links else NF)}
    out["not_found"] = ";".join(k for k, v in out.items() if v == NF)
    return out


def build(rows: list[dict], skip: set[str]) -> dict:
    """rows: app.v_lead_list rows in score order (facility_id used only to leave out excluded homes)."""
    kept = []
    for i, r in enumerate(rows, 1):
        if str(r.get("facility_id")) in skip:
            continue
        x = {k: r.get(k) for k in FIELDS}
        x["breakdown"] = {k: v for k, v in (x.pop("score_breakdown") or {}).items() if k in BREAKDOWN_KEYS}
        x["rank"] = i
        x["score"] = float(x["score"]) if x["score"] is not None else None
        x["source_urls"] = [u for u in (x["source_urls"] or []) if public_link(u)]
        x["buyer_source_url"] = public_link(x["buyer_source_url"])
        kept.append(x)
    a = [lead_row(r, "A") for r in kept if r["buyer_status"] == "named"
         and "sale_closed_over_12m" not in (r["flags"] or [])]
    b = [lead_row(r, "B") for r in kept if r["buyer"] == NF and r["stage"] != "sale"]
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
    in_a = {r["rank"] for r in a}
    in_b = {r["rank"] for r in b}
    items = [item(r, [x for x, ok in (("A", r["rank"] in in_a), ("B", r["rank"] in in_b)) if ok]) for r in kept]
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
            "family_top_n": sc["family_top_n"], "name_only_top": sc["court_name_only"]["keep_out_of_top"]}


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
        {"step": "Shown on Deal Radar", "count": c["leads"],
         "rule": "Main-list leads whose evidence is public, each with its source links."},
        {"step": "List A", "count": c["list_a"], "rule": "Live deal with a buyer named in a source.", "list": "A"},
        {"step": "List B", "count": c["list_b"], "rule": "Distress before any sale; no buyer found yet.", "list": "B"},
        {"step": "List C", "count": c["list_c"], "rule": "Buyers named on 2+ homes in the list.", "list": "C"},
    ]


UPLOAD_KEYS = ("generated_at", "counts", "items", "list_c", "explore", "funnel", "sources_table", "rules")
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


def main(argv=None) -> int:
    from src.common.db import connect
    ap = argparse.ArgumentParser(prog="python -m src.web.snapshot")
    ap.add_argument("--out", type=Path, default=OUTPUT_DIR / "site_snapshot.json")
    a = ap.parse_args(argv)
    with connect() as conn:
        rows = [dict(r) for r in conn.execute("select * from app.v_lead_list order by score desc nulls last").fetchall()]
    skip = excluded()
    snap = build(rows, skip)
    with connect() as conn:
        skip_names = {(r["n"].upper(), (r["c"] or "").upper(), r["s"] or "") for r in conn.execute(
            "select canonical_name n, city c, state s from core.facility where facility_id = any(%s::uuid[])",
            (list(skip),)).fetchall()}
        explore = watch_rows(skip_names)
        snap["explore"] = explore
        snap["counts"]["explore"] = len(explore)
        snap["funnel"] = funnel(conn, snap, len(explore))
        snap["sources_table"] = source_rows(conn)
        snap["rules"] = rules_block()
    write(snap, a.out)
    print(f"snapshot: {snap['counts']} -> {a.out}; upload {a.out.with_suffix('.b64').name} "
          f"({a.out.with_suffix('.b64').stat().st_size:,} bytes) as the Render Secret File (gitignored, never commit)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
