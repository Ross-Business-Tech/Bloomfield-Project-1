"""Facility matching (docs/BUILD_SPEC.md step 3) plus small pure helpers used by every enrich step.

Scoring rule (from src/common/normalize.match_score, weights in config/thresholds.yaml):
    0.35 * name token_set + 0.45 * street token_sort + 20 same street number - 25 different + 10 same phone
    >= 90 auto, 75-89 needs_review, < 75 new facility.

ASSUMPTION (documented in the core-builder report): when either side has no street address (all HUD
files, court cases, some news extractions) the address part of the formula is always 0, so nothing could
ever reach 75. For those pairs we use a stricter name-only score instead:
    0.5 * token_set_ratio + 0.5 * token_sort_ratio of the DISTINCTIVE name tokens (+10 same phone),
i.e. the normalized name minus generic industry words (nursing, rehabilitation, center, health care ...).
Averaging with token_sort and dropping generic words stops "Nursing Center" scoring 100 against every
"X Nursing Center". Blocking (same state + zip5 or same state + city) still applies, so a name-only match
needs the same name in the same zip or city.
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field

from rapidfuzz import fuzz

from src.common.normalize import (
    classify_match, match_score, normalize_name, normalize_phone, normalize_street, zip5,
)

NOT_FOUND = "NOT FOUND"

STATES = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR", "california": "CA", "colorado": "CO",
    "connecticut": "CT", "delaware": "DE", "district of columbia": "DC", "florida": "FL", "georgia": "GA",
    "hawaii": "HI", "idaho": "ID", "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS",
    "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD", "massachusetts": "MA",
    "michigan": "MI", "minnesota": "MN", "mississippi": "MS", "missouri": "MO", "montana": "MT",
    "nebraska": "NE", "nevada": "NV", "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM",
    "new york": "NY", "north carolina": "NC", "north dakota": "ND", "ohio": "OH", "oklahoma": "OK",
    "oregon": "OR", "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC", "south dakota": "SD",
    "tennessee": "TN", "texas": "TX", "utah": "UT", "vermont": "VT", "virginia": "VA", "washington": "WA",
    "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY", "puerto rico": "PR", "guam": "GU",
    "virgin islands": "VI", "northern mariana islands": "MP",
}
STATE_CODES = set(STATES.values())


def clean(value) -> str | None:
    """'' / 'NOT FOUND' / None -> None; everything else stripped text."""
    if value is None:
        return None
    if isinstance(value, (list, tuple)):
        value = value[0] if value else None
        return clean(value)
    text = str(value).strip()
    if not text or text.upper() == NOT_FOUND:
        return None
    return text


def state_code(value) -> str | None:
    text = clean(value)
    if not text:
        return None
    up = text.upper().replace(".", "").replace(" ", "").strip()
    if up in STATE_CODES:
        return up
    low = text.lower().strip()
    return STATES.get(low) or _ABBR.get(low.replace(".", "").replace(" ", "")) or _AP_EXTRA.get(low.replace(".", ""))


_AP_EXTRA = {"calif": "CA", "penn": "PA", "penna": "PA", "mich": "MI", "wisc": "WI", "w va": "WV", "d c": "DC"}


def city_key(value) -> str | None:
    text = clean(value)
    if not text:
        return None
    text = re.sub(r"[^a-z0-9 ]+", " ", text.lower())
    text = re.sub(r"\bsaint\b", "st", text)
    return " ".join(text.split()) or None


# Industry words that every facility shares. In the name-only path they are dropped so that
# "Cold Spring Hills Center for Nursing & Rehab" does not look like "White Oaks Rehab and Nursing Center".
GENERIC_NAME_TOKENS = {
    "and", "of", "the", "for", "at", "a", "on", "in", "by", "center", "centre", "care", "health", "healthcare",
    "nursing", "rehabilitation", "home", "homes", "living", "assisted", "senior", "seniors", "skilled",
    "facility", "extended", "residence", "residences", "community", "communities", "services", "service",
    "memory", "medical", "wellness", "therapy", "restorative", "nrsg", "crt", "convalescent", "manor",
    "subacute", "post", "acute", "transitional", "retirement", "hospital", "system", "systems", "group",
    "management", "llc", "inc", "lp", "pc", "ltd", "corp", "company", "associates", "partners", "operating",
}


def distinctive_name(name: str | None) -> str:
    """Normalized name minus generic industry words ('' when nothing distinctive is left)."""
    return " ".join(t for t in normalize_name(name).split() if t not in GENERIC_NAME_TOKENS)


def name_only_score(a: dict, b: dict) -> float:
    da, db = distinctive_name(a.get("name")), distinctive_name(b.get("name"))
    if not da or not db:
        return 0.0          # nothing distinctive on one side: a name-only match would be a guess
    score = 0.5 * fuzz.token_set_ratio(da, db) + 0.5 * fuzz.token_sort_ratio(da, db)
    pa, pb = normalize_phone(a.get("phone")), normalize_phone(b.get("phone"))
    if pa and pa == pb:
        score += 10
    return round(score, 1)


def pair_score(src: dict, fac: dict) -> float:
    """Spec score when both sides have a street, else the name-only score (see module docstring)."""
    if normalize_street(src.get("street")) and normalize_street(fac.get("street")):
        return match_score(src, fac)
    return name_only_score(src, fac)


@dataclass
class MatchResult:
    facility_id: str | None
    score: float | None
    status: str            # 'auto' | 'needs_review' | 'new'
    candidates: int = 0


@dataclass
class FacilityIndex:
    """In-memory blocking index over core.facility. New facilities are added as they are created so that
    later records in the same run (e.g. a second HUD loan on the same building) match them."""
    by_zip: dict = field(default_factory=lambda: defaultdict(list))
    by_city: dict = field(default_factory=lambda: defaultdict(list))
    rows: dict = field(default_factory=dict)

    def add(self, fac: dict) -> None:
        fid = str(fac["facility_id"])
        fac.setdefault("care_types", [])
        self.rows[fid] = fac
        st = state_code(fac.get("state"))
        if not st:
            return
        z = zip5(fac.get("zip5"))
        if z:
            self.by_zip[(st, z)].append(fid)
        c = city_key(fac.get("city"))
        if c:
            self.by_city[(st, c)].append(fid)

    def candidates(self, rec: dict) -> list[dict]:
        """Block on state + zip5, plus state + city (BUILD_SPEC fallback; the union adds recall when a
        facility sits in a neighbouring zip of the same city)."""
        st = state_code(rec.get("state"))
        if not st:
            return []
        ids: list[str] = []
        z = zip5(rec.get("zip5"))
        if z:
            ids += self.by_zip.get((st, z), [])
        c = city_key(rec.get("city"))
        if c:
            ids += self.by_city.get((st, c), [])
        seen, out = set(), []
        for fid in ids:
            if fid not in seen:
                seen.add(fid)
                out.append(self.rows[fid])
        return out

    def best(self, rec: dict) -> MatchResult:
        cands = self.candidates(rec)
        best_id, best_score = None, None
        for fac in cands:
            if direction_conflict(rec.get("name"), fac.get("name")):
                continue                      # 'Autumn House West' can never be 'Autumn House East'
            s = pair_score(rec, fac)
            if best_score is None or s > best_score:
                best_id, best_score = str(fac["facility_id"]), s
        if best_score is None:
            return MatchResult(None, None, "new", 0)
        status = classify_match(best_score)
        if status == "new":
            return MatchResult(None, best_score, "new", len(cands))
        return MatchResult(best_id, best_score, status, len(cands))


# ---------------------------------------------------------------- courts

_DISTRICT = {"n": "n", "s": "s", "e": "e", "w": "w", "m": "m", "c": "c",
             "northern": "n", "southern": "s", "eastern": "e", "western": "w", "middle": "m", "central": "c"}
_ABBR = {
    "ala": "AL", "alaska": "AK", "ariz": "AZ", "ark": "AR", "cal": "CA", "colo": "CO", "conn": "CT",
    "del": "DE", "dc": "DC", "fla": "FL", "ga": "GA", "haw": "HI", "hawaii": "HI", "idaho": "ID", "ill": "IL",
    "ind": "IN", "iowa": "IA", "kan": "KS", "ky": "KY", "la": "LA", "me": "ME", "md": "MD", "mass": "MA",
    "mich": "MI", "minn": "MN", "miss": "MS", "mo": "MO", "mont": "MT", "neb": "NE", "nev": "NV", "nh": "NH",
    "nj": "NJ", "nm": "NM", "ny": "NY", "nc": "NC", "nd": "ND", "ohio": "OH", "okla": "OK", "or": "OR",
    "ore": "OR", "pa": "PA", "pr": "PR", "ri": "RI", "sc": "SC", "sd": "SD", "tenn": "TN", "tex": "TX",
    "utah": "UT", "vt": "VT", "va": "VA", "wash": "WA", "wva": "WV", "wis": "WI", "wyo": "WY",
    "nmari": "MP", "nmar": "MP", "marii": "MP", "guam": "GU", "vi": "VI",
}


def normalize_court(court: str | None) -> str:
    """Reconcile court names across loaders to CourtListener ids: 'Bankr. N.D. Tex.' / 'Northern District
    of Texas' -> 'txnb'; 'Bankr. D. Del.' -> 'deb'. Unparseable values are returned unchanged."""
    raw = (court or "").strip()
    if not raw:
        return "UNKNOWN"
    if re.fullmatch(r"[a-z]{2,6}", raw):
        return raw
    text = raw.lower()
    m = re.match(r"(northern|southern|eastern|western|middle|central)?\s*district of (.+)$", text)
    if m:
        st = STATES.get(m.group(2).strip())
        if st:
            return f"{st.lower()}{_DISTRICT.get(m.group(1) or '', '')}b"
        return raw
    if text.startswith("bankr"):
        rest = text[len("bankr."):].strip() if text.startswith("bankr.") else text[5:].strip()
        rest = rest.replace(" ", "")              # 'n.d.tex.' / 'd.del.' / 's.d.n.y.' / 'd.n.mar.i.'
        m = re.match(r"(?:([nsewmc])\.)?d\.(.+)$", rest)
        if m:
            st_key = m.group(2).replace(".", "")
            st = _ABBR.get(st_key)
            if st:
                return f"{st.lower()}{m.group(1) or ''}b"
    return raw


def court_state(court_id: str | None) -> str | None:
    if court_id and re.fullmatch(r"[a-z]{3,6}", court_id):
        st = court_id[:2].upper()
        return st if st in STATE_CODES or st == "MP" else None
    return None


def normalize_docket(docket: str | None) -> str:
    """'8:26-bk-10925' -> '26-10925'; '07-40290(CEC)' -> '07-40290'. Other formats unchanged."""
    text = (docket or "").strip()
    m = re.search(r"(?<!\d)(\d{2})-(?:[a-z]{2}-)?(\d{4,5})(?!\d)", text.lower())
    if m:
        return f"{m.group(1)}-{m.group(2).zfill(5)}"
    return text


def court_source_id(url: str) -> str:
    host = (re.match(r"https?://([^/]+)", url or "") or [None, ""])[1].lower()
    for key, sid in (("courtlistener", "courtlistener"), ("epiq11", "epiq"), ("stretto", "stretto"),
                     ("elevenflo", "elevenflo"), ("kroll", "kroll")):
        if key in host:
            return sid
    return "courtlistener"


# ---------------------------------------------------------------- care types

def care_types_from_text(*texts) -> list[str]:
    """Map free text (HUD SOA category, license type, extracted care_type) to IL / AL / MC / SNF / CCRC."""
    flat = []
    for x in texts:
        flat.extend(x if isinstance(x, (list, tuple)) else [x])
    blob = " ".join(t for t in (clean(x) for x in flat) if t).lower().replace("_", " ")
    out = []
    if re.search(r"skilled|nursing|snf|\bicf\b", blob):
        out.append("SNF")
    if re.search(r"assisted|home for the aged|\balf\b|residential care|personal care", blob):
        out.append("AL")
    if re.search(r"board|\bbrd\b|foster", blob):
        out.append("BC")                  # board & care / adult foster care
    elif "AL" not in out and re.search(r"\bal\b", blob):
        out.append("AL")
    if re.search(r"memory|dementia|alzheimer", blob):
        out.append("MC")
    if re.search(r"independent", blob):
        out.append("IL")
    if re.search(r"ccrc|continuing care|life plan", blob):
        out.append("CCRC")
    return out


def to_int(value) -> int | None:
    text = clean(value)
    if text is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    m = re.search(r"\d[\d,]*", text)
    return int(m.group(0).replace(",", "")) if m else None


# ---------------------------------------------------------------- HUD (Gate 2 decisions, config matching.hud)

def hud_care_classes(soa_category: str | None, soa_code: str | None = None) -> set[str]:
    """Care class of a HUD Section 232 loan: {'SNF'}, {'AL'}, {'BC'} or a mix. Empty set = unknown category
    (e.g. 'Refi/Improvements +', fire-safety loans): compatible with anything."""
    text = (soa_category or "").lower()
    out = set()
    if re.search(r"nurs|\bicf\b|skilled", text):
        out.add("SNF")
    if re.search(r"assis|assted", text):
        out.add("AL")
    if re.search(r"board|brd|\bboar\b", text):
        out.add("BC")
    return out


def facility_care_classes(care_types) -> set[str]:
    out = set()
    for t in care_types or []:
        t = str(t).upper()
        if t == "SNF":
            out.add("SNF")
        elif t in ("AL", "MC", "IL"):
            out.add("AL")
        elif t == "BC":
            out.add("BC")
        elif t == "CCRC":
            out |= {"SNF", "AL"}
    return out


# ASSUMPTION: board & care and assisted living are both non-medical residential care, so they are compatible
# with each other; either is incompatible with a nursing home (SNF). Mixed HUD categories match either.
_COMPAT = {"SNF": {"SNF"}, "AL": {"AL", "BC"}, "BC": {"BC", "AL"}}


def care_compatible(hud_classes: set[str], fac_care_types) -> bool:
    fac = facility_care_classes(fac_care_types)
    if not hud_classes or not fac:
        return True                       # unknown on either side: no basis to exclude
    return any(fac & _COMPAT[c] for c in hud_classes)


def split_borrower_prefix(name: str | None) -> tuple[str, str] | None:
    """'NEXCARE- HOLT SENIOR CARE' -> ('NEXCARE', 'HOLT SENIOR CARE'); None when there is no usable '-'."""
    if not name or "-" not in name:
        return None
    pre, rest = name.split("-", 1)
    pre, rest = " ".join(pre.split()).upper(), rest.strip(" -&")
    if not pre or not rest:
        return None
    return pre, rest


def qualifying_prefixes(names) -> dict[str, int]:
    """Prefixes treated as borrower-group codes: seen on >= 2 HUD loans, or containing a digit.
    `names` = one property name per distinct HUD loan. Returns {prefix: loan count}."""
    counts: dict[str, int] = defaultdict(int)
    for n in names:
        sp = split_borrower_prefix(n)
        if sp:
            counts[sp[0]] += 1
    return {p: c for p, c in counts.items() if c >= 2 or re.search(r"\d", p)}


def hud_name_variants(name: str | None, prefixes: dict) -> tuple[list[str], str | None]:
    """Names to score (raw, and stripped when the prefix qualifies; the higher score wins) + the prefix."""
    raw = (name or "").strip()
    sp = split_borrower_prefix(raw)
    if sp and sp[0] in prefixes:
        return [raw, sp[1]], sp[0]
    return [raw], None


def hud_best(idx: "FacilityIndex", rec: dict, names: list[str], hud_classes: set[str], cfg: dict) -> MatchResult:
    """Best facility for a HUD loan. Incompatible care types are never candidates; 'auto' needs the exact zip."""
    best_fac, best_score, n = None, None, 0
    for fac in idx.candidates(rec):
        if cfg.get("require_care_type_match", True) and not care_compatible(hud_classes, fac.get("care_types")):
            continue
        if direction_conflict(rec.get("name"), fac.get("name")):
            continue
        n += 1
        s = max(pair_score({**rec, "name": nm}, fac) for nm in names)
        same_zip = zip5(rec.get("zip5")) is not None and zip5(rec.get("zip5")) == zip5(fac.get("zip5"))
        if best_score is None or s > best_score or (s == best_score and same_zip):
            best_fac, best_score = fac, s
    if best_fac is None:
        return MatchResult(None, None, "new", 0)
    status = classify_match(best_score)
    if status == "new":
        return MatchResult(None, best_score, "new", n)
    if status == "auto" and cfg.get("auto_requires_exact_zip", True):
        if zip5(rec.get("zip5")) is None or zip5(rec.get("zip5")) != zip5(best_fac.get("zip5")):
            status = "needs_review"
    return MatchResult(str(best_fac["facility_id"]), best_score, status, n)


# ---------------------------------------------------------------- direction rule (Gate 2 follow-up)

_DIR_WORDS = {"east": "E", "west": "W", "north": "N", "south": "S", "northeast": "NE", "northwest": "NW",
              "southeast": "SE", "southwest": "SW", "ne": "NE", "nw": "NW", "se": "SE", "sw": "SW"}
# single N/S/E/W as a standalone token, but not an initial such as "E.A. Hawse"
_DIR_LETTER = re.compile(r"(?<![A-Za-z.])([NSEWnsew])\.?(?![A-Za-z.])")


def directions(name: str | None) -> set[str]:
    """Direction words in a name: 'Autumn House West' -> {'W'}. Apostrophes are removed first so "St. Ann's"
    does not yield a standalone 's'."""
    text = re.sub(r"['\u2019`]", "", name or "")
    out = {_DIR_WORDS[w] for w in (t.lower() for t in re.findall(r"[A-Za-z]+", text)) if w in _DIR_WORDS}
    out |= {m.group(1).upper() for m in _DIR_LETTER.finditer(text)}
    return out


def direction_conflict(a: str | None, b: str | None) -> bool:
    """Both names carry direction words and they differ -> different buildings ('... West' vs '... East').
    Only one side with a direction word -> no conflict."""
    da, db = directions(a), directions(b)
    return bool(da) and bool(db) and da != db


# ---------------------------------------------------------------- real borrower groups (Gate 2 follow-up)

# Words that make a prefix a generic descriptor, not a company: GENERIC_NAME_TOKENS plus these.
BORROWER_GENERIC_EXTRA = {"senior", "seniors", "living", "village", "villa", "place", "mid", "tri", "north", "south",
                          "east", "west", "aged", "elder", "elderly", "residential", "geriatric"}
# ASSUMPTION: ordinary English / place words. A prefix made only of these (and generic words) is ambiguous:
# kept only if it contains a digit or its loans span 2+ states (Gate 2 rule, e.g. 'REGENCY').
BORROWER_AMBIGUOUS = {
    "regency", "independence", "cardinal", "oasis", "infinity", "granite", "milestone", "bluegrass", "birchwood",
    "greystone", "meridian", "northwind", "concepts", "foundations", "harvest", "diamond", "quality", "advantage",
    "orchard", "park", "spring", "springs", "hills", "hill", "creek", "madison", "cascade", "legacy", "heritage",
    "royal", "golden", "evergreen", "avalon", "prairie", "ridge", "river", "rivers", "sandy", "summit", "creative",
    "olympia", "sunnybrook", "copperfield", "saddleback", "horizon", "liberty", "commons", "country", "glen", "port",
    "front", "porch", "plum", "next", "apple", "citadel", "crowne", "prestige", "provider", "secure", "two",
    "caring", "rocky", "mountain", "portfolio", "fund", "sub", "christian", "hebrew", "life", "view", "manhattan",
    "pointe", "governors", "bay", "trinity", "american", "midwest", "atrium", "athena", "waters", "unihealth",
}


def borrower_group_verdict(prefix: str, states: set[str]) -> str:
    """'keep' | 'drop_generic' | 'drop_ambiguous' for a qualifying HUD borrower-code prefix."""
    toks = [re.sub(r"\d+", "", t) for t in normalize_name(prefix).split()]
    toks = [t for t in toks if t]
    generic = GENERIC_NAME_TOKENS | BORROWER_GENERIC_EXTRA
    if not toks or all(t in generic for t in toks):
        return "drop_generic"
    if all(t in generic or t in BORROWER_AMBIGUOUS for t in toks):
        return "keep" if re.search(r"\d", prefix) or len(states) >= 2 else "drop_ambiguous"
    return "keep"
