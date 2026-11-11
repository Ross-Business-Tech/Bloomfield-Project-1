"""Step 8: CSV export of app.v_lead_list, summary counts (each with % buyer named) and the source-URL check."""
from __future__ import annotations

import csv
import random
from datetime import date

from src.common.config import OUTPUT_DIR
from src.common.http import PoliteClient, SourceBlocked

CSV_COLS = ["property", "city", "state", "units", "beds", "care_types", "distress_type", "stage", "buyer",
            "buyer_parent", "buyer_type", "buyer_type_source", "buyer_status", "buyer_date", "score", "flags",
            "unconfirmed", "buyer_source_url", "source_urls", "ccn", "lead_id"]
_BD_COLS = {"buyer_parent", "buyer_type", "buyer_type_source", "buyer_date"}   # read from score_breakdown


def _cell(r: dict, c: str):
    v = (r.get("score_breakdown") or {}).get(c) if c in _BD_COLS else r.get(c)
    return " | ".join(v) if isinstance(v, list) else v


def export_rows(rows: list[dict], path=None, prefix: str = "watch") -> tuple[str, int]:
    path = path or OUTPUT_DIR / f"{prefix}_{date.today():%Y-%m-%d}.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = sorted(rows, key=lambda r: -float(r["score"] or 0))
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(CSV_COLS)
        for r in rows:
            w.writerow([_cell(r, c) for c in CSV_COLS])
    return str(path), len(rows)


def export_csv(conn, path=None, view: str = "app.v_lead_list", prefix: str = "leads") -> tuple[str, int]:
    """Main list -> output/leads_<date>.csv. The watch list is CSV-only (export_rows from the score run)."""
    assert view in ("app.v_lead_list", "app.v_lead_list_internal", "app.v_watch_list")
    return export_rows([dict(r) for r in conn.execute(f"select * from {view}").fetchall()], path, prefix)


def pct(n, d) -> str:
    return f"{(100.0 * n / d):.0f}%" if d else "n/a"


def summary(conn) -> dict:
    """Lead counts total / by stage / by category, each with % buyer named (human reporting rule)."""
    q = lambda sql: conn.execute(sql).fetchall()      # noqa: E731
    out = {}
    t = q("select count(*) n, count(*) filter (where buyer_status='named') named, "
          "count(*) filter (where buyer_status='candidate') cand from app.v_lead_list")[0]
    out["total"] = {**t, "pct_named": pct(t["named"], t["n"])}
    out["by_stage"] = [{**r, "pct_named": pct(r["named"], r["n"])} for r in q(
        "select stage, count(*) n, count(*) filter (where buyer_status='named') named from app.v_lead_list "
        "group by 1 order by array_position(array['sale','late','middle','early'], stage)")]
    out["by_category"] = [{**r, "pct_named": pct(r["named"], r["n"])} for r in q(
        "select s.category, count(distinct v.lead_id) n, count(distinct v.lead_id) filter (where v.buyer_status='named') named "
        "from app.v_lead_list v join app.lead_evidence e on e.lead_id = v.lead_id join core.signal s on s.signal_id = e.signal_id "
        "where s.signal_type <> 'market_sale' group by 1 order by 2 desc")]
    out["top"] = {}
    for n in (20, 100):
        r = q(f"select count(*) n, count(*) filter (where buyer_status='named') named from "
              f"(select * from app.v_lead_list limit {n}) t")[0]
        out["top"][n] = {**r, "pct_named": pct(r["named"], r["n"])}
    out["flags"] = q("select f, count(*) n from app.v_lead_list, unnest(flags) f group by 1 order by 2 desc")
    out["signals"] = q("select signal_type, stage, count(*) n, count(distinct facility_id) facilities from core.signal "
                       "group by 1, 2 order by 3 desc")
    return out


def check_urls(conn, n: int = 20, seed: int = 7) -> list[dict]:
    """Sample n distinct lead source URLs (one per host first, so CMS pages don't dominate) and fetch each once with
    the polite client (robots.txt obeyed, 1 req/s/host, no cache). robots/401/403 = 'blocked', never bypassed."""
    urls = [r["u"] for r in conn.execute(
        "select distinct u from app.v_lead_list, unnest(source_urls) u").fetchall()]
    rnd = random.Random(seed)
    rnd.shuffle(urls)
    by_host = {}
    for u in urls:
        by_host.setdefault(u.split("/")[2], []).append(u)
    sample = []
    while len(sample) < min(n, len(urls)):
        for host in sorted(by_host):
            if by_host[host] and len(sample) < n:
                sample.append(by_host[host].pop())
    results = []
    with PoliteClient("score_linkcheck", use_cache=False, timeout=60, max_retries=1) as client:
        for u in sample:
            try:
                r = client.get(u)
                results.append({"url": u, "status": r.status_code, "result": "ok" if r.status_code == 200 else "fail"})
            except SourceBlocked as exc:
                results.append({"url": u, "status": None, "result": "blocked", "note": str(exc)[:120]})
            except Exception as exc:    # network error: report, never retry around a block
                results.append({"url": u, "status": None, "result": "error", "note": f"{type(exc).__name__}"})
    return results


# ── Gate 3: deal rows (one per bankruptcy family) ────────────────────────────────────────────────────────────────
DEAL_COLS = ["deal_rank", "deal", "deal_kind", "deal_score", "deal_homes", "deal_size_units_or_beds", "deal_size_fit",
             "deal_too_big", "home_order", "property", "city", "state", "units", "beds", "size_fit", "stage",
             "distress_type", "proof", "buyer", "buyer_parent", "buyer_type", "buyer_type_source", "buyer_status",
             "home_score", "flags", "buyer_source_url", "source_urls", "ccn", "lead_id",
             "deal_sponsor", "deal_current_operator", "deal_owner_sources"]


def _private_groups() -> dict:
    """Owner / operator notes per licensed-loan deal row (eval/private/loan_groups_PRIVATE.yaml; never committed)."""
    import yaml
    from src.common.config import PROJECT_ROOT
    p = PROJECT_ROOT / "eval" / "private" / "loan_groups_PRIVATE.yaml"
    return (yaml.safe_load(p.read_text()) or {}).get("groups", {}) if p.exists() else {}
PROOF_LABEL = {4: "checked by a person", 3: "petition / claims agent", 2: "CMS operator = debtor",
               1: "CMS parent owner (portfolio)", 0: "name only"}
_STAGES = ("early", "middle", "late", "sale")
_GROUPED_STATUSES = ("named", "named (undated)", "stale", "shell")


def public_row(r: dict) -> dict | None:
    """Python twin of app.v_lead_list for CSV-only rows (watch list): None for licensed-only leads, licensed parts
    masked otherwise (deliverable rule, Nov 10, 2026)."""
    flags = list(r.get("flags") or [])
    if "licensed_only" in flags:
        return None
    r = dict(r)
    size_licensed = r.pop("size_licensed", False)
    if "licensed_private" not in flags:
        return r
    bd = r.get("score_breakdown") or {}
    pub = bd.get("public")
    if not pub:                       # no public twin: never shown
        return None
    out = dict(r)
    if size_licensed or bd.get("size_licensed"):
        out.update(units=None, beds=None)
    out.update(distress_type=pub.get("distress_type"), stage=pub.get("stage"), score=pub.get("score"),
               buyer=pub.get("buyer"), buyer_status=pub.get("buyer_status"),
               buyer_source_url=pub.get("buyer_source_url"), buyer_chain=pub.get("buyer_chain"))
    out["score_breakdown"] = {k: bd[k] for k in ("deal", "size_fit", "bankruptcy_family") if bd.get(k) is not None}
    out["flags"] = [f for f in flags if f not in ("licensed_private", "licensed_only")]
    out["source_urls"] = [u for u in (r.get("source_urls") or []) if str(u).startswith(("http://", "https://"))]
    return out


def _all_leads(conn, extra: list[dict] | None = None, internal: bool = False) -> list[dict]:
    """Main list + the CSV-only watch list rows. internal=True: everything (licensed facts included; INTERNAL_*
    CSVs, ranking, backtest). internal=False: Bloomfield-facing (app.v_lead_list; watch rows masked)."""
    if internal:
        return [dict(r) for r in conn.execute("select * from app.v_lead_list_internal").fetchall()] + \
            [dict(r) for r in extra or []]
    main = [dict(r) for r in conn.execute("select * from app.v_lead_list").fetchall()]
    return main + [x for x in (public_row(dict(r)) for r in extra or []) if x is not None]


def _loan_groups(conn) -> dict[str, str]:
    """facility -> licensed-loan deal row label (private.licensed_loan.loan_group)."""
    return {r["fid"]: r["loan_group"] for r in conn.execute(
        "select facility_id::text fid, loan_group from private.licensed_loan "
        "where facility_id is not null and loan_group is not null").fetchall()}


def deals(conn, extra: list[dict] | None = None, internal: bool = False) -> list[dict]:
    """Main-list leads grouped into deals, homes nested (main + watch homes):
      1. a bankruptcy family = one deal;
      2. otherwise homes whose buyer has the same parent (stated by a source or the CMS owner behind a shell)
         = one deal, e.g. all homes the Yosef Meystel Delta Trust took over;
      3. otherwise the home is its own deal.
    Homes inside a deal are ordered by size fit ($3-30M) -> distress severity -> proof strength.
    Deal score = best main-list home's score (before any family cap) with that home's size points replaced by
    the DEAL's size points: a bankruptcy is sized by the total units (else beds) of all its homes; a same-buyer
    group by its best home (separate purchases). A deal above deal_size.too_big_factor x the band's top never
    ranks in the top deal_size.keep_out_of_top."""
    from src.common.normalize import normalize_name
    from . import rules
    sf = rules.cfg()["scoring"]["size_fit"]
    ds = rules.cfg()["scoring"].get("deal_size") or {"too_big_factor": 2, "keep_out_of_top": 20}
    names = {r["family_id"]: r["family_name"] for r in conn.execute("select family_id, family_name from core.case_family").fetchall()}
    groups: dict = {}
    loan_group = _loan_groups(conn) if internal else {}      # licensed portfolios: internal only
    for r in _all_leads(conn, extra, internal):
        bd = r["score_breakdown"] or {}
        d = bd.get("deal")
        parent = bd.get("buyer_parent") if r["buyer_status"] in _GROUPED_STATUSES else None
        lg = loan_group.get(str(r["facility_id"]))
        if d:
            key, label, kind = ("fam", d["family_id"]), names.get(d["family_id"]), "bankruptcy"
        elif lg:                          # one loan portfolio = one transaction (licensed facts; CSV only)
            key, label, kind = ("loan", lg), lg, "loan portfolio"
            d = None
        elif parent:
            key, label, kind = ("buyer", normalize_name(parent)), f"{parent} (buyer)", "same buyer"
        else:
            key, label, kind = ("lead", r["facility_id"]), r["property"], "single home"
        g = groups.setdefault(key, {"deal": label, "kind": kind, "homes": [], "best": None, "best_fit_pts": 0.0})
        proof = d.get("proof", 0) if d else 0
        r["_proof"] = PROOF_LABEL[proof] if d else ""
        fit = rules.size_fit_points(r["units"], r["beds"])[0] > 0
        ntypes = len([t for t in (r["distress_type"] or "").split(", ") if t and t != "market_sale"])
        r["_key"] = (d["rank_in_family"],) if d else (not fit, -_STAGES.index(r["stage"]), -ntypes, -proof,
                                                      r["property"] or "")
        g["homes"].append(r)
        if r["tier"] == "main":
            s_ = float(d["home_score_uncapped"] if d else r["score"])
            if g["best"] is None or s_ > g["best"]:
                g["best"], g["best_fit_pts"] = s_, float(bd.get("size_fit") or 0)
    out = []
    for g in groups.values():
        if g["best"] is None:
            continue
        if g["kind"] == "same buyer":     # separate purchases by one buyer: each is financed on its own
            best = max((r for r in g["homes"] if r["tier"] == "main"), key=lambda r: float(r["score"] or 0))
            sizes = [best["units"] or best["beds"]]
        else:                             # one bankruptcy = one transaction: size of all its homes together
            sizes = [r["units"] or r["beds"] for r in g["homes"]]
        g["size"] = sum(x for x in sizes if x) or None
        g["size_fit"] = bool(g["size"]) and sf["min_units"] <= g["size"] <= sf["max_units"]
        g["too_big"] = bool(g["size"]) and g["size"] > sf["max_units"] * ds["too_big_factor"]
        g["score"] = round(g["best"] - g["best_fit_pts"] + (float(sf["points"]) if g["size_fit"] else 0.0), 2)
        out.append(g)
    out.sort(key=lambda g: (-g["score"], g["deal"] or ""))
    n = int(ds["keep_out_of_top"])
    ok = [g for g in out if not g["too_big"]]
    if len(ok) >= n:                       # push too-big deals to just under the N-th deal that fits
        floor = ok[n - 1]["score"] - 0.01
        for g in out:
            if g["too_big"] and g["score"] > floor:
                g["score_before_size_rule"], g["score"] = g["score"], round(floor, 2)
        out.sort(key=lambda g: (-g["score"], g["too_big"], g["deal"] or ""))
    for i, g in enumerate(out, 1):
        g["rank"] = i
        g["homes"].sort(key=lambda r: r["_key"])
        for j, r in enumerate(g["homes"], 1):
            r["_order"] = j
    return out


def export_deals(conn, extra: list[dict] | None = None, path=None, internal: bool = False) -> tuple[str, int]:
    from . import rules
    path = path or OUTPUT_DIR / f"{'INTERNAL_' if internal else ''}deals_{date.today():%Y-%m-%d}.csv"
    ds = deals(conn, extra, internal)
    notes = _private_groups() if internal else {}
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(DEAL_COLS)
        for g in ds:
            for r in g["homes"]:
                bd = r["score_breakdown"] or {}
                w.writerow([g["rank"], g["deal"], g["kind"], g["score"], len(g["homes"]), g["size"], g["size_fit"],
                            g["too_big"], r["_order"], r["property"], r["city"], r["state"], r["units"], r["beds"],
                            rules.size_fit_points(r["units"], r["beds"])[0] > 0, r["stage"], r["distress_type"],
                            r["_proof"], r["buyer"], bd.get("buyer_parent"), bd.get("buyer_type"),
                            bd.get("buyer_type_source"), r["buyer_status"], r["score"],
                            " | ".join(r["flags"] or []), r["buyer_source_url"], " | ".join(r["source_urls"] or []),
                            r["ccn"], r["lead_id"]] + ([notes.get(g["deal"], {}).get(k) for k in
                                                         ("borrower_sponsor", "current_operator", "sources")]
                                                        if g["kind"] == "loan portfolio" else [None, None, None]))
    return str(path), len(ds)


def repeat_buyers(conn, extra: list[dict] | None = None, months: int = 24, min_deals: int = 2,
                  today: date | None = None, internal: bool = False) -> dict:
    """Organizations that bought or took over (buyer_status 'named') distressed assets in 2+ separate deals within
    `months`. One deal = one bankruptcy family, else one source announcement (an article naming 5 homes is one deal),
    else one home. Joint buyers ("A; B") credit each named entity. Buyers are grouped by parent org only when a
    source states it (buyer_parent), otherwise by normalized name, so e.g. Focus Health Network is never merged into
    Focus Healthcare Partners without proof. Parents come from config/buyer_orgs.yaml / the source, or CMS ownership
    behind a shell. Hospital / public and lender buyers are left out (Gate 3 decisions); leads with no buyer date
    are left out. Both are counted."""
    from src.common.normalize import normalize_name
    from . import rules
    today = today or date.today()
    known = {}
    for x in rules.buyer_orgs():
        for n in [x["name"]] + list(x.get("aliases") or []):
            known[normalize_name(n)] = x
    buyers: dict = {}
    skipped = {"named_without_date": 0, "hospital_district": 0, "hospital_or_lender": 0}
    for r in _all_leads(conn, extra, internal):
        if r["buyer_status"] != "named":
            continue
        if "buyer_hospital_district" in (r["flags"] or []):
            skipped["hospital_district"] += 1
            continue
        bd = r["score_breakdown"] or {}
        when = bd.get("buyer_date")
        if not when:
            skipped["named_without_date"] += 1
            continue
        when = date.fromisoformat(when)
        if rules.months_between(when, today) > months:
            continue
        d = bd.get("deal")
        dkey = ("fam", d["family_id"]) if d else ("src", r["buyer_source_url"]) if r["buyer_source_url"] \
            else ("lead", r["lead_id"])
        names = [bd["buyer_parent"]] if bd.get("buyer_parent") else [n.strip() for n in r["buyer"].split(";") if n.strip()]
        names = [(known.get(normalize_name(n)) or {}).get("parent") or n for n in names]
        for display in dict.fromkeys(names):
            canon = (known.get(normalize_name(display)) or {}).get("name") or display
            btype, basis = rules.buyer_type(canon, known)
            if basis not in ("source",) and display == bd.get("buyer_parent", r["buyer"]) and bd.get("buyer_type"):
                btype, basis = bd["buyer_type"], bd.get("buyer_type_basis") or basis
            if btype in ("hospital/public", "lender"):
                skipped["hospital_or_lender"] += 1
                continue
            b = buyers.setdefault(normalize_name(canon), {"buyer": canon, "deals": {}, "entities": set(),
                                                          "buyer_type": btype, "type_basis": basis,
                                                          "type_source": "confirmed" if basis in ("source", "cms")
                                                          else "inferred"})
            b["entities"].add(r["buyer"])
            deal = b["deals"].setdefault(dkey, {"homes": [], "date": when, "source_url": r["buyer_source_url"]})
            deal["homes"].append(f"{r['property']} ({r['city']}, {r['state']})")
            deal["date"] = max(deal["date"], when)
    rows = [b for b in buyers.values() if len(b["deals"]) >= min_deals]
    rows.sort(key=lambda b: (-len(b["deals"]), -sum(len(d["homes"]) for d in b["deals"].values()),
                             -max(d["date"] for d in b["deals"].values()).toordinal(), b["buyer"]))
    return {"buyers": rows, **skipped}


def export_repeat_buyers(conn, extra: list[dict] | None = None, path=None, top: int = 10,
                         internal: bool = False) -> tuple[str, int]:
    path = path or OUTPUT_DIR / f"{'INTERNAL_' if internal else ''}repeat_buyers_{date.today():%Y-%m-%d}.csv"
    res = repeat_buyers(conn, extra, internal=internal)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["rank", "buyer", "buyer_type", "buyer_type_source", "buying_entities", "deals", "homes", "deal_date", "deal_homes",
                    "source_url"])
        for i, b in enumerate(res["buyers"][:top], 1):
            for d in sorted(b["deals"].values(), key=lambda d: d["date"], reverse=True):
                w.writerow([i, b["buyer"], b["buyer_type"], b["type_source"], " | ".join(sorted(b["entities"])), len(b["deals"]),
                            sum(len(x["homes"]) for x in b["deals"].values()), d["date"], " | ".join(d["homes"]),
                            d["source_url"]])
    return str(path), min(top, len(res["buyers"]))
