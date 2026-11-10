"""Bankruptcy families: group stg.court_case rows that belong to one (jointly administered) bankruptcy.

    python -m src.discover.courts.families [--dry-run] [--no-date-lookup]

Grouping rules, strongest first (each case records the strongest rule that linked it, `join_method`):
  1. jointly_administered_text: CourtListener case names carry '<b>Jointly Administered under 25-34698.</b>';
     every such case joins the lead docket in the same court.
  2. lead_case: the same docket number (normalized 'YY-NNNNN') seen on several sources (CourtListener, Stretto, Epiq,
     ElevenFlo) is one case. Claims-agent pages list the LEAD case, so their docket number becomes the family's lead.
     When one side has no court (Epiq index), the names must also share a distinctive word.
  3. same_debtor_name: same court, identical normalized debtor name, filed within 60 days (e.g. an adversary docket
     opened under the debtor's own name a month after the petition).
  4. two_day_rule (fallback): same court, filed within 2 days, and either both cases are labelled 'yes' or their names
     share a distinctive word. Two groups that each name a different 'Jointly Administered' lead are never merged.
A family's grouping_method is the weakest rule any member needed (so 'two_day_rule' means the boundary is heuristic);
a family of one case (possibly several source copies of one docket) is 'single_case' / 'lead_case'.

Senior-housing label per case:
  - 'hand': read at run time from the gitignored hand classification (FAMILIES_CLASSIFICATION_CSV, default
    eval/private/cl_classified.csv; columns stg_court_case_id, senior_housing). Never copied into code or the repo.
    If the file is absent (e.g. in CI), a case keeps the hand label already stored in core.case_family_member.
  - 'keyword' (everything else): see keyword_label(). 'yes' on clear senior-housing words, 'no' on clearly other
    healthcare (hospital, pharmacy, medical group ...), otherwise 'maybe'.
Family label = strongest member label (yes > maybe > no). Families labelled 'no' are not stored.

Re-runnable and idempotent: existing family_ids are kept (a component reuses the id most of its members already
had); new cases join existing families; families left without members are deleted.
"""
from __future__ import annotations

import argparse
import csv
import html
import os
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date

from src.common.config import PROJECT_ROOT
from src.common.normalize import normalize_name

from .base import HEALTHCARE_NAME, WINDOW_MONTHS, months_ago, parse_date

DEFAULT_CLASSIFICATION = PROJECT_ROOT / "eval" / "private" / "cl_classified.csv"
TWO_DAY = 2
SAME_NAME_DAYS = 60
METHOD_RANK = {"jointly_administered_text": 4, "lead_case": 3, "same_debtor_name": 2, "two_day_rule": 1,
               "manual": 5, "single_case": 0}
LABEL_RANK = {"yes": 2, "maybe": 1, "no": 0}

# ── courts ──────────────────────────────────────────────────────────────────────────────────────────────────────
STATE_NAMES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California", "CO": "Colorado",
    "CT": "Connecticut", "DE": "Delaware", "DC": "District of Columbia", "FL": "Florida", "GA": "Georgia",
    "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas",
    "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan",
    "MN": "Minnesota", "MS": "Mississippi", "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
    "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York", "NC": "North Carolina",
    "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania", "PR": "Puerto Rico",
    "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas",
    "UT": "Utah", "VT": "Vermont", "VA": "Virginia", "WA": "Washington", "WV": "West Virginia", "WI": "Wisconsin",
    "WY": "Wyoming",
}
_NAME_TO_STATE = {v.lower(): k for k, v in STATE_NAMES.items()}
# Bluebook court abbreviations used by ElevenFlo ("Bankr. N.D. Tex.").
_BLUEBOOK = {
    "ala": "AL", "alaska": "AK", "ariz": "AZ", "ark": "AR", "cal": "CA", "colo": "CO", "conn": "CT", "del": "DE",
    "d.c": "DC", "fla": "FL", "ga": "GA", "haw": "HI", "idaho": "ID", "ill": "IL", "ind": "IN", "iowa": "IA",
    "kan": "KS", "ky": "KY", "la": "LA", "me": "ME", "md": "MD", "mass": "MA", "mich": "MI", "minn": "MN",
    "miss": "MS", "mo": "MO", "mont": "MT", "neb": "NE", "nev": "NV", "n.h": "NH", "n.j": "NJ", "n.m": "NM",
    "n.y": "NY", "n.c": "NC", "n.d": "ND", "ohio": "OH", "okla": "OK", "or": "OR", "pa": "PA", "p.r": "PR",
    "r.i": "RI", "s.c": "SC", "s.d": "SD", "tenn": "TN", "tex": "TX", "utah": "UT", "vt": "VT", "va": "VA",
    "wash": "WA", "w.va": "WV", "w. va": "WV", "wis": "WI", "wyo": "WY",
}
_DIRS = {"n": "n", "s": "s", "e": "e", "w": "w", "m": "m", "c": "c", "northern": "n", "southern": "s",
         "eastern": "e", "western": "w", "middle": "m", "central": "c"}


def _code(state: str, direction: str = "") -> str:
    # CourtListener spells Nebraska's bankruptcy court 'nebraskab'; every other id is postal code + direction + 'b'.
    return "nebraskab" if state == "NE" else f"{state.lower()}{direction}b"


def court_code(court: str | None) -> str | None:
    """'txnb' / 'Northern District of Texas' / 'Bankr. N.D. Tex.' -> 'txnb'. Unknown -> None."""
    if not court or court.strip().upper() == "UNKNOWN":
        return None
    c = court.strip()
    if re.fullmatch(r"[a-z]{3,10}", c):
        return c
    m = re.search(r"(Northern|Southern|Eastern|Western|Middle|Central)?\s*District of ([A-Za-z ]+)$", c, re.I)
    if m:
        st = _NAME_TO_STATE.get(m.group(2).strip().lower())
        return _code(st, _DIRS[m.group(1).lower()] if m.group(1) else "") if st else None
    c = re.sub(r"^Bankr\.\s*", "", c)
    m = re.fullmatch(r"(?:([NSEWMC])\.\s*)?D\.\s*(.+?)\.?", c)
    if m:
        st = _BLUEBOOK.get(m.group(2).strip().lower().rstrip("."))
        return _code(st, _DIRS[m.group(1).lower()] if m.group(1) else "") if st else None
    return None


def court_state(code: str | None) -> str | None:
    if not code:
        return None
    if code == "nebraskab":
        return "NE"
    st = code[:2].upper()
    return st if st in STATE_NAMES else None


# ── dockets and names ───────────────────────────────────────────────────────────────────────────────────────────
_DOCKET = re.compile(r"(\d{2})-(?:[a-z]{2}-)?(\d{5})")
_JOINT = re.compile(r"Jointly\s+Administered\s+under\s+(?:Case\s+No\.?\s*)?([0-9:]*\d{2}-(?:[a-z]{2}-)?\d{5})", re.I)


def norm_docket(docket: str | None) -> str | None:
    """'2:26-bk-10221' / '26-10221' -> '26-10221'."""
    m = _DOCKET.search(docket or "")
    return f"{m.group(1)}-{m.group(2)}" if m else None


def joint_lead(case_name: str | None) -> str | None:
    m = _JOINT.search(html.unescape(re.sub(r"<[^>]+>", " ", case_name or "")))
    return norm_docket(m.group(1)) if m else None


def clean_name(name: str | None) -> str:
    t = html.unescape(re.sub(r"<[^>]+>", " ", name or ""))
    t = _JOINT.sub("", t)
    t = re.sub(r"\s*\((?:19|20)\d{2}\)\s*$", "", t)          # Stretto's "(2025)" repeat-filing suffix
    return re.sub(r"\s+", " ", t).strip(" .")


STOP = {"llc", "inc", "lp", "ltd", "the", "of", "and", "at", "dba", "holdings", "holding", "company", "co", "corp",
        "corporation", "operations", "operating", "facility", "healthcare", "health", "care", "center", "centre",
        "senior", "living", "assisted", "rehabilitation", "rehab", "nursing", "home", "homes", "services", "group",
        "jv", "property", "realty", "acquisition", "memory", "skilled", "opco", "propco", "dst", "fund", "in", "re",
        "for", "a", "an", "debtor", "debtors", "l", "p", "llp", "pllc", "et", "al", "mt", "fka", "aka", "new"}


def distinctive_tokens(name: str | None) -> set[str]:
    words = re.findall(r"[a-z][a-z'&-]+", clean_name(name).lower())
    return {w for w in words if w not in STOP and len(w) >= 3}


# ── senior-housing label ────────────────────────────────────────────────────────────────────────────────────────
KEYWORD_YES = re.compile(
    r"senior\s+(?:living|care|housing|ministries|community|residence)|assisted\s+living|memory\s+care|nursing|"
    r"skilled|continuing\s+care|retirement\s+(?:community|center|village|system|home|living)|life\s+plan|\bccrc\b|"
    r"care\s+cent(?:er|re)s?|rehabilitation\s+(?:and|&)\s+(?:care|health)|health\s+(?:and|&)\s+rehab|elder\s*care|"
    r"\bmanor\b|lutheran\s+(?:home|village)|methodist\s+retirement|personal\s+care\s+home|\balf\b|adult\s+care|"
    r"care\s+for\s+the\s+elderly|\bpost[- ]acute|manorcare|congregate\s+living",
    re.I)
KEYWORD_NO = re.compile(
    r"hospital|medical\s+(?:center|centre|group|staffing)|physician|pharmac|biomedical|biotech|clinic\b|"
    r"hospitality|behavioral|surgical|surgery|dental|imaging|orthop|laborator|diagnostic|health\s+system|"
    r"health\s+plan|insurance|therapeutics|devices?\b|home\s*health|home\s*care|hospice|pediatric|ob/?gyn|"
    r"primary\s+care|medical|wellness|practice|aesthetic|emergency|specialists|pulmonary|\bp\.?c\.?$|pllc|"
    r"\bp\.a\.?$",
    re.I)


def keyword_label(name: str | None) -> str:
    """Rule for cases without a hand label:
      yes   - a clear senior-housing word (KEYWORD_YES: assisted living, nursing, memory care, manor, ...);
      no    - clearly other healthcare (KEYWORD_NO: hospital, pharmacy, home health, medical practice, ...) or no
              healthcare/housing hint at all (base.HEALTHCARE_NAME);
      maybe - a healthcare/housing hint but nothing decisive (e.g. 'Gulf Coast Health Care', 'Homestead Village')."""
    n = clean_name(name)
    if KEYWORD_YES.search(n):
        return "yes"
    if KEYWORD_NO.search(n) or not HEALTHCARE_NAME.search(n):
        return "no"
    return "maybe"


def load_hand_labels(path=None) -> dict[int, str]:
    path = path or os.environ.get("FAMILIES_CLASSIFICATION_CSV") or DEFAULT_CLASSIFICATION
    if not os.path.exists(path):
        return {}
    with open(path, newline="", encoding="utf-8") as f:
        return {int(r["stg_court_case_id"]): r["senior_housing"].strip().lower()
                for r in csv.DictReader(f) if r.get("senior_housing", "").strip().lower() in LABEL_RANK}


# ── grouping ────────────────────────────────────────────────────────────────────────────────────────────────────
def source_kind(url: str | None) -> str:
    u = url or ""
    for host, kind in (("courtlistener.com", "courtlistener"), ("stretto.com", "stretto"), ("epiq11.com", "epiq"),
                       ("elevenflo.com", "elevenflo")):
        if host in u:
            return kind
    return "other"


@dataclass
class Case:
    id: int
    court_raw: str | None
    docket_raw: str
    name: str
    filed: date | None
    source_url: str
    claims_agent_url: str | None = None
    courtlistener_id: str | None = None
    label: str = "maybe"
    label_source: str = "keyword"
    code: str | None = None
    dk: str | None = None
    lead_ref: str | None = None
    kind: str = "other"
    tokens: set = field(default_factory=set)

    @classmethod
    def from_row(cls, r: dict) -> "Case":
        raw_name = r.get("case_name") or r.get("debtor_name") or ""
        name = clean_name(r.get("debtor_name")) or clean_name(raw_name)
        c = cls(int(r["id"]), r.get("court"), r.get("docket_no") or "", name, parse_date(r.get("filed_date")),
                r.get("source_url") or "", r.get("claims_agent_url"), r.get("courtlistener_id"))
        c.code, c.dk, c.lead_ref = court_code(c.court_raw), norm_docket(c.docket_raw), joint_lead(raw_name)
        c.kind, c.tokens = source_kind(c.source_url), distinctive_tokens(name)
        return c


@dataclass
class Family:
    members: list[Case]
    join_method: dict[int, str]
    family_id: int | None = None
    family_name: str = ""
    court: str | None = None
    lead_docket_no: str | None = None
    filed_date: date | None = None
    filed_date_source: str | None = None
    grouping_method: str = "single_case"
    senior_housing: str = "maybe"
    within_36m: bool | None = None
    claims_agent_url: str | None = None
    notes: list[str] = field(default_factory=list)


class _UF:
    def __init__(self):
        self.parent: dict = {}
        self.leads: dict = {}          # root -> set of 'Jointly Administered' lead dockets in the component

    def find(self, x):
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a, b, guard=False) -> bool:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return True
        la, lb = self.leads.get(ra, set()), self.leads.get(rb, set())
        if guard and la and lb and not (la & lb):
            return False
        self.parent[rb] = ra
        self.leads[ra] = la | lb
        return True


def _same_case(a: Case, b: Case) -> bool:
    if a.code and b.code:
        return a.code == b.code
    return bool(a.tokens & b.tokens)


def group_cases(cases: list[Case]) -> list[tuple[list[Case], dict[int, str]]]:
    """Return [(members, {case_id: join_method})]. Pure function (tested)."""
    uf = _UF()
    best: dict[int, str] = {}

    def link(a, b, method, guard=False):
        if uf.union(a, b, guard):
            for x in (a, b):
                if isinstance(x, int) and METHOD_RANK[method] > METHOD_RANK.get(best.get(x, "single_case"), 0):
                    best[x] = method

    for c in cases:
        uf.find(c.id)
    # 1. Jointly Administered text: join a virtual lead node per (court, lead docket).
    by_dk = defaultdict(list)
    for c in cases:
        if c.dk:
            by_dk[c.dk].append(c)
    for c in cases:
        if c.lead_ref:
            node = ("JA", c.code, c.lead_ref)
            uf.leads[uf.find(node)] = {c.lead_ref}
            link(c.id, node, "jointly_administered_text")
            uf.leads[uf.find(c.id)] = uf.leads.get(uf.find(c.id), set()) | {c.lead_ref}
            for lead in by_dk.get(c.lead_ref, []):
                if lead.code == c.code:
                    link(c.id, lead.id, "jointly_administered_text")
    # 2. Same docket number on several sources = one case.
    for group in by_dk.values():
        for i, a in enumerate(group):
            for b in group[i + 1:]:
                if _same_case(a, b):
                    link(a.id, b.id, "lead_case")
    # 3. Same court + identical debtor name within 60 days.
    by_name = defaultdict(list)
    for c in cases:
        if c.code and c.filed:
            by_name[(c.code, normalize_name(c.name))].append(c)
    for group in by_name.values():
        group.sort(key=lambda c: c.filed)
        for i, a in enumerate(group):
            for b in group[i + 1:]:
                if (b.filed - a.filed).days <= SAME_NAME_DAYS and a.dk != b.dk:
                    link(a.id, b.id, "same_debtor_name")
    # 4. Fallback: same court, filed within 2 days (only cases not labelled 'no').
    by_court = defaultdict(list)
    for c in cases:
        if c.code and c.filed and c.label != "no":
            by_court[c.code].append(c)
    for group in by_court.values():
        group.sort(key=lambda c: (c.filed, c.id))
        for i, b in enumerate(group):
            for a in reversed(group[:i]):
                if (b.filed - a.filed).days > TWO_DAY:
                    break
                if (a.label == "yes" and b.label == "yes") or (a.tokens & b.tokens):
                    link(a.id, b.id, "two_day_rule", guard=True)

    comps = defaultdict(list)
    for c in cases:
        comps[uf.find(c.id)].append(c)
    out = []
    for members in comps.values():
        methods = {c.id: best.get(c.id, "single_case") for c in members}
        out.append((members, methods))
    return out


_KIND_PREF = {"courtlistener": 0, "stretto": 1, "elevenflo": 2, "epiq": 3, "other": 4}


def describe_family(members: list[Case], methods: dict[int, str], today: date | None = None) -> Family:
    today = today or date.today()
    fam = Family(members=members, join_method=dict(methods))
    leads = Counter(c.lead_ref for c in members if c.lead_ref)
    agent_dks = [c.dk for c in members if c.kind in ("epiq", "stretto") and c.dk]
    if leads:
        lead = leads.most_common(1)[0][0]
    elif agent_dks:
        lead = Counter(agent_dks).most_common(1)[0][0]          # claims agents list the lead case
    else:
        dated = sorted((c for c in members if c.dk), key=lambda c: (c.filed or date.max, c.dk))
        lead = dated[0].dk if dated else None
    fam.lead_docket_no = lead
    lead_cases = [c for c in members if c.dk == lead] or members
    named = sorted(lead_cases, key=lambda c: (c.kind not in ("epiq", "stretto"), _KIND_PREF[c.kind], len(c.name)))
    fam.family_name = named[0].name[:200] or (lead or "NOT FOUND")
    codes = Counter(c.code for c in members if c.code)
    fam.court = codes.most_common(1)[0][0] if codes else None

    dated_leads = sorted((c for c in lead_cases if c.filed), key=lambda c: _KIND_PREF[c.kind])
    if dated_leads:
        src = dated_leads[0]
        others = {c.filed for c in dated_leads if abs((c.filed - src.filed).days) > TWO_DAY}
        if others:
            fam.notes.append(f"filing dates disagree across sources for {lead}: "
                             + ", ".join(sorted({str(src.filed)} | {str(d) for d in others}))
                             + f"; kept {src.kind} date")
    else:
        dated = sorted((c for c in members if c.filed), key=lambda c: c.filed)
        src = dated[0] if dated else None
    if src:
        fam.filed_date, fam.filed_date_source = src.filed, src.source_url

    agent = [c for c in members if c.claims_agent_url] + [c for c in members if c.kind == "elevenflo"]
    agent.sort(key=lambda c: (c.dk != lead, {"stretto": 0, "epiq": 1, "elevenflo": 2}.get(c.kind, 3)))
    fam.claims_agent_url = (agent[0].claims_agent_url or agent[0].source_url) if agent else None

    # A hand label beats a keyword label: the keyword rule only decides families nobody classified by hand.
    hand = [c.label for c in members if c.label_source == "hand"]
    fam.senior_housing = max(hand or [c.label for c in members], key=LABEL_RANK.get)
    non_lead = [m for cid, m in methods.items() if m != "single_case"]
    if len(members) == 1:
        fam.grouping_method = "single_case"
    else:
        fam.grouping_method = min(non_lead, key=METHOD_RANK.get) if non_lead else "lead_case"
    for c in members:
        if len(members) > 1 and fam.join_method[c.id] == "single_case":
            fam.join_method[c.id] = "lead_case"
    fam.within_36m = within_window(fam.filed_date, lead, today)
    n_cl = sum(c.kind == "courtlistener" for c in members)
    fam.notes.insert(0, f"{len(members)} case rows ({n_cl} CourtListener)")
    return fam


def within_window(filed: date | None, lead_docket: str | None, today: date | None = None) -> bool | None:
    """Filed within 36 months. Without a date, the docket number's year still settles clear cases."""
    cutoff = months_ago(WINDOW_MONTHS, today)
    if filed:
        return filed >= cutoff
    m = re.match(r"(\d{2})-", lead_docket or "")
    if m:
        year = 2000 + int(m.group(1))
        if year < cutoff.year:
            return False
        if year > cutoff.year:
            return True
    return None


def build_families(rows: list[dict], hand: dict[int, str], stored_hand: dict[int, str] | None = None,
                   today: date | None = None, keep_no: bool = False) -> list[Family]:
    cases = []
    stored_hand = stored_hand or {}
    for r in rows:
        c = Case.from_row(r)
        if c.id in hand:
            c.label, c.label_source = hand[c.id], "hand"
        elif c.id in stored_hand:
            c.label, c.label_source = stored_hand[c.id], "hand"
        else:
            c.label = keyword_label(c.name)
        cases.append(c)
    fams = [describe_family(m, j, today) for m, j in group_cases(cases)]
    return [f for f in fams if keep_no or f.senior_housing != "no"]


def assign_ids(fams: list[Family], existing: dict[int, int]) -> set[int]:
    """Reuse family ids: each family takes the existing id most of its members had (largest families first).
    Returns ids that are no longer used."""
    used: set[int] = set()
    for fam in sorted(fams, key=lambda f: -len(f.members)):
        votes = Counter(existing[c.id] for c in fam.members if c.id in existing)
        for fid, _ in votes.most_common():
            if fid not in used:
                fam.family_id = fid
                used.add(fid)
                break
        merged = set(votes) - {fam.family_id}
        if merged:
            fam.notes.append(f"merged earlier families {sorted(merged)}")
    return set(existing.values()) - used


# ── undated families: filing date from the public ElevenFlo case page ──────────────────────────────────────────
def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def name_variants(name: str) -> list[str]:
    parts = re.split(r",?\s*\b(?:f/k/a|d/b/a|dba|fka|formerly known as)\b\s*", name, flags=re.I)
    return [p.strip(" ,.") for p in parts if p.strip(" ,.")]


def lookup_dates_elevenflo(fams: list[Family], client=None) -> list[str]:
    """For families with no filing date, find the debtor's ElevenFlo page in the public case sitemap, confirm the
    docket number on the page, and take the filing date from it. Returns human-readable notes."""
    from src.common.http import PoliteClient, SourceBlocked
    from .elevenflo import SITEMAP, _DESC, _META_DESC, sitemap_urls

    todo = [f for f in fams if f.filed_date is None]
    if not todo:
        return []
    notes = []
    own = client is None
    client = client or PoliteClient("elevenflo")
    try:
        sm = client.get(SITEMAP, use_cache=False)
        urls = sitemap_urls(sm.text) if sm.status_code == 200 else []
        slugs = {u: re.sub(r"-\d+$", "", u.rsplit("/", 1)[-1]) for u in urls}
        for fam in todo:
            want = [_slug(v) for c in fam.members for v in name_variants(c.name)]
            want = [w for w in dict.fromkeys(want) if len(w) >= 8]
            cands = [u for u, s in slugs.items() if any(s == w or s.startswith(w) or w.startswith(s) for w in want)]
            found = False
            for url in cands[:3]:
                try:
                    r = client.get(url, use_cache=False)
                except SourceBlocked as exc:
                    notes.append(f"{fam.family_name}: ElevenFlo blocked ({exc})")
                    break
                m = _META_DESC.search(r.text) if r.status_code == 200 else None
                d = _DESC.search(html.unescape(m.group(1))) if m else None
                if not d or norm_docket(d.group("docket")) != fam.lead_docket_no:
                    continue
                filed = parse_date(d.group("filed"))
                if not filed:
                    continue
                fam.filed_date, fam.filed_date_source = filed, url
                fam.court = fam.court or court_code(d.group("court"))
                yy = (fam.lead_docket_no or "")[:2]
                if yy.isdigit() and 2000 + int(yy) != filed.year:
                    fam.notes.append(f"ElevenFlo filing date {filed} does not match docket-number year 20{yy}")
                fam.within_36m = within_window(filed, fam.lead_docket_no)
                notes.append(f"{fam.family_name}: filed {filed} (ElevenFlo {url})")
                found = True
                break
            if not found:
                fam.notes.append("filing date NOT FOUND: Epiq case pages are JavaScript-only and no ElevenFlo page "
                                 "with this docket number was found")
                notes.append(f"{fam.family_name}: date NOT FOUND (within_36m={fam.within_36m} from docket year)")
    finally:
        if own:
            client.close()
    return notes


# ── database ────────────────────────────────────────────────────────────────────────────────────────────────────
CASE_SQL = """select id, court, docket_no, case_name, debtor_name, filed_date, source_url, claims_agent_url,
                     courtlistener_id from stg.court_case"""


def save(conn, fams: list[Family]) -> dict:
    existing = {r["stg_court_case_id"]: r["family_id"] for r in
                conn.execute("select stg_court_case_id, family_id from core.case_family_member").fetchall()}
    stale = assign_ids(fams, existing)
    # Keep a filing date found earlier (e.g. by the ElevenFlo lookup) when this run did not find one.
    known = {r["family_id"]: r for r in conn.execute(
        "select family_id, filed_date, filed_date_source from core.case_family where filed_date is not null")}
    for f in fams:
        k = known.get(f.family_id)
        if f.filed_date is None and k:
            f.filed_date, f.filed_date_source = k["filed_date"], k["filed_date_source"]
            f.within_36m = within_window(f.filed_date, f.lead_docket_no)
            f.notes = [n for n in f.notes if not n.startswith("filing date NOT FOUND")]
    new = upd = 0
    with conn.cursor() as cur:
        for f in fams:
            vals = (f.family_name, f.court, f.lead_docket_no, f.filed_date, f.filed_date_source, f.grouping_method,
                    f.senior_housing, f.within_36m, f.claims_agent_url, "; ".join(f.notes)[:2000])
            if f.family_id is None:
                cur.execute("""insert into core.case_family (family_name, court, lead_docket_no, filed_date,
                                 filed_date_source, grouping_method, senior_housing, within_36m, claims_agent_url, notes)
                               values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) returning family_id""", vals)
                f.family_id = cur.fetchone()["family_id"]
                new += 1
            else:
                cur.execute("""update core.case_family set family_name=%s, court=%s, lead_docket_no=%s, filed_date=%s,
                                 filed_date_source=%s, grouping_method=%s, senior_housing=%s, within_36m=%s,
                                 claims_agent_url=%s, notes=%s, updated_at=now()
                               where family_id=%s and grouping_method <> 'manual'""", vals + (f.family_id,))
                upd += 1
        keep = [c.id for f in fams for c in f.members]
        cur.execute("delete from core.case_family_member where not (stg_court_case_id = any(%s))", (keep,))
        cur.executemany(
            """insert into core.case_family_member (family_id, stg_court_case_id, join_method, senior_housing, label_source)
               values (%s,%s,%s,%s,%s)
               on conflict (stg_court_case_id) do update set family_id=excluded.family_id,
                 join_method=excluded.join_method, senior_housing=excluded.senior_housing,
                 label_source=excluded.label_source""",
            [(f.family_id, c.id, f.join_method[c.id], c.label, c.label_source) for f in fams for c in f.members])
        cur.execute("""delete from core.case_family f where not exists
                         (select 1 from core.case_family_member m where m.family_id = f.family_id)""")
        removed = cur.rowcount
    conn.commit()
    return {"families_new": new, "families_updated": upd, "families_removed": removed, "ids_released": len(stale)}


def summarize(fams: list[Family]) -> str:
    def status(f):
        s = {True: "recent", False: "older", None: "unknown"}[f.within_36m]
        return s + ("" if f.filed_date else " (no date; docket year)" if f.within_36m is not None else " (no date)")
    by_m = Counter(f.grouping_method for f in fams)
    by_s = Counter(status(f) for f in fams)
    by_l = Counter(f.senior_housing for f in fams)
    return (f"families={len(fams)} cases={sum(len(f.members) for f in fams)}\n"
            f"  by grouping_method: {dict(by_m)}\n  by status: {dict(by_s)}\n  by senior_housing: {dict(by_l)}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m src.discover.courts.families")
    ap.add_argument("--dry-run", action="store_true", help="group and print; write nothing")
    ap.add_argument("--no-date-lookup", action="store_true", help="skip the ElevenFlo date lookup for undated families")
    a = ap.parse_args(argv)
    from src.common.db import connect
    conn = connect()
    try:
        rows = conn.execute(CASE_SQL).fetchall()
        stored = {r["stg_court_case_id"]: r["senior_housing"] for r in conn.execute(
            "select stg_court_case_id, senior_housing from core.case_family_member where label_source = 'hand'")}
        hand = load_hand_labels()
        print(f"cases={len(rows)} hand labels from file={len(hand)} stored hand labels={len(stored)}")
        fams = build_families(rows, hand, stored)
        if not a.no_date_lookup:
            for n in lookup_dates_elevenflo(fams):
                print("  date lookup:", n)
        print(summarize(fams))
        if not a.dry_run:
            print("  wrote:", save(conn, fams))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
