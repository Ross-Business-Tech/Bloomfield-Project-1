"""Pure rules for Wave 3 (no database): signal stages, distress mapping, buyer status, score components.

Every number comes from config/thresholds.yaml. A few Gate 2 / Wave 3 decisions have no key in that file yet;
they live in WAVE3_DEFAULTS below, are labeled ASSUMPTION, and are listed in the Wave 3 report as config requests
(config/ is owned by the orchestrator). A key added to thresholds.yaml under `scoring:` with the same name wins.
"""
from __future__ import annotations

import re
from functools import lru_cache
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import yaml

from src.common.config import thresholds

STAGES = ("early", "middle", "late", "sale")

# Gate 2 retention decision: metric signals keep only the latest value per facility + type.
METRIC_SIGNALS = {"low_occupancy", "negative_noi", "staffing_drop", "sff_or_candidate", "dscr_below_1",
                  "maturity_12m_low_dscr"}
# Not distress: a broker-arranged market sale alone never creates a lead.
NON_DISTRESS = {"market_sale"}

CATEGORY = {
    "low_occupancy": "operating", "negative_noi": "operating", "staffing_drop": "operating",
    "sff_or_candidate": "operating", "operator_turnover": "ownership", "hud_loan_terminated": "debt_clock", "hud_delinquency": "debt_clock",
    "special_servicing": "debt_clock", "lender_takeback": "debt_clock", "maturity_default": "debt_clock",
    "dscr_below_1": "debt_clock", "maturity_12m_low_dscr": "debt_clock", "warn_closure": "news",
    "court_sale_process": "legal", "market_sale": "news",
}

# ASSUMPTION (Wave 3): numbers with no key in thresholds.yaml yet. Requested as config keys in the Wave 3 report.
WAVE3_DEFAULTS = {
    # stages for the signal types added by migration 20261110000400 (not in stage_of_signal yet)
    "stage_of_new_signals": {"chapter_7": "late", "loan_or_lease_default": "middle", "closure": "middle",
                             "court_sale_process": "sale", "market_sale": "sale"},
    "market_sale_bonus": 5,              # a broker sale adds a little, never creates a lead
    "court_sale_priority": 100,          # human decision: court sale processes go to the top of the list
    "debtor_org_link_weight": 0.5,       # case linked through the parent/debtor org, not the facility name
    "senior_housing_maybe_weight": 0.5,  # bankruptcy family only 'maybe' senior housing
    "family_top_n": 3,                   # at most 3 leads per bankruptcy family keep the family's full points
    "family_overflow_weight": 0.1,       # ... the rest keep 10% of family stage/priority/buyer points (flag family_capped)
    "lead_window_months": 36,            # an event older than this is context only (same 36 months as Gate 2)
    "occupancy_max_plausible": 1.0,
    "early_main_min_signals": 3,         # Gate 3 decision 2d      # cost-report occupancy above 100% is a data error, not a signal
}


def cfg() -> dict:
    return thresholds()


# ── court links: how a bankruptcy case is tied to a facility ─────────────────────────────────────────────────────
# Gate 3 top-5 check: 3 of 5 court links made by facility NAME alone were wrong. A court link now counts as
# confirmed only when an address / CCN source ties the debtor to that building:
#   human        a person confirmed it (python -m src.enrich.review)
#   cms_operator the debtor is the facility's operator / licensee in CMS (ownership chain)
#   petition / claims_agent   a hand-entered link from the petition or claims agent (config/court_facts.yaml)
# A debtor that is only a parent owner in CMS is a portfolio link ('cms_ownership'): confirmed but lower weight.
DIRECT_CONFIRMATIONS = ("human", "cms_operator", "petition", "claims_agent")
_COURT_FACTS = Path(__file__).resolve().parents[2] / "config" / "court_facts.yaml"


def court_facts() -> dict:
    """Hand-checked court facts (links, sale facts, excluded evidence). Empty when the file is missing."""
    if not _COURT_FACTS.exists():
        return {}
    data = yaml.safe_load(_COURT_FACTS.read_text()) or {}
    for section in ("links", "sales", "buyers", "facilities", "excluded_sale_evidence"):
        for item in data.get(section) or []:
            if not str(item.get("source_url", "")).startswith(("http://", "https://")):
                raise ValueError(f"config/court_facts.yaml {section}: every entry needs a source_url ({item})")
    return data


def court_name_only_cfg() -> dict:
    return {"score_cap": 60, "keep_out_of_top": 50, **(cfg().get("scoring", {}).get("court_name_only") or {})}


def wave3(key: str):
    return cfg().get("scoring", {}).get(key, WAVE3_DEFAULTS[key])


def stage_of(signal_type: str) -> str:
    s = cfg().get("stage_of_signal", {}).get(signal_type) or WAVE3_DEFAULTS["stage_of_new_signals"].get(signal_type)
    if s not in STAGES:
        raise KeyError(f"no stage configured for signal type {signal_type}")
    return s


def months_between(earlier: date, later: date) -> float:
    return (later.year - earlier.year) * 12 + (later.month - earlier.month) + (later.day - earlier.day) / 30.0


# ── metric rules (BUILD_SPEC step 5) ─────────────────────────────────────────────────────────────────────────────
def is_low_occupancy(value) -> bool:
    if value is None:
        return False
    v = float(value)
    return 0 < v <= wave3("occupancy_max_plausible") and v < cfg()["signals"]["low_occupancy"]["occupancy_below"]


def is_negative_noi(value) -> bool:
    return value is not None and float(value) < cfg()["signals"]["negative_noi"]["net_income_below"]


def census_drop(series: list[tuple[date, float]]) -> float | None:
    """series = [(period_end, avg_census)] any order. Returns the fractional drop from the value
    `over_quarters` quarters before the latest one to the latest, or None if that history is missing."""
    q = cfg()["signals"]["staffing_drop"]["over_quarters"]
    pts = sorted((d, float(v)) for d, v in series if v is not None)
    if len(pts) < q + 1:
        return None
    (d0, v0), (d1, v1) = pts[-(q + 1)], pts[-1]
    if v0 <= 0 or months_between(d0, d1) < 3 * q - 1:
        return None
    return (v0 - v1) / v0


def is_staffing_drop(series) -> bool:
    drop = census_drop(series)
    return drop is not None and drop > cfg()["signals"]["staffing_drop"]["census_drop_pct"]


def operator_changes_qualify(change_dates: list[date], today: date) -> bool:
    """>= min_changes CHOW / licensee / new-operator changes within within_months (BUILD_SPEC step 5)."""
    rule = cfg()["signals"]["operator_turnover"]
    recent = {d for d in change_dates if d and months_between(d, today) <= rule["within_months"]}
    return len(recent) >= rule["min_changes"]


# ── news extraction → signals (Gate 2 decision #3) ───────────────────────────────────────────────────────────────
EXTRACTION_DISTRESS = {
    "chapter_11": "chapter_11", "bankruptcy_other": "chapter_11", "chapter_7": "chapter_7",
    "receivership": "receivership", "foreclosure": "foreclosure", "deed_in_lieu": "deed_in_lieu",
    "loan_or_lease_default": "loan_or_lease_default", "closure": "closure",
}


_LENDER_TAKEBACK = re.compile(r"\b(sheriff'?s?\s+sale|REO|real estate owned|credit[- ]bid|deed in lieu|"
                              r"(taken|took|acquired|bought|repossessed)\s+(back\s+)?by\s+(the\s+)?(lender|bank|mortgagee))\b",
                              re.I)


def is_lender_takeback(buyer=None, lender=None, stage_text=None) -> bool:
    """Gate 3 decision: a lender taking the building back (REO / sheriff's sale / credit bid) is still 'late',
    not a 'sale' to a new owner. True when the buyer is the lender, or the stage text describes a takeback."""
    if is_found(buyer) and is_found(lender):
        b, l = (re.sub(r"[^a-z0-9 ]", "", x.lower()).strip() for x in (buyer, lender))
        if b and l and (b in l or l in b):
            return True
    return bool(_LENDER_TAKEBACK.search(stage_text or ""))


def news_signal_types(distress_types, buyer=None, lender=None, stage_text=None) -> list[tuple[str, str]]:
    """Extraction distress_type list → [(signal_type, stage)].

    - any bankruptcy / receivership / foreclosure / default / closure → that signal; if the same article also
      reports a sale, the distressed asset is being sold, so the stage is 'sale' — except a lender takeback
      (REO / sheriff's sale / credit bid, Gate 3 decision), which stays at the signal's own stage ('late').
    - 'sale' alone (broker / market sale) → market_sale (not distress).
    - 'operator_change' is not a signal by itself; it counts toward operator_turnover (handled by the caller).
    """
    if not isinstance(distress_types, list):
        return []
    types = {str(t) for t in distress_types}
    out = []
    has_sale = "sale" in types and not ({"foreclosure", "deed_in_lieu"} & types
                                        and is_lender_takeback(buyer, lender, stage_text))
    for t in sorted(types):
        st = EXTRACTION_DISTRESS.get(t)
        if st and st not in [o[0] for o in out]:
            out.append((st, "sale" if has_sale else stage_of(st)))
    if has_sale and not out:
        out.append(("market_sale", stage_of("market_sale")))
    return out


# ── court sale processes (Gate 2 decision #4) ────────────────────────────────────────────────────────────────────
SALE_STAGE_ORDER = ["sale motion", "bidding procedures", "stalking horse", "auction", "sale order", "sale closed"]
_NON_SENIOR_ASSET = re.compile(r"\b(hospital|hospitals|physician|pharmacy|proton|surgical|ambulatory)\b", re.I)
_SENIOR_ASSET = re.compile(r"\b(nursing|assisted living|senior living|skilled|memory care|ccrc|continuing care|"
                           r"long[- ]term care|retirement|rehabilitation center|care center|senior housing)\b", re.I)


def is_non_senior_sale(evidence_text: str | None) -> bool:
    """True when the sale evidence is about hospital / physician / pharmacy assets and never mentions a senior
    housing asset (e.g. Redeemer hospital sale, Pacifica of the Valley hospital assets, Midwest Christian's
    pharmacy). Such a sale is no buyer for a nursing home and creates no court_sale_process signal."""
    text = evidence_text or ""
    return bool(_NON_SENIOR_ASSET.search(text)) and not _SENIOR_ASSET.search(text)


def sale_stage_rank(stage_text: str | None) -> int:
    return SALE_STAGE_ORDER.index(stage_text) if stage_text in SALE_STAGE_ORDER else -1


def court_buyer_status(stage_text: str | None) -> str:
    """Buyer named in a sale order / closed sale = named; a proposed or stalking-horse bidder = candidate."""
    return "named" if stage_text in ("sale order", "sale closed") else "candidate"


_CANDIDATE_WORDS = re.compile(r"\b(propos\w*|stalking|bidder|bid|plans? to|intends? to|letter of intent|LOI|"
                              r"under contract|pending|expected to|will acquire|will take over|agreed to|"
                              r"seeks? approval|potential)\b", re.I)


def news_buyer_status(stage_text: str | None) -> str:
    return "candidate" if _CANDIDATE_WORDS.search(stage_text or "") else "named"


def is_found(value) -> bool:
    return isinstance(value, str) and value.strip() not in ("", "NOT FOUND")


# ── scoring (BUILD_SPEC step 7) ──────────────────────────────────────────────────────────────────────────────────
def recency_points(latest: date | None, today: date) -> float:
    r = cfg()["scoring"]["recency"]
    if latest is None:
        return 0.0
    m = max(months_between(latest, today), 0.0)
    if m <= r["full_points_within_months"]:
        return float(r["max_points"])
    if m >= r["zero_points_after_months"]:
        return 0.0
    span = r["zero_points_after_months"] - r["full_points_within_months"]
    return round(r["max_points"] * (r["zero_points_after_months"] - m) / span, 2)


def size_fit_points(units, beds) -> tuple[float, bool]:
    """(points, known). Units preferred, else beds."""
    s = cfg()["scoring"]["size_fit"]
    n = units if units else beds
    if not n:
        return 0.0, False
    return (float(s["points"]) if s["min_units"] <= n <= s["max_units"] else 0.0), True


def chain_quality_points(avg_overall_rating) -> float:
    """CMS 1-5 star average of the buyer's chain, scaled linearly to 0..buyer_chain_quality_max."""
    if avg_overall_rating is None:
        return 0.0
    mx = cfg()["scoring"]["buyer_chain_quality_max"]
    v = min(max(float(avg_overall_rating), 1.0), 5.0)
    return round(mx * (v - 1.0) / 4.0, 2)


def buyer_points(status: str) -> float:
    sc = cfg()["scoring"]
    undated = float(sc["buyer_named_bonus"]) * float(sc.get("buyer_undated_weight", 0.5))
    return float({"named": sc["buyer_named_bonus"], "named (undated)": undated,
                  "candidate": sc["buyer_candidate_bonus"]}.get(status, 0))


@dataclass
class Sig:
    """One signal as the scorer sees it (core.signal row + how it was linked)."""
    facility_id: str
    signal_type: str
    stage: str
    source_key: str
    source_url: str
    event_date: date | None = None
    value: float | None = None
    evidence_text: str | None = None
    category: str = "news"
    detected_by: str = "code"
    weight: float = 1.0                  # stored as core.signal.confidence
    link_status: str = "auto"            # 'needs_review' when the facility link is a 75-89 fuzzy match
    link_method: str | None = None       # 'facility_name' / 'debtor_org' / 'manual' for court links
    family_id: int | None = None
    family_name: str | None = None
    flags: list[str] = field(default_factory=list)
    confirmed_by: str | None = None      # court links: see DIRECT_CONFIRMATIONS; None = matched by name only

    @property
    def distress(self) -> bool:
        return self.signal_type not in NON_DISTRESS

    def stage_points(self) -> float:
        return cfg()["scoring"]["stage_weight"][self.stage] * self.weight


def in_lead_window(sig: Sig, today: date) -> bool:
    """Metric signals are always current (latest value only). An event older than lead_window_months is context."""
    if sig.signal_type in METRIC_SIGNALS or sig.event_date is None:
        return True
    return months_between(sig.event_date, today) <= wave3("lead_window_months")


# ── Gate 3 buyer fixes: buyer type, shells, dates ─────────────────────────────────────────────────────────────────
BUYER_TYPES = ("operator", "PE/investor", "public company", "large operator", "REIT", "hospital/public", "lender")
_BUYER_ORGS = Path(__file__).resolve().parents[2] / "config" / "buyer_orgs.yaml"
_TYPE_WORDS = [   # first match wins; ASSUMPTION: keyword rules, overridden by config/buyer_orgs.yaml
    ("hospital/public", re.compile(r"\bhospital\b|\bhealth(care)? district\b|\bcounty\b|\bauthority\b|"
                                   r"\bcity of\b|\bstate of\b|\bmunicipal", re.I)),
    ("lender", re.compile(r"\bbank\b|bancorp|\blend(er|ing)\b|\bmortgage\b|credit union|\bloan\b", re.I)),
    ("REIT", re.compile(r"\breit\b|real estate investment trust|\brealty\b|properties trust", re.I)),
    ("PE/investor", re.compile(r"\bcapital\b|\bpartners\b|\binvest(ment|ments|ors)?\b|\bequity\b|\bfunds?\b|"
                               r"\badvisors\b|asset management|\bventures\b", re.I)),
]
_SHELL = re.compile(r"\b(op\s?co|prop\s?co)\b", re.I)


@lru_cache(maxsize=1)
def buyer_orgs() -> list[dict]:
    """Hand-checked buyer facts (type / parent), each with a source_url."""
    if not _BUYER_ORGS.exists():
        return []
    items = (yaml.safe_load(_BUYER_ORGS.read_text()) or {}).get("orgs") or []
    for x in items:
        if not str(x.get("source_url", "")).startswith(("http://", "https://")):
            raise ValueError(f"config/buyer_orgs.yaml: source_url missing for {x.get('name')}")
        if x.get("type") and x["type"] not in BUYER_TYPES:
            raise ValueError(f"config/buyer_orgs.yaml: unknown type {x['type']!r}")
    return items


def buyer_type(name: str | None, known: dict | None = None) -> tuple[str, str]:
    """(type, basis). `known` = normalized name -> buyer_orgs entry. Basis: 'source' (config) / 'name' / 'default'."""
    from src.common.normalize import normalize_name
    if not name:
        return "operator", "default"
    k = (known or {}).get(normalize_name(name))
    if k and k.get("type"):
        return k["type"], "source"
    # brand words from config/buyer_orgs.yaml `match` (e.g. any "... Sonida ..." entity -> Sonida's type)
    norm = f" {normalize_name(name)} "
    for b in buyer_orgs():
        for w in b.get("match") or []:
            if b.get("type") and f" {normalize_name(w)} " in norm:
                return b["type"], "source"
    for t, rx in _TYPE_WORDS:
        if rx.search(name):
            return t, "name"
    return "operator", "default"


def buyer_type_weight(t: str) -> float:
    w = {"operator": 1.0, "PE/investor": 1.0, "public company": 0.4, "large operator": 0.4, "REIT": 0.4,
         "hospital/public": 0.0, "lender": 0.0}
    w.update(cfg().get("scoring", {}).get("buyer_type_weight") or {})
    return float(w[t])


def is_shell_buyer(buyer: str | None, facility_name: str | None) -> bool:
    """An OpCo / PropCo LLC, or an entity named after the facility itself: a single-asset shell, not the real buyer."""
    from src.common.normalize import normalize_name
    if not buyer:
        return False
    return bool(_SHELL.search(buyer)) or (bool(facility_name) and normalize_name(buyer) == normalize_name(facility_name))


_MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov",
                                       "dec"], 1)}


def stated_date(text: str | None) -> date | None:
    """Latest day a free-text date can mean: '2023' -> Dec 31 2023, 'March 2025' -> Mar 31 2025, ISO dates as is."""
    import calendar
    if not text:
        return None
    t = text.strip().lower()
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", t)
    if m:
        try:
            return date(int(m[1]), int(m[2]), int(m[3]))
        except ValueError:
            return None
    m = re.search(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(?:(\d{1,2}),?\s+)?(\d{4})\b", t)
    if m:
        y, mo = int(m[3]), _MONTHS[m[1]]
        d = int(m[2]) if m[2] else calendar.monthrange(y, mo)[1]
        try:
            return date(y, mo, d)
        except ValueError:
            return None
    m = re.search(r"\b(19|20)\d{2}\b", t)
    return date(int(m[0]), 12, 31) if m else None


def news_change_date(event_dates, published: date | None) -> date | None:
    """When a news article states event dates, the change happened by the latest of them (never after publishing);
    otherwise the publish date is the best date we have."""
    found = [d for d in (stated_date(e.get("date_text")) for e in (event_dates or []) if isinstance(e, dict)) if d]
    if published:
        found = [min(d, published) for d in found]
    return max(found) if found else published


def months_old(d: date | None, today: date) -> float | None:
    return months_between(d, today) if d else None


def sale_order_date(sale: dict) -> date | None:
    """Date of a court sale order / closing from the docket line (e.g. 'Dkt. 291 (2026-11-05) Sale Order ...').
    Only for an order or a closed sale; the bankruptcy filing date is never used as the sale date."""
    if sale.get("stage_text") not in ("sale order", "sale closed"):
        return None
    text = sale.get("evidence_text") or ""
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", text)
    if m:
        try:
            return date(int(m[1]), int(m[2]), int(m[3]))
        except ValueError:
            return None
    # written dates ("approved the sale in January 2026", "on February 1, 2026"); a bare year is too vague
    m = re.search(r"\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(?:\d{1,2},?\s+)?\d{4}\b", text, re.I)
    return stated_date(m[0]) if m else None


_HARD_EVENTS = ("chapter_11", "chapter_7", "receivership", "foreclosure", "deed_in_lieu", "closure", "warn_closure",
                "hud_loan_terminated", "loan_or_lease_default", "court_sale_process", "mechanics_lien", "price_collapse")


def hard_events() -> tuple:
    """Signal types that alone put a home on the main list (scoring.hard_events in thresholds.yaml wins)."""
    return tuple(cfg().get("scoring", {}).get("hard_events") or _HARD_EVENTS)
