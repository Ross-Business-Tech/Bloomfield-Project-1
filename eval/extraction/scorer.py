"""Score extraction output against the hand-labeled cases in eval/extraction/cases.json.

An article is "fully correct" when every one of the 15 BUILD_SPEC fields matches after light normalization.

Label format (per field in `expected`):
- text fields: "value", or a list of acceptable alternatives ["N.Y.", "New York"]. "NOT FOUND" means must be NOT FOUND.
  Several entities in one value are separated by "; " and compared as a set.
- units / beds: compared as digits only.
- stage_text: "NOT FOUND", or a reference quote. Any prediction counts as correct when it is not NOT FOUND and
  appears verbatim (case/punctuation-insensitive) in the document text: the field is a free quote, so the check is
  "is it really a quote", not "is it the same quote".
- care_type / distress_type / event_dates / amounts: {"required": [...], "optional": [...]} or "NOT FOUND"
  (= nothing required, nothing allowed). Prediction is correct when it contains every required item and nothing
  outside required + optional. Dates are compared after month-name normalization; amounts as numbers
  ("$76 million" == "$76M" == 76000000).
"""
from __future__ import annotations

import re

from src.extract.schema import FIELDS, NOT_FOUND, loose

MONTHS = {
    "january": "jan", "jan": "jan", "february": "feb", "feb": "feb", "march": "mar", "mar": "mar",
    "april": "apr", "apr": "apr", "may": "may", "june": "jun", "jun": "jun", "july": "jul", "jul": "jul",
    "august": "aug", "aug": "aug", "september": "sep", "sept": "sep", "sep": "sep", "october": "oct", "oct": "oct",
    "november": "nov", "nov": "nov", "december": "dec", "dec": "dec",
}
_MULT = {"thousand": 1e3, "k": 1e3, "million": 1e6, "m": 1e6, "mm": 1e6, "billion": 1e9, "b": 1e9, "bn": 1e9}


def norm_text(s: str) -> str:
    return loose(s).strip()


def norm_digits(s: str) -> str:
    return s if s == NOT_FOUND else re.sub(r"[^\d]", "", s)


def norm_date(s: str) -> str:
    words = re.findall(r"[a-z]+|\d+", s.lower())
    return " ".join(MONTHS.get(w, w) for w in words)


def norm_amount(s: str) -> str:
    m = re.search(r"([\d][\d,]*(?:\.\d+)?)\s*(thousand|million|billion|mm|bn|k|m|b)?\b", s.lower())
    if not m:
        return norm_text(s)
    value = float(m.group(1).replace(",", "")) * _MULT.get(m.group(2) or "", 1)
    return f"{value:.0f}"


def _as_set(value, key=None, norm=norm_text) -> set[str]:
    if value == NOT_FOUND or value is None:
        return set()
    if isinstance(value, str):
        return {norm(p) for p in value.split(";") if p.strip()}
    out = set()
    for item in value:
        if isinstance(item, dict):
            item = item.get(key, "")
        if str(item).strip():
            out.add(norm(str(item)))
    return out


LIST_KEYS = {"care_type": (None, lambda x: x.strip().lower()), "distress_type": (None, lambda x: x.strip().lower()),
             "event_dates": ("date_text", norm_date), "amounts": ("amount_text", norm_amount)}


def field_correct(field: str, expected, predicted, doc_text: str = "") -> bool:
    if field in LIST_KEYS:
        key, norm = LIST_KEYS[field]
        pred = _as_set(predicted, key, norm)
        if expected == NOT_FOUND:
            return not pred
        req = {norm(x) for x in expected.get("required", [])}
        opt = {norm(x) for x in expected.get("optional", [])}
        return req <= pred and pred <= (req | opt)

    predicted = predicted if isinstance(predicted, str) else NOT_FOUND
    if field == "stage_text":
        if expected == NOT_FOUND:
            return predicted == NOT_FOUND
        return predicted != NOT_FOUND and loose(predicted) in loose(doc_text)

    alternatives = expected if isinstance(expected, list) else [expected]
    norm = norm_digits if field in ("units", "beds") else norm_text
    for alt in alternatives:
        if alt == NOT_FOUND or predicted == NOT_FOUND:
            if alt == predicted:
                return True
            continue
        if _as_set(alt, norm=norm) == _as_set(predicted, norm=norm):
            return True
    return False


def score_case(case: dict, predicted_fields: dict, doc_text: str) -> dict:
    misses = [f for f in FIELDS
              if not field_correct(f, case["expected"][f], predicted_fields.get(f, NOT_FOUND), doc_text)]
    return {"doc_id": case["doc_id"], "fully_correct": not misses, "misses": misses}
