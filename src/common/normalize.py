"""Name / address normalizers and the facility match score (docs/BUILD_SPEC.md step 3).

Address outweighs name because facilities are often renamed after a sale.
"""
from __future__ import annotations

import re

from rapidfuzz import fuzz

from .config import thresholds

NOT_FOUND = "NOT FOUND"

_NAME_DROP = {
    "llc", "l l c", "inc", "incorporated", "corp", "corporation", "co", "company", "lp", "llp", "ltd",
    "pllc", "propco", "opco", "the", "dba", "holdings", "operations", "operating", "realty",
}
_NAME_EXPAND = {
    "ctr": "center", "cntr": "center", "rehab": "rehabilitation", "hlth": "health", "nsg": "nursing",
    "hc": "healthcare", "mgmt": "management", "sr": "senior", "snr": "senior", "comm": "community",
    "assist": "assisted", "lvg": "living", "mem": "memory", "st": "saint", "mt": "mount",
}
_STREET_EXPAND = {
    "st": "street", "str": "street", "ave": "avenue", "av": "avenue", "rd": "road", "dr": "drive",
    "blvd": "boulevard", "ln": "lane", "hwy": "highway", "pkwy": "parkway", "ct": "court", "pl": "place",
    "cir": "circle", "trl": "trail", "ter": "terrace", "sq": "square", "pike": "pike", "tpke": "turnpike",
    "n": "north", "s": "south", "e": "east", "w": "west",
    "ne": "northeast", "nw": "northwest", "se": "southeast", "sw": "southwest",
}
_UNIT = re.compile(r"(?:\b(?:suite|ste|unit|apt|bldg|building|floor)\b\.?|#)\s*[\w-]*")


def _tokens(text: str | None) -> list[str]:
    if not text:
        return []
    text = text.lower().replace("&", " and ")
    text = re.sub(r"[^a-z0-9 ]+", " ", text)
    return text.split()


def normalize_name(name: str | None) -> str:
    text = " ".join(_tokens(name))
    text = re.sub(r"\bl l c\b", " ", text)
    out = [_NAME_EXPAND.get(t, t) for t in text.split() if t not in _NAME_DROP]
    return " ".join(out)


def normalize_street(street: str | None) -> str:
    if not street:
        return ""
    text = _UNIT.sub(" ", street.lower())
    return " ".join(_STREET_EXPAND.get(t, t) for t in _tokens(text))


def street_number(street: str | None) -> str | None:
    m = re.match(r"\s*(\d+[a-z]?)\b", (street or "").lower())
    return m.group(1) if m else None


def zip5(value) -> str | None:
    digits = re.sub(r"\D", "", str(value or ""))
    if not digits:
        return None
    return digits[:5].zfill(5) if len(digits) <= 5 else digits[:5]


def normalize_phone(value) -> str | None:
    digits = re.sub(r"\D", "", str(value or ""))
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits if len(digits) == 10 else None


def match_score(a: dict, b: dict) -> float:
    """a, b: dicts with name, street and optional phone. Higher = more likely the same building."""
    cfg = thresholds()["matching"]
    w = cfg["weights"]
    score = w["name_token_set"] * fuzz.token_set_ratio(normalize_name(a.get("name")), normalize_name(b.get("name")))
    score += w["street_token_sort"] * fuzz.token_sort_ratio(normalize_street(a.get("street")), normalize_street(b.get("street")))
    na, nb = street_number(a.get("street")), street_number(b.get("street"))
    if na and nb:
        score += cfg["street_number_equal_bonus"] if na == nb else -cfg["street_number_differ_penalty"]
    pa, pb = normalize_phone(a.get("phone")), normalize_phone(b.get("phone"))
    if pa and pa == pb:
        score += cfg["phone_equal_bonus"]
    return round(score, 1)


def classify_match(score: float) -> str:
    """'auto' (same facility), 'needs_review' (human checks at Gate 2) or 'new' (create a new facility)."""
    cfg = thresholds()["matching"]
    if score >= cfg["auto_match_at_or_above"]:
        return "auto"
    if score >= cfg["needs_review_at_or_above"]:
        return "needs_review"
    return "new"


def entity_stem(name: str | None) -> str:
    """normalize_name without the trust / master-tenant / op-prop-co suffixes one owner group uses for the same home:
    '<Brand> of Springfield DST' (the trust that owns it) == '<Brand> of Springfield MT, LLC' (its master tenant /
    licence holder) == '<Brand> of Springfield OpCo'."""
    # drop before normalize_name ('MT' would otherwise become 'mount')
    # only as a trailing suffix ('... of X MT, LLC'), never 'Mt. Pleasant'
    raw = re.sub(r"\b(dst|mt|op\s?co|prop\s?co|master\s+tenant)\b[\s,.]*((llc|l\.l\.c\.|inc|lp|ltd)\.?)?\s*$", " ",
                 (name or "").strip(), flags=re.I)
    return normalize_name(raw)


_CITY_ALIASES: dict | None = None


def city_alias(city: str | None, state: str | None) -> str | None:
    """config/city_aliases.yaml: 'Orion', 'MI' -> 'Lake Orion' (case kept: an all-caps input gives an all-caps output)."""
    global _CITY_ALIASES
    if not city or not state:
        return city
    if _CITY_ALIASES is None:
        import yaml
        from src.common.config import PROJECT_ROOT
        path = PROJECT_ROOT / "config" / "city_aliases.yaml"
        raw = (yaml.safe_load(path.read_text(encoding="utf-8")) or {}) if path.exists() else {}
        _CITY_ALIASES = {(str(s).upper(), str(k).strip().upper()): str(v).strip()
                         for s, m in raw.items() for k, v in (m or {}).items()}
    new = _CITY_ALIASES.get((str(state).upper(), city.strip().upper()))
    if not new:
        return city
    return new.upper() if city.isupper() else new
