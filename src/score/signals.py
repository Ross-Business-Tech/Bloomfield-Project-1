"""Step 5: signal rules → core.signal (BUILD_SPEC step 5, Gate 2 decisions).

Reads core.* / stg.* (read-only) and returns Sig objects; `write_signals` upserts them on source_key.
Retention (Gate 2): metric signals keep one row per facility + type (source_key has no period, so a newer
period overwrites the old one and a facility that no longer qualifies loses its row); event signals keep full
history (one row per event, never deleted).
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date

from src.common.bulk import bulk_upsert
from src.common.normalize import entity_stem, normalize_name

from src.extract.client import DEFAULT_MODEL as EXTRACTION_MODEL   # only the current model's answers (Gate 3: Sonnet)

from . import rules
from .rules import CATEGORY, METRIC_SIGNALS, Sig, stage_of

NEWS_SOURCES = ("shb_rss", "shn_rss", "snn_rss", "gnews_rss", "mcknights_ltc_rss", "mcknights_sl_rss", "gdelt_doc",
                "bondoro_rss", "whatnow_rss")
COURT_SOURCES = ("courtlistener", "elevenflo", "epiq", "stretto", "kroll", "court_manual")


@dataclass
class BuyerHint:
    facility_id: str
    name: str
    status: str                 # named / candidate
    source_url: str
    origin: str                 # court_sale / news / chow / license_change
    event_date: date | None = None
    org_id: str | None = None
    link_status: str = "auto"
    weight: float = 1.0
    family_id: int | None = None
    parent: str | None = None   # buyer's parent org, only when a source states it (config/court_facts.yaml)
    forced: bool = False        # hand-check override: kept even when the own-operator rule would drop it


def _http(url) -> bool:
    return isinstance(url, str) and url.startswith(("http://", "https://"))


def _fac_filter(limit_ids):
    return "" if limit_ids is None else " and facility_id = any(%(fids)s::uuid[])"


# ── metric signals ───────────────────────────────────────────────────────────────────────────────────────────────
def metric_signals(conn, fids=None) -> list[Sig]:
    out: list[Sig] = []
    p = {"fids": fids}
    latest = conn.execute(
        "select distinct on (facility_id, metric) facility_id::text, metric, period_end, value, source_url "
        "from core.metric where metric in ('occupancy_proxy','net_income','sff_status')" + _fac_filter(fids) +
        " order by facility_id, metric, period_end desc", p).fetchall()
    for r in latest:
        fid, m, v = r["facility_id"], r["metric"], r["value"]
        if m == "occupancy_proxy" and rules.is_low_occupancy(v):
            st, ev = "low_occupancy", f"Cost-report occupancy {float(v):.0%} (period ending {r['period_end']})"
        elif m == "net_income" and rules.is_negative_noi(v):
            st, ev = "negative_noi", f"Cost-report net income ${float(v):,.0f} (period ending {r['period_end']})"
        elif m == "sff_status" and v is not None:
            label = {1: "SFF candidate", 2: "Special Focus Facility"}.get(int(v), f"SFF status {v}")
            st, ev = "sff_or_candidate", f"CMS {label} list, {r['period_end']:%B %Y}"
        else:
            continue
        out.append(Sig(fid, st, stage_of(st), f"m:{st}:{fid}", r["source_url"], r["period_end"], v, ev,
                       CATEGORY[st]))
    # staffing_drop: needs >= over_quarters + 1 quarters of PBJ census per facility
    series = defaultdict(list)
    urls = {}
    for r in conn.execute("select facility_id::text, period_end, value, source_url from core.metric "
                          "where metric = 'avg_census'" + _fac_filter(fids), p).fetchall():
        series[r["facility_id"]].append((r["period_end"], r["value"]))
        urls[r["facility_id"]] = r["source_url"]
    for fid, s in series.items():
        if rules.is_staffing_drop(s):
            last = max(s)
            drop = rules.census_drop(s)
            out.append(Sig(fid, "staffing_drop", stage_of("staffing_drop"), f"m:staffing_drop:{fid}", urls[fid],
                           last[0], round(drop, 4), f"PBJ average census down {drop:.0%} over "
                           f"{rules.cfg()['signals']['staffing_drop']['over_quarters']} quarters", "operating"))
    # DSCR rules: only loans that carry a DSCR (HUD files do not; ready for CMBS / bridge data)
    sc = rules.cfg()["signals"]
    for r in conn.execute("select distinct on (facility_id) facility_id::text, loan_id::text, dscr, maturity_date, "
                          "source_url from core.loan where dscr is not null and status = 'active'"
                          + _fac_filter(fids) + " order by facility_id, dscr", p).fetchall():
        d = float(r["dscr"])
        if d < sc["dscr_below_1"]["dscr_below"]:
            out.append(Sig(r["facility_id"], "dscr_below_1", stage_of("dscr_below_1"), f"m:dscr_below_1:{r['facility_id']}",
                           r["source_url"], None, d, f"Loan DSCR {d:.2f}", "debt_clock"))
        m = sc["maturity_12m_low_dscr"]
        if r["maturity_date"] and d < m["dscr_below"] and \
                rules.months_between(date.today(), r["maturity_date"]) <= m["months_to_maturity"]:
            out.append(Sig(r["facility_id"], "maturity_12m_low_dscr", stage_of("maturity_12m_low_dscr"),
                           f"m:maturity_12m_low_dscr:{r['facility_id']}", r["source_url"], r["maturity_date"], d,
                           f"Loan matures {r['maturity_date']} with DSCR {d:.2f}", "debt_clock"))
    return out


# ── ownership: operator turnover ─────────────────────────────────────────────────────────────────────────────────
def ownership_signals(conn, today: date, news_changes: dict, fids=None) -> tuple[list[Sig], list[BuyerHint]]:
    out, hints = [], []
    p = {"fids": fids}
    rule = rules.cfg()["signals"]["operator_turnover"]
    # (a) Gate 2: one building with 2+ distinct Medicare numbers (merged duplicate) = operator turnover
    if rule.get("two_medicare_numbers"):
        rows = conn.execute(
            "select x.facility_id::text, array_agg(distinct x.source_key order by x.source_key) ccns, "
            "min(e.source_url) url from core.facility_xref x "
            "left join stg.cms_snf_enrollment e on e.ccn = x.source_key "
            "where x.match_method = 'exact_ccn'" + _fac_filter(fids).replace("facility_id", "x.facility_id") +
            " group by 1 having count(distinct x.source_key) >= 2", p).fetchall()
        last_chow = {r["facility_id"]: r["d"] for r in conn.execute(
            "select facility_id::text, max(effective_date) d from core.ownership_event where event_type='chow' "
            "group by 1").fetchall()}
        for r in rows:
            url = r["url"] or "https://data.cms.gov/provider-characteristics/hospitals-and-other-facilities/skilled-nursing-facility-enrollments"
            out.append(Sig(r["facility_id"], "operator_turnover", stage_of("operator_turnover"),
                           f"ot:ccn:{r['facility_id']}", url, last_chow.get(r["facility_id"]), len(r["ccns"]),
                           "Same building has %d Medicare numbers (CCN %s): a new operator re-enrolled it"
                           % (len(r["ccns"]), ", ".join(r["ccns"])), "ownership"))
    # (b) >= min_changes CHOW / license changes / news-reported new operators within within_months
    changes = defaultdict(list)    # facility -> [(date, source_url, label)]
    for r in conn.execute("select facility_id::text, event_type, effective_date d, source_url, source_key, "
                          "buyer_org_id::text buyer, (select name from core.organization o where o.org_id = buyer_org_id) bname "
                          "from core.ownership_event where event_type in ('chow','license_change') and effective_date is not null"
                          + _fac_filter(fids), p).fetchall():
        changes[r["facility_id"]].append((r["d"], r["source_url"], r["event_type"]))
        if r["buyer"] and rules.months_between(r["d"], today) <= rule["within_months"] and _http(r["source_url"]):
            hints.append(BuyerHint(r["facility_id"], r["bname"], "named", r["source_url"],
                                   "chow" if r["event_type"] == "chow" else "license_change", r["d"], r["buyer"]))
    for fid, lst in news_changes.items():
        changes[fid].extend(lst)
    for fid, lst in changes.items():
        dates = [d for d, _, _ in lst]
        if rules.operator_changes_qualify(dates, today):
            recent = sorted((x for x in lst if rules.months_between(x[0], today) <= rule["within_months"]),
                            key=lambda x: x[0])
            latest = recent[-1]
            out.append(Sig(fid, "operator_turnover", stage_of("operator_turnover"),
                           f"ot:chg:{fid}:{latest[0]}", latest[1], latest[0], len(recent),
                           f"{len(recent)} operator changes in {rule['within_months']} months "
                           f"({', '.join(f'{lbl} {d}' for d, _, lbl in recent)})", "ownership"))
    return out, hints


# ── debt clock: HUD claim terminations ───────────────────────────────────────────────────────────────────────────
def hud_signals(conn, fids=None) -> list[Sig]:
    """Only CLAIM terminations (lender filed an FHA insurance claim: assignment, conveyance, partial claim...).
    ASSUMPTION: prepayment / maturity / voluntary terminations are refinances or payoffs, not distress."""
    out = []
    for r in conn.execute(
            "select l.facility_id::text, l.source_key lk, l.termination_date, l.termination_type, l.source_url, "
            "coalesce(x.review_status, 'auto') rs, x.match_score from core.loan l "
            "left join core.facility_xref x on x.source_id = split_part(l.source_key, ':', 1) "
            "  and x.source_key = split_part(l.source_key, ':', 2) and x.facility_id = l.facility_id "
            "where l.loan_source = 'hud' and l.status = 'terminated_claim'"
            + _fac_filter(fids).replace("facility_id", "l.facility_id"), {"fids": fids}).fetchall():
        s = Sig(r["facility_id"], "hud_loan_terminated", stage_of("hud_loan_terminated"),
                f"hud:{r['lk']}:{r['facility_id']}", r["source_url"], r["termination_date"], None,
                f"HUD Section 232 loan {r['lk'].split(':')[-1]} terminated with an insurance claim "
                f"({r['termination_type']}) on {r['termination_date']}", "debt_clock")
        if r["rs"] == "needs_review":
            s.link_status = "needs_review"
            s.evidence_text += f" [facility link needs review, score {r['match_score']}]"
        out.append(s)
    return out


# ── state licence rosters: licensee / management-company changes and closures (Wave 3.5) ─────────────────────────
# Decision (Nov 10, 2026, revised): a licensee / management-company change is a BUYER EVENT, not distress. It becomes an
# operator_turnover signal only when the same home had real distress in the 24 months before the change:
# closure, HUD delinquency, special servicing / lender takeback (REO), low occupancy or SFF status.
TURNOVER_NEEDS_PRIOR = ("closure", "hud_delinquency", "special_servicing", "lender_takeback", "low_occupancy",
                        "sff_or_candidate")
TURNOVER_PRIOR_MONTHS = 24


def license_change_events(conn, today: date, fids=None) -> list[dict]:
    rule = rules.cfg()["signals"]["operator_turnover"]
    out = []
    for r in conn.execute(
            "select e.facility_id::text, e.event_type, e.effective_date d, e.prior_seen_date p, e.source_url, "
            "e.source_key, so.name seller, bo.name buyer from core.ownership_event e "
            "left join core.organization so on so.org_id = e.seller_org_id "
            "left join core.organization bo on bo.org_id = e.buyer_org_id "
            "where e.event_type in ('license_change','management_change') and e.prior_seen_date is not null"
            + _fac_filter(fids).replace("facility_id", "e.facility_id"), {"fids": fids}).fetchall():
        if rules.months_between(r["d"], today) <= rule["within_months"]:
            out.append(dict(r))
    return out


def license_signals(conn, today: date, prior: list[Sig], fids=None) -> list[Sig]:
    """operator_turnover from roster changes, only after prior distress (see TURNOVER_NEEDS_PRIOR)."""
    distress = defaultdict(list)
    for s_ in prior:
        if s_.signal_type in TURNOVER_NEEDS_PRIOR and s_.event_date:
            distress[s_.facility_id].append(s_)
    out = []
    for r in license_change_events(conn, today, fids):
        before = [x for x in distress.get(r["facility_id"], [])
                  if x.event_date <= r["d"] and rules.months_between(x.event_date, r["d"]) <= TURNOVER_PRIOR_MONTHS]
        if not before:
            continue
        what = "Licensee" if r["event_type"] == "license_change" else "Management company"
        prior_txt = ", ".join(sorted({f"{x.signal_type} {x.event_date}" for x in before}))
        out.append(Sig(r["facility_id"], "operator_turnover", stage_of("operator_turnover"), f"ot:{r['source_key']}",
                       r["source_url"], r["d"], 1,
                       f"{what} changed after distress ({prior_txt}): {r['seller']} (last seen {r['p']}) -> "
                       f"{r['buyer']} (first seen {r['d']}); state licence roster snapshots", "ownership"))
    return out


CLOSURE_MAX_MONTHS = 24          # ASSUMPTION: older closures are not a lending opportunity
# The closures file has no bed count. Homes under 13 beds were never loaded from the roster snapshots, so a closed
# home with no known size is almost always a small one: only closures with a known size of 13+ count (assumption).
CLOSURE_MIN_SIZE = 13


def closure_signals(conn, today: date, fids=None) -> list[Sig]:
    out = []
    for r in conn.execute(
            "select x.facility_id::text, x.review_status rs, x.match_score, c.* from stg.state_closure c "
            "join core.facility_xref x on x.source_id = 'tx_hhsc' and x.source_key = c.facility_key "
            "join core.facility f on f.facility_id = x.facility_id "
            "where c.state = 'TX' and c.closure_date is not null "
            f"and coalesce(f.units, f.beds) >= {CLOSURE_MIN_SIZE}"
            + _fac_filter(fids).replace("facility_id", "x.facility_id"), {"fids": fids}).fetchall():
        if rules.months_between(r["closure_date"], today) > CLOSURE_MAX_MONTHS:
            continue
        s = Sig(r["facility_id"], "closure", stage_of("closure"), f"closure:TX:{r['facility_key']}:{r['closure_date']}",
                r["source_url"], r["closure_date"], None,
                f"Texas HHSC lists this assisted living facility ({r['facility_name']}, owner {r['owner']}) as "
                f"closed on {r['closure_date']}", "operating")
        if r["rs"] == "needs_review":
            s.link_status = "needs_review"
            s.evidence_text += f" [facility link needs review, score {r['match_score']}]"
        out.append(s)
    return out


# ── licensed loan facts (licensed exports, schema private) ────────────────────────────────────────────────────────
# Source tag 'licensed:<source>' instead of a web link (decision Nov 10, 2026); leads resting on these are flagged
# 'licensed_private' and hidden from the public view. Status histories have the most recent month on the left.
# ASSUMPTION: position 0 of a history string = the month before the export date (Oct 2026 for the Nov 10 export).
LICENSED_HISTORY_MONTH0 = date(2026, 10, 1)


def _month_back(i: int) -> date:
    y, m = divmod(LICENSED_HISTORY_MONTH0.year * 12 + LICENSED_HISTORY_MONTH0.month - 1 - i, 12)
    return date(y, m + 1, 1)


def _run_latest(hist: str, codes: str) -> int | None:
    """Index of the most recent month showing one of `codes` in the leading run (the status is ongoing)."""
    for i, ch in enumerate(hist):
        if ch in codes:
            return i
        if ch != "-":
            return None
    return None


def _run_start(hist: str, codes: str) -> int | None:
    """Index (months back) where the most recent unbroken run of `codes` starts, counting from the left."""
    i, start = 0, None
    while i < len(hist) and hist[i] in codes + "-":
        if hist[i] in codes:
            start = i
        i += 1
    return start


def licensed_signals(conn, today: date, fids=None) -> list[Sig]:
    out = []
    for r in conn.execute("select * from private.licensed_loan where facility_id is not null"
                          + _fac_filter(fids), {"fids": fids}).fetchall():
        fid, hist = str(r["facility_id"]), (r["status_history"] or "")
        url = f"licensed:provider:{r['source_file']}"
        tag = f" [{r['source_label']}]"
        needs_review = r["match_method"] == "fuzzy_needs_review"

        def add(stype, when, text):
            s_ = Sig(fid, stype, stage_of(stype), f"lic:{stype}:{r['loan_key']}", url, when, None, text + tag, "debt_clock")
            if needs_review:
                s_.link_status = "needs_review"
                s_.evidence_text += f" [facility link needs review, score {r['match_score']:.0f}]"
            out.append(s_)

        if "hud232" in r["source_file"]:
            # payment history: C = current; any other letter/digit = a delinquent month
            bad = [i for i, ch in enumerate(hist[:24]) if ch not in "C-"]
            if bad:
                add("hud_delinquency", _month_back(bad[0]),
                    f"HUD Section 232 / Ginnie Mae ({r['deal_name']}) loan delinquent {int(r['months_delinquent_total'] or 0)} "
                    f"months in total; latest delinquent month {_month_back(bad[0]):%b %Y}"
                    + (f", current since then" if bad[0] > 0 and hist[0] == "C" else ""))
            continue
        status = (r["loan_status"] or "").lower()
        # ongoing status: dated by the latest month still in that status; the start goes in the evidence
        if status == "reo":
            i, j = _run_start(hist, "R"), _run_latest(hist, "R")
            add("lender_takeback", _month_back(j) if j is not None else None,
                "Loan is REO: the lender took the property back" + (f" (REO since about {_month_back(i):%b %Y})" if i is not None else ""))
        elif status == "special":
            i, j = _run_start(hist, "SLXM9Y"), _run_latest(hist, "SLXM9Y")
            add("special_servicing", _month_back(j) if j is not None else None,
                "Loan in special servicing" + (f" since about {_month_back(i):%b %Y}" if i is not None else "")
                + (f"; transfer reason: {r['transfer_reason']}" if r["transfer_reason"] else ""))
        if r["maturity_date"] and r["maturity_date"] < today and status in ("special", "reo"):
            add("maturity_default", r["maturity_date"],
                f"Loan matured {r['maturity_date']} and was not repaid (status {r['loan_status']})")
    return out


def licensed_buyer_hints(conn, legal: list[Sig], fids=None) -> list[BuyerHint]:
    """Buyers from licensed sources (private.licensed_buyer): one home (facility_id) or a whole bankruptcy family."""
    fams = defaultdict(set)
    for s_ in legal:
        if s_.family_id is not None:
            fams[s_.family_id].add(s_.facility_id)
    out = []
    for b in conn.execute("select * from private.licensed_buyer").fetchall():
        if b["facility_id"] is not None:
            homes = {str(b["facility_id"])}
        elif b["community"]:
            continue                      # a per-community buyer whose building was not found: never family-wide
        elif b["family_id"] is not None:
            homes = set(fams.get(b["family_id"], set()))
        else:
            continue
        for fid in homes:
            if fids is not None and fid not in fids:
                continue
            out.append(BuyerHint(fid, b["buyer_name"], b["status"], "licensed:provider", "licensed",
                                 b["event_date"], None, "auto", 1.0, b["family_id"], None))
    return out


def licensed_sale_signals(conn, fids=None) -> list[Sig]:
    """Court-approved sales from licensed bankruptcy data -> court_sale_process (sale stage) at each community."""
    out = []
    for b in conn.execute("select * from private.licensed_buyer where sale_signal and facility_id is not null"
                          + _fac_filter(fids), {"fids": fids}).fetchall():
        s_ = Sig(str(b["facility_id"]), "court_sale_process", stage_of("court_sale_process"),
                 f"licsale:{b['hint_key']}", "licensed:provider", b["event_date"], None,
                 (b["evidence_text"] or "") + f" [{b['source_label']}]", "legal")
        out.append(s_)
    return out


def licensed_fact_signals(conn, fids=None) -> tuple[list[Sig], list[BuyerHint]]:
    """Row-level licensed facts (private.licensed_fact), only rows marked NEW (new distress / new buyer), only when the
    row is linked to a facility. Existing rules: chapter 11 / 7, foreclosure, receivership and a court-approved sale
    need court proof (a case number in the row, or a bankruptcy family we hold); a bond / loan default needs none.
    Text-only operating claims (occupancy, NOI, SFF) are left out: those rules read CMS numbers. Rows with no
    facility (portfolio, property NOT FOUND) stay facts only."""
    out, hints = [], []
    for r in conn.execute("select * from private.licensed_fact where facility_id is not null and list_change ilike 'new%%'"
                          + _fac_filter(fids), {"fids": fids}).fetchall():
        fid, key, url = str(r["facility_id"]), r["fact_key"], "licensed:provider"
        tag = f" [{r['source_label']}]"
        proof = bool(r["court_case"] or r["family_id"])
        case = r["court_case"] or (f"bankruptcy family {r['family_id']}" if r["family_id"] else None)
        types = " ".join(r["distress_types"] or []).lower()
        reported = r["article_date"]

        def add(stype, when, text, cat):
            out.append(Sig(fid, stype, stage_of(stype), f"licfact:{stype}:{key}", url, when, None, text + tag, cat))

        chap = (r["chapter"] or "").split()[0] if r["chapter"] else None
        if proof and "chapter_11" in types and chap in ("11", "7"):
            add(f"chapter_{chap}", r["filed_date"] or reported,
                f"{r['debtor']}: Chapter {r['chapter']} filed {r['filed_date'] or 'date NOT FOUND'} ({case})", "legal")
        if proof and "foreclosure" in types:
            add("foreclosure", reported, f"{r['debtor']}: foreclosure ({r['stage_text'] or 'stage NOT FOUND'}; "
                f"lender {r['lender'] or 'NOT FOUND'}; {case})", "legal")
        if proof and "receivership" in types:
            add("receivership", reported, f"{r['debtor']}: receivership ({case})", "legal")
        if re.search(r"\b(bond|loan) default\b", types):
            add("loan_or_lease_default", reported,
                f"{r['debtor']}: {next(t for t in r['distress_types'] if 'default' in t.lower())} "
                f"(lender {r['lender'] or 'NOT FOUND'}; {r['key_amounts'] or 'amounts NOT FOUND'})", "debt_clock")
        buyer = re.split(r"\s+\(", r["buyer_name"])[0].strip() if r["buyer_name"] else None
        status_txt = (r["buyer_status"] or "").lower()
        if buyer and "new buyer" in (r["list_change"] or "").lower():
            m = re.search(r"(\d{4}-\d{2}-\d{2})", status_txt)
            when = date.fromisoformat(m[1]) if m else reported
            named = status_txt.startswith(("approved", "closed"))
            hints.append(BuyerHint(fid, buyer, "named" if named else "candidate", url, "licensed", when, None, "auto",
                                   1.0, r["family_id"], None))
            if proof and named:
                add("court_sale_process", when, f"{r['debtor']}: court {status_txt.split(';')[0]} sale to {buyer} "
                    f"({case})", "legal")
    return out, hints


# ── news: extraction → distress / market_sale, buyers, operator changes ──────────────────────────────────────────
def news_signals(conn, fids=None) -> tuple[list[Sig], list[BuyerHint], dict]:
    out, hints = [], []
    ROUNDUP_DROPPED.clear()
    changes = defaultdict(list)
    sale_orgs = {(r["facility_id"], r["source_url"]): r["buyer"] for r in conn.execute(
        "select facility_id::text, source_url, buyer_org_id::text buyer from core.ownership_event "
        "where event_type = 'news_reported_sale' and buyer_org_id is not null").fetchall()}
    rows = conn.execute(
        "select d.id doc_id, d.source_id, d.source_url, d.published_at::date pub, d.title, e.fields, "
        "x.facility_id::text, x.review_status, x.match_score, x.source_key xkey, "
        "case when e.fields->>'buyer_or_new_operator' is not null then d.body_text end body, "
        "(select f.canonical_name from core.facility f where f.facility_id = x.facility_id) fac_name "
        "from stg.extraction e join raw.document d on d.id = e.document_id "
        "join core.facility_xref x on x.source_id = d.source_id "
        "  and (x.source_key = 'doc:' || d.id or x.source_key like 'doc:' || d.id || ':%%') "
        "where e.fields->>'status' = 'extracted' and e.model = %(model)s and d.source_id = any(%(src)s) "
        "and x.review_status <> 'rejected'" + _fac_filter(fids).replace("facility_id", "x.facility_id"),
        {"src": list(NEWS_SOURCES), "fids": fids, "model": EXTRACTION_MODEL}).fetchall()
    for r in rows:
        f, fid, url = r["fields"], r["facility_id"], r["source_url"]
        if not _http(url):
            continue
        dts = f.get("distress_type")
        types = rules.news_signal_types(dts, f.get("buyer_or_new_operator"), f.get("lender"), f.get("stage_text"))
        link_nr = r["review_status"] == "needs_review"
        what = (f.get("stage_text") if rules.is_found(f.get("stage_text")) else r["title"]) or ""
        unverified = "distress_type" in (f.get("unverified_fields") or [])
        for st, stage in types:
            s = Sig(fid, st, stage, f"news:{r['source_id']}:{r['doc_id']}:{fid}:{st}", url, r["pub"], None,
                    (what[:400] + (" [chapter not stated]" if "bankruptcy_other" in (dts or []) and st == "chapter_11"
                                   else "")), "news", "ai", 0.5 if unverified else 1.0)
            if link_nr:
                s.link_status = "needs_review"
                s.evidence_text += f" [facility link needs review, score {r['match_score']}]"
            if stage == "sale" and st != "market_sale" and not link_nr and rules.news_reports_closing(what):
                s.sale_status = "closed"          # the article reports a finished sale (date: stated, else publication)
                s.sale_date = rules.news_change_date(f.get("event_dates"), r["pub"])
            out.append(s)
        buyer = f.get("buyer_or_new_operator")
        # roundup rule (AUDIT ONLY, see docs/ROADMAP.md): in a multi-deal article the buyer should be named in the same
        # deal section as the property. The check is recorded, not applied: it still has false alarms.
        if rules.is_found(buyer) and rules.buyer_in_property_section(r["body"], r["fac_name"], buyer) is False:
            ROUNDUP_DROPPED[fid] = buyer.strip()
        # an operator change / buyer from an article needs a confirmed building link (a 75-89 fuzzy match that
        # still awaits review only shows the distress, flagged): conservative, Wave 4
        if rules.is_found(buyer) and (types or "operator_change" in (dts or [])) and not link_nr:
            hints.append(BuyerHint(fid, buyer.strip(), rules.news_buyer_status(f.get("stage_text")), url, "news",
                                   rules.news_change_date(f.get("event_dates"), r["pub"]), sale_orgs.get((fid, url)),
                                   "needs_review" if link_nr else "auto"))
        if isinstance(dts, list) and "operator_change" in dts and r["pub"] and not link_nr:
            changes[fid].append((r["pub"], url, "news-reported new operator"))
    # WARN notices: the notice itself is the closure / layoff signal
    for r in conn.execute(
            "select d.id doc_id, d.source_url, coalesce(d.published_at, d.fetched_at)::date pub, d.title, "
            "x.facility_id::text, x.review_status from raw.document d join core.facility_xref x "
            "on x.source_id = d.source_id and x.source_key = 'doc:' || d.id where d.source_id = 'warn'"
            + _fac_filter(fids).replace("facility_id", "x.facility_id"), {"fids": fids}).fetchall():
        if _http(r["source_url"]):
            s = Sig(r["facility_id"], "warn_closure", stage_of("warn_closure"), f"warn:{r['doc_id']}:{r['facility_id']}",
                    r["source_url"], r["pub"], None, f"WARN layoff / closure notice: {r['title']}", "news")
            s.link_status = "needs_review" if r["review_status"] == "needs_review" else "auto"
            out.append(s)
    return out, hints, changes


# ── legal: bankruptcy families + court sale processes ────────────────────────────────────────────────────────────
def _family_case_links(conn) -> dict[int, list[dict]]:
    """family_id -> case-facility links [{case court/docket, docket_url, facility_id, link_method, review_status}]."""
    from src.enrich.steps import _case_keys      # same court/docket reconciliation core-builder used (read-only)
    members = conn.execute(
        "select m.family_id, sc.id, sc.court, sc.docket_no, sc.debtor_name, sc.case_name, sc.chapter "
        "from core.case_family_member m join stg.court_case sc on sc.id = m.stg_court_case_id").fetchall()
    keys = _case_keys(conn, members)
    cases = {(r["court"], r["docket_no"]): r for r in conn.execute(
        "select case_id::text, court, docket_no, chapter, docket_url, claims_agent_url, debtor_name from core.legal_case").fetchall()}
    cf = defaultdict(list)
    for r in conn.execute("select case_id::text, facility_id::text, link_method, match_score from core.case_facility").fetchall():
        cf[r["case_id"]].append(r)
    # one status per (facility, case) across court sources: a human 'rejected' beats everything, then 'confirmed'
    xr = {}
    prio = {"rejected": 3, "confirmed": 2, "needs_review": 1, "auto": 0}
    for r in conn.execute("select facility_id::text, source_key, review_status from core.facility_xref "
                          "where source_id = any(%(s)s)", {"s": list(COURT_SOURCES)}).fetchall():
        k = (r["facility_id"], r["source_key"].split("#")[0])     # hand entries: '<court>:<docket>#<home name>'
        if prio[r["review_status"]] >= prio.get(xr.get(k), -1):
            xr[k] = r["review_status"]
    # CMS ownership chain: the facility's own operator / licensee orgs (normalized names)
    operators = defaultdict(set)
    op_stems = defaultdict(set)              # trust / master-tenant variants of the same entity (entity_stem)
    for r in conn.execute("select r.facility_id::text, o.name from core.facility_role r join core.organization o "
                          "using (org_id) where r.role in ('operator','licensee')").fetchall():
        operators[r["facility_id"]].add(normalize_name(r["name"]))
        op_stems[r["facility_id"]].add(entity_stem(r["name"]))
    out = defaultdict(list)
    for m in members:
        key = keys.get(m["id"])
        lc = cases.get(key) if key else None
        if not lc:
            continue
        debtor = normalize_name(m["debtor_name"] or m["case_name"])
        for link in cf.get(lc["case_id"], []):
            fid, method = link["facility_id"], link["link_method"] or "facility_name"
            rs = xr.get((fid, f"{key[0]}:{key[1]}"), "auto") if method == "facility_name" else "auto"
            if rs == "rejected":
                continue                       # a person said this case is not this building
            if rs == "confirmed":
                confirmed_by = "human"
            elif debtor and debtor in operators.get(fid, ()):
                confirmed_by = "cms_operator"
            elif debtor and len(entity_stem(debtor).split()) >= 3 and entity_stem(debtor) in op_stems.get(fid, ()):
                confirmed_by = "cms_operator"    # same entity as the home's operator / licence holder (DST vs MT name)
            elif method == "debtor_org":
                confirmed_by = "cms_ownership"   # debtor is a parent owner: portfolio link
            else:
                confirmed_by = None             # facility name only
            out[m["family_id"]].append({
                "facility_id": fid, "link_method": method,
                "match_score": link["match_score"], "review_status": rs, "chapter": lc["chapter"] or m["chapter"],
                "url": lc["docket_url"] if _http(lc["docket_url"]) else lc["claims_agent_url"],
                "docket": f"{key[0]}:{key[1]}", "confirmed_by": confirmed_by,
                "debtor": lc["debtor_name"]})
    _add_manual_links(conn, members, keys, cases, out)
    return out


def _add_manual_links(conn, members, keys, cases, out) -> None:
    """Hand-entered court → facility links from config/court_facts.yaml (petition / claims-agent addresses)."""
    manual = rules.court_facts().get("links") or []
    if not manual:
        return
    by_ccn = {r["ccn"]: r["facility_id"] for r in conn.execute(
        "select ccn, facility_id::text from core.facility where ccn = any(%(c)s)",
        {"c": [str(x["ccn"]) for x in manual]}).fetchall()}
    fams = defaultdict(set)
    chapters = {}
    for m in members:
        key = keys.get(m["id"])
        if key and key in cases:
            fams[f"{key[0]}:{key[1]}"].add(m["family_id"])
            chapters[f"{key[0]}:{key[1]}"] = cases[key]["chapter"] or m["chapter"]
    for x in manual:
        fid = by_ccn.get(str(x["ccn"]))
        if not fid:
            raise ValueError(f"config/court_facts.yaml: CCN {x['ccn']} is not in core.facility")
        for fam in fams.get(x["case"], ()):
            out[fam] = [l for l in out[fam] if l["facility_id"] != fid]
            out[fam].append({"facility_id": fid, "link_method": "manual", "match_score": None,
                             "review_status": "confirmed", "chapter": chapters[x["case"]], "url": x["source_url"],
                             "docket": x["case"], "confirmed_by": x.get("confirmed_by", "petition")})


FORMER_OPERATOR: set = set()     # facilities whose parent-case link was dropped by the former-operator rule (last build)
WRONG_DOCKET: set = set()        # facilities whose docket link named another case and was replaced (last build)
ROUNDUP_DROPPED: dict = {}       # facility -> buyer the roundup check could not place in the property's section (audit only)


def legal_signals(conn, fids=None) -> tuple[list[Sig], list[BuyerHint], dict]:
    FORMER_OPERATOR.clear()
    WRONG_DOCKET.clear()
    """Gate 2: only senior_housing 'yes' (full weight) or 'maybe' (lower weight + flag) families filed within
    36 months create legal signals; older families are context only. A link confirmed by address / CCN / the CMS
    operator = full weight; a link through a parent owner org = debtor_org_link_weight (portfolio_link); a link by
    facility name alone = full weight but flagged court_name_only (the scorer caps it). config/court_facts.yaml adds
    hand-checked links, sale facts (stage, hearing date, stalking horses) and excludes wrong sale evidence."""
    out, hints, skipped = [], [], defaultdict(int)
    fams = {r["family_id"]: r for r in conn.execute("select * from core.case_family").fetchall()}
    links = _family_case_links(conn)
    fam_of_case = {l["docket"]: f for f, ls in links.items() for l in ls}
    facts = rules.court_facts()
    excluded = {(fam_of_case.get(x["case"]), x["source_url"]) for x in facts.get("excluded_sale_evidence") or []}
    sales = defaultdict(list)
    for r in conn.execute("select * from core.sale_check where sale_process_found").fetchall():
        if (r["family_id"], r["source_url"]) in excluded:
            skipped["sale_evidence_excluded_by_hand"] += 1
            continue
        sales[r["family_id"]].append(r)
    # Nov 11 hand check, former-operator rule: a home that CMS shows changing hands to a non-debtor BEFORE the petition
    # date left the debtor before the bankruptcy; the parent's case / sale is not attached to it.
    from src.enrich.steps import same_party
    chows = defaultdict(list)
    for r in conn.execute("select e.facility_id::text f, e.effective_date d, o.name from core.ownership_event e "
                          "join core.organization o on o.org_id = e.buyer_org_id where e.event_type = 'chow' "
                          "and e.effective_date is not null").fetchall():
        chows[r["f"]].append((r["d"], r["name"]))
    debtors = defaultdict(set)
    for r in conn.execute("select f.family_id, f.family_name n from core.case_family f union all "
                          "select m.family_id, coalesce(sc.debtor_name, sc.case_name) from core.case_family_member m "
                          "join stg.court_case sc on sc.id = m.stg_court_case_id").fetchall():
        if r["n"]:
            debtors[r["family_id"]].add(r["n"])
    fac_names = {r["f"]: (r["canonical_name"], r["city"]) for r in conn.execute(
        "select facility_id::text f, canonical_name, city from core.facility where facility_id in "
        "(select facility_id from core.case_facility)").fetchall()}

    undated = defaultdict(list)                # CHOW rows with no date: never enough to drop a signal
    for r in conn.execute("select e.facility_id::text f, o.name from core.ownership_event e join core.organization o "
                          "on o.org_id = e.buyer_org_id where e.event_type = 'chow' and e.effective_date is null").fetchall():
        undated[r["f"]].append(r["name"])
    open_roles = defaultdict(set)              # orgs CMS still lists (no end date) as operator / owner of the home
    for r in conn.execute("select r.facility_id::text f, o.name from core.facility_role r join core.organization o "
                          "using (org_id) where r.role in ('operator','licensee','owner','indirect_owner') "
                          "and r.end_date is null").fetchall():
        open_roles[r["f"]].add(r["name"])

    def former_operator(fid, fam_id, filed):
        """'left' = CMS dates a change of ownership to a non-debtor BEFORE the petition and no debtor is still listed
        on the home; 'suspect' = the same picture but a date is missing or a debtor is still listed; else None."""
        ds = debtors.get(fam_id, ())
        non_debtor = lambda n: not any(same_party(n, d) for d in ds)      # noqa: E731
        if not filed:
            return "suspect" if any(non_debtor(n) for _, n in chows.get(fid, [])) or \
                any(non_debtor(n) for n in undated.get(fid, [])) else None
        before = sorted(x for x in chows.get(fid, []) if x[0] < filed)
        if before and non_debtor(before[-1][1]):
            still = any(same_party(n, d) for n in open_roles.get(fid, ()) for d in ds)
            return "suspect" if still else "left"
        if not before and any(non_debtor(n) for n in undated.get(fid, [])):
            return "suspect"
        return None
    manual_sales = {}                       # family_id -> hand-checked sale facts (config/court_facts.yaml)
    for x in facts.get("sales") or []:
        fam = fam_of_case.get(x["case"])
        if fam is None:
            skipped["manual_sale_case_not_linked"] += 1
            continue
        if x["stage_text"] not in rules.SALE_STAGE_ORDER:
            raise ValueError(f"config/court_facts.yaml sales: unknown stage_text {x['stage_text']!r}")
        manual_sales[fam] = x
        sales[fam].append({"channel": "manual", "stage_text": x["stage_text"], "buyer_name": None,
                           "source_url": x["source_url"], "evidence_text": x.get("evidence_text") or ""})
    for fam_id, fam in fams.items():
        if fam["senior_housing"] not in ("yes", "maybe") or fam["within_36m"] is not True:
            skipped["older_or_not_senior"] += 1
            continue
        fam_w = 1.0 if fam["senior_housing"] == "yes" else rules.wave3("senior_housing_maybe_weight")
        # best link per facility: address/CCN-confirmed first, then facility_name over debtor_org, auto over needs_review
        best = {}
        for l in links.get(fam_id, []):
            if fids is not None and l["facility_id"] not in fids:
                continue
            rank = (l["confirmed_by"] in rules.DIRECT_CONFIRMATIONS, l["link_method"] != "debtor_org",
                    l["review_status"] in ("auto", "confirmed"), _http(l["url"]))
            if l["facility_id"] not in best or rank > best[l["facility_id"]][0]:
                best[l["facility_id"]] = (rank, l)
        if not best:
            skipped["no_facility_link"] += 1
            continue
        chapters = {str(l["chapter"]) for l in links[fam_id] if l["chapter"]}
        btype = "chapter_11" if "11" in chapters else "chapter_7" if "7" in chapters else None
        # sale process: latest stage across channels, ignoring hospital / pharmacy-only asset sales
        good = [s for s in sales.get(fam_id, []) if not rules.is_non_senior_sale(s["evidence_text"]) and _http(s["source_url"])]
        if sales.get(fam_id) and not good:
            skipped["sale_excluded_non_senior_asset"] += 1
        # hand-checked facts beat machine-read ones; court / claims-agent evidence beats a news article
        good = [s for s in good if s["channel"] == "manual"] or [s for s in good if s["channel"] != "news"] or good
        top_sale = max(good, key=lambda s: rules.sale_stage_rank(s["stage_text"])) if good else None
        buyer_sale = next((s for s in sorted(good, key=lambda s: -rules.sale_stage_rank(s["stage_text"]))
                           if rules.is_found(s["buyer_name"])), None)
        msale = manual_sales.get(fam_id)
        hearing = f"; sale hearing {msale['sale_hearing']}" if msale and msale.get("sale_hearing") else ""
        sale_status = rules.court_sale_status(top_sale["stage_text"]) if top_sale else None
        sale_date = None
        if sale_status:
            sale_date = (date.fromisoformat(str(msale["event_date"])) if msale and top_sale.get("channel") == "manual"
                         and msale.get("event_date") else rules.sale_order_date(top_sale))
        month_only = bool(sale_status) and not (msale and top_sale.get("channel") == "manual" and msale.get("event_date")) \
            and rules.sale_date_month_only(top_sale)
        for fid, (_, l) in best.items():
            conf = l["confirmed_by"]
            former = former_operator(fid, fam_id, fam["filed_date"]) if conf != "human" and l["link_method"] != "manual" else None
            if former == "left":
                skipped["former_operator_chow_before_petition"] += 1
                FORMER_OPERATOR.add(fid)
                continue
            direct = conf in rules.DIRECT_CONFIRMATIONS
            portfolio = l["link_method"] == "debtor_org" and not direct
            w = fam_w * (rules.wave3("debtor_org_link_weight") if portfolio else 1.0)
            flags = (["senior_housing_maybe"] if fam["senior_housing"] == "maybe" else []) + \
                    (["portfolio_link"] if portfolio else []) + \
                    (["court_name_only"] if conf is None else []) + \
                    (["former_operator_suspect"] if former == "suspect" else [])
            if former == "suspect":
                skipped["former_operator_suspect_kept"] += 1
            how = {"human": "a person checked it",
                   "cms_operator": "debtor is this facility's operator in CMS",
                   "petition": "the petition lists this address",
                   "claims_agent": "the claims agent lists this address",
                   "cms_ownership": "debtor is this facility's parent owner in CMS"}.get(
                conf, "facility name only" + (f" {l['match_score']}" if l["match_score"] else "") + "; not confirmed")
            nr = l["review_status"] == "needs_review" and not direct
            url = l["url"] if _http(l["url"]) else fam["claims_agent_url"]
            # links rule: never attach a docket whose own case name is another debtor / property
            if rules.docket_names_other_case(url, l.get("debtor"), fam["family_name"], *fac_names.get(fid, ())):
                alt = fam["claims_agent_url"] if _http(fam["claims_agent_url"]) else (top_sale or {}).get("source_url")
                if _http(alt):
                    url = alt
                    skipped["docket_of_other_case_replaced"] += 1
                    WRONG_DOCKET.add(fid)
            if btype and _http(url):
                s = Sig(fid, btype, stage_of(btype), f"fam:{fam_id}:{fid}:{btype}", url, fam["filed_date"], None,
                        f"{fam['family_name']}: Chapter {btype.split('_')[1]} filed {fam['filed_date'] or 'date NOT FOUND'} "
                        f"(linked: {how})", "legal", "code", w,
                        "needs_review" if nr else "auto", l["link_method"], fam_id, fam["family_name"], flags, conf)
                out.append(s)
            if top_sale:
                s = Sig(fid, "court_sale_process", stage_of("court_sale_process"),
                        f"fam:{fam_id}:{fid}:court_sale_process", top_sale["source_url"], None,
                        rules.sale_stage_rank(top_sale["stage_text"]),
                        f"{fam['family_name']}: {top_sale['stage_text']}{hearing} — {top_sale['evidence_text'] or ''}"[:500],
                        "legal", "code", w, "needs_review" if nr else "auto", l["link_method"], fam_id,
                        fam["family_name"], flags, conf, sale_status, sale_date, month_only)
                out.append(s)
            if buyer_sale:
                hints.append(BuyerHint(fid, buyer_sale["buyer_name"].strip(),
                                       rules.court_buyer_status(buyer_sale["stage_text"]), buyer_sale["source_url"],
                                       "court_sale", rules.sale_order_date(buyer_sale), None, "needs_review" if nr else "auto", w,
                                       fam_id))
            if msale and msale.get("buyer_all_homes"):
                hints.append(BuyerHint(fid, msale["buyer_all_homes"].strip(),
                                       msale.get("buyer_status") or rules.court_buyer_status(msale["stage_text"]),
                                       msale["source_url"], "manual",
                                       date.fromisoformat(str(msale["event_date"])) if msale.get("event_date")
                                       else None, None, "auto", w, fam_id))
    hints += manual_buyer_hints(conn, facts, fids)
    return out, hints, dict(skipped)


def manual_buyer_hints(conn, facts: dict, fids=None) -> list[BuyerHint]:
    """Hand-checked buyers (config/court_facts.yaml `buyers`): a facility by CCN or by its hand-entered court xref."""
    out = []
    for b in facts.get("buyers") or []:
        if b.get("ccn"):
            row = conn.execute("select facility_id::text from core.facility where ccn = %s", (str(b["ccn"]),)).fetchone()
        else:
            # one home of a multi-home case: its hand-entry key '<court>:<docket>#<home name>'
            key = f"{b['case']}#{normalize_name(b['home'])}" if b.get("home") else b["case"]
            row = conn.execute("select facility_id::text from core.facility_xref where source_key = %s "
                               "and match_method = 'manual'", (key,)).fetchone()
        if not row:
            raise ValueError(f"config/court_facts.yaml buyers: no facility for {b.get('ccn') or b.get('case')} "
                             "(run python -m src.enrich --step manual first)")
        if fids is not None and row["facility_id"] not in fids:
            continue
        ev = b.get("event_date")
        out.append(BuyerHint(row["facility_id"], b["name"].strip(), b.get("status", "named"), b["source_url"], "manual",
                             date.fromisoformat(str(ev)) if ev else None, None, "auto", 1.0, None, b.get("parent")))
    return out


PUBLIC_EVENT_CATEGORY = {"foreclosure": "legal", "receivership": "legal", "deed_in_lieu": "legal", "closure": "news",
                         "price_collapse": "news", "lender_takeback": "debt_clock", "loan_or_lease_default": "debt_clock"}


def public_event_signals(conn, fids=None) -> list[Sig]:
    """Hand-checked PUBLIC distress events (config/court_facts.yaml `public_events`). Signals only: a foreclosing
    lender that takes the property back is recorded as context, never as a buyer hint."""
    out = []
    for i, e in enumerate(rules.court_facts().get("public_events") or []):
        if not _http(e.get("source_url")):
            raise ValueError(f"config/court_facts.yaml public_events: public source_url missing ({e})")
        if e.get("ccn"):
            row = conn.execute("select facility_id::text from core.facility where ccn = %s", (str(e["ccn"]),)).fetchone()
        else:
            row = conn.execute("select facility_id::text from core.facility_xref where source_key = %s "
                               "and source_id = 'court_manual'", (e["case"],)).fetchone()
        if not row:
            raise ValueError(f"config/court_facts.yaml public_events: no facility for {e.get('ccn') or e.get('case')}")
        if fids is not None and row["facility_id"] not in fids:
            continue
        st = e["signal_type"]
        ev = e.get("event_date")
        text = e.get("evidence_text") or ""
        if e.get("owner_after"):
            text += f" Owner after: {e['owner_after']}."
        out.append(Sig(row["facility_id"], st, stage_of(st), f"public_event:{e.get('case') or e.get('ccn')}:{st}:{ev}",
                       e["source_url"], date.fromisoformat(str(ev)) if ev else None,
                       float(e["value"]) if e.get("value") is not None else None, text,
                       CATEGORY.get(st) or PUBLIC_EVENT_CATEGORY.get(st, "news"), "code", 1.0, "auto", "manual",
                       flags=list(e.get("flags") or [])))
    return out


# ── all ──────────────────────────────────────────────────────────────────────────────────────────────────────────
def build(conn, today: date | None = None, fids: list[str] | None = None):
    """Return (signals, buyer_hints, stats). `fids` restricts to a facility sample (--limit)."""
    today = today or date.today()
    sigs = metric_signals(conn, fids)
    news, news_hints, changes = news_signals(conn, fids)
    own, own_hints = ownership_signals(conn, today, changes, fids)
    hud = hud_signals(conn, fids)
    legal, legal_hints, skipped = legal_signals(conn, fids)
    fact_sigs, fact_hints = licensed_fact_signals(conn, fids)
    licensed = licensed_signals(conn, today, fids) + licensed_sale_signals(conn, fids) + fact_sigs
    closures = closure_signals(conn, today, fids)
    sigs += news + own + hud + legal + licensed + closures + public_event_signals(conn, fids)
    sigs += license_signals(conn, today, sigs, fids)          # needs the distress signals above
    # hard rule 1: a web link, or a licensed-source tag (private facts; such leads are hidden from the public view)
    sigs = [s for s in sigs if _http(s.source_url) or str(s.source_url).startswith("licensed:")]
    seen, uniq = set(), []
    for s in sigs:
        if s.source_key not in seen:
            seen.add(s.source_key)
            uniq.append(s)
    hints = news_hints + own_hints + legal_hints + licensed_buyer_hints(conn, legal, fids) + fact_hints
    from . import overrides
    uniq, hints, ov = overrides.apply(conn, uniq, hints, fids)
    return uniq, hints, {"legal_skipped": skipped, "former_operator_rule": len(FORMER_OPERATOR),
                         "wrong_docket_replaced": len(WRONG_DOCKET), "roundup_buyers_suspect_not_applied": len(ROUNDUP_DROPPED),
                         "overrides": ov}


COLS = ["facility_id", "category", "signal_type", "stage", "event_date", "value", "evidence_text", "source_url",
        "detected_by", "confidence", "source_key"]


def write_signals(conn, sigs: list[Sig], *, prune_metrics: bool = True) -> dict:
    rows = [[s.facility_id, s.category, s.signal_type, s.stage, s.event_date, s.value,
             (s.evidence_text or "")[:2000], s.source_url, s.detected_by, round(s.weight, 3), s.source_key]
            for s in sigs]
    before = conn.execute("select count(*) n from core.signal").fetchone()["n"]
    bulk_upsert(conn, "core.signal", COLS, rows, ["source_key"])
    after_insert = conn.execute("select count(*) n from core.signal").fetchone()["n"]
    pruned = 0
    if prune_metrics:
        # latest-only retention: a metric signal whose facility no longer qualifies is removed
        keep = [s.source_key for s in sigs if s.signal_type in METRIC_SIGNALS]
        pruned = conn.execute("delete from core.signal where signal_type = any(%(t)s) and not (source_key = any(%(k)s))",
                              {"t": list(METRIC_SIGNALS), "k": keep}).rowcount
    # AI-read news signals come from the current extraction model only (Gate 3: Sonnet). A news signal the current
    # model no longer produces is a stale reading of the same article, not a past event, so it is removed.
    keep_ai = [s.source_key for s in sigs if s.detected_by == "ai"]
    pruned_ai = 0
    if prune_metrics:
        pruned_ai = conn.execute("delete from core.signal where detected_by = 'ai' and source_key like 'news:%%' "
                                 "and not (source_key = any(%(k)s))", {"k": keep_ai}).rowcount
    conn.commit()
    return {"signals_built": len(sigs), "inserted": after_insert - before, "pruned_metric": pruned,
            "pruned_stale_ai": pruned_ai}
