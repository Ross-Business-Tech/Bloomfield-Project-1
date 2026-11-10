"""The extraction contract: field names, JSON schema sent to Claude, and post-processing into stg.extraction rows.

Every field the model returns is either copied from the text or the literal string "NOT FOUND".
List fields come back as lists; an empty list is stored as "NOT FOUND" so downstream code sees one convention.
"""
from __future__ import annotations

import re

NOT_FOUND = "NOT FOUND"

# BUILD_SPEC pipeline step 4, in order.
SCALAR_FIELDS = [
    "property_name", "street", "city", "state", "units", "beds",
    "stage_text", "debtor_or_owner", "receiver_or_trustee", "buyer_or_new_operator", "lender",
]
LIST_FIELDS = ["care_type", "distress_type", "event_dates", "amounts"]
FIELDS = [
    "property_name", "street", "city", "state", "units", "beds", "care_type", "distress_type", "stage_text",
    "debtor_or_owner", "receiver_or_trustee", "buyer_or_new_operator", "lender", "event_dates", "amounts",
]

CARE_TYPES = [
    "skilled_nursing", "assisted_living", "memory_care", "independent_living", "ccrc",
    "senior_housing_other", "hospital", "other_health_care",
]
DISTRESS_TYPES = [
    "chapter_11", "chapter_7", "bankruptcy_other", "receivership", "foreclosure", "deed_in_lieu",
    "loan_or_lease_default", "closure", "sale", "operator_change",
]

_str = {"type": "string"}
_property_obj = {
    "type": "object",
    "properties": {k: _str for k in ["property_name", "street", "city", "state", "units", "beds"]},
    "required": ["property_name", "street", "city", "state", "units", "beds"],
    "additionalProperties": False,
}

JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "property_name": _str,
        "street": _str,
        "city": _str,
        "state": _str,
        "units": _str,
        "beds": _str,
        "care_type": {"type": "array", "items": {"type": "string", "enum": CARE_TYPES}},
        "distress_type": {"type": "array", "items": {"type": "string", "enum": DISTRESS_TYPES}},
        "stage_text": _str,
        "debtor_or_owner": _str,
        "receiver_or_trustee": _str,
        "buyer_or_new_operator": _str,
        "lender": _str,
        "event_dates": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"date_text": _str, "event": _str},
                "required": ["date_text", "event"],
                "additionalProperties": False,
            },
        },
        "amounts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"amount_text": _str, "what": _str},
                "required": ["amount_text", "what"],
                "additionalProperties": False,
            },
        },
        "other_properties": {"type": "array", "items": _property_obj},
    },
    "required": FIELDS + ["other_properties"],
    "additionalProperties": False,
}


def _clean(value) -> str:
    if value is None:
        return NOT_FOUND
    s = re.sub(r"\s+", " ", str(value)).strip()
    if not s or s.upper().rstrip(".") in {"NOT FOUND", "N/A", "NA", "NONE", "UNKNOWN", "NULL"}:
        return NOT_FOUND
    return s


PARTY_FIELDS = ["debtor_or_owner", "receiver_or_trustee", "buyer_or_new_operator", "lender"]
_AFFILIATE = re.compile(r"^((an?|the)\s+)?(affiliates?|subsidiary|subsidiaries|entity|entities)\s+(of|affiliated with)\s+", re.I)


def clean_party(value: str) -> str:
    """Names only: drop 'an affiliate of' lead-ins; a value that is a description ('a Texas-based investor')
    rather than a name becomes NOT FOUND."""
    parts = []
    for part in value.split(";"):
        p = _AFFILIATE.sub("", part.strip())
        if not p or re.match(r"^(an?)\s", p, re.I):
            continue
        parts.append(p)
    return "; ".join(parts) or NOT_FOUND


def postprocess(raw: dict, source_text: str = "") -> tuple[dict, list[str]]:
    """Model JSON -> (fields jsonb, not_found_fields). Empty values become "NOT FOUND".

    Also records `unverified_fields`: scalar text fields whose value does not appear in the source text
    (after case/whitespace/punctuation normalization). These are kept but flagged for review, never silently used
    as if verified.
    """
    fields: dict = {}
    for f in SCALAR_FIELDS:
        fields[f] = _clean(raw.get(f))
    for f in PARTY_FIELDS:
        if fields[f] != NOT_FOUND:
            fields[f] = clean_party(fields[f])
    for f in ("units", "beds"):
        if fields[f] != NOT_FOUND:
            digits = re.sub(r"[^\d]", "", fields[f])
            fields[f] = digits or NOT_FOUND
    ct = sorted({c for c in (raw.get("care_type") or []) if c in CARE_TYPES})
    fields["care_type"] = ct or NOT_FOUND
    dt = sorted({d for d in (raw.get("distress_type") or []) if d in DISTRESS_TYPES})
    if {"chapter_11", "chapter_7"} & set(dt):
        dt = [d for d in dt if d != "bankruptcy_other"]  # bankruptcy_other = no chapter stated
    fields["distress_type"] = dt or NOT_FOUND
    dates = [
        {"date_text": _clean(d.get("date_text")), "event": _clean(d.get("event"))}
        for d in (raw.get("event_dates") or [])
        if re.search(r"\d", _clean(d.get("date_text")))  # a date needs a day or a year; drops "Monday", "last June"
    ]
    fields["event_dates"] = dates or NOT_FOUND
    amounts = [
        {"amount_text": _clean(a.get("amount_text")), "what": _clean(a.get("what"))}
        for a in (raw.get("amounts") or [])
        if _clean(a.get("amount_text")) != NOT_FOUND
    ]
    fields["amounts"] = amounts or NOT_FOUND
    others = []
    for p in raw.get("other_properties") or []:
        q = {k: _clean(p.get(k)) for k in _property_obj["required"]}
        if q["property_name"] != NOT_FOUND or q["street"] != NOT_FOUND:
            others.append(q)
    fields["other_properties"] = others

    not_found = [f for f in FIELDS if fields[f] == NOT_FOUND]
    if source_text:
        hay = loose(source_text)
        unverified = []
        for f in SCALAR_FIELDS:
            v = fields[f]
            if v == NOT_FOUND or f in ("units", "beds"):
                continue
            parts = [p for p in re.split(r";", v) if p.strip()]
            if any(loose(p) not in hay for p in parts):
                unverified.append(f)
        for f, key in (("event_dates", "date_text"), ("amounts", "amount_text")):
            if fields[f] != NOT_FOUND and any(loose(x[key]) not in hay for x in fields[f]):
                unverified.append(f)
        fields["unverified_fields"] = unverified
    return fields, not_found


def loose(s: str) -> str:
    """Lowercase, unify quotes, drop punctuation, collapse whitespace (for 'is this literally in the text')."""
    s = s.lower().replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    s = re.sub(r"[^\w$%\s]", " ", s)
    return " " + re.sub(r"\s+", " ", s).strip() + " "
