"""Cheap keyword pre-filter: skip documents with no sign of a distress or ownership event, so they never reach the API.

Deliberately loose (false positives only cost a fraction of a cent; false negatives lose leads). Court documents
(source category 'legal') and WARN notices always pass. Skipped documents are recorded in stg.extraction with
fields.status = 'skipped_prefilter'.
"""
from __future__ import annotations

import re

PREFILTER_VERSION = "kw-v1"
ALWAYS_PASS_SOURCES = {"elevenflo", "warn"}

# Matched case-insensitively on title + body.
STRONG = [
    r"bankrupt", r"chapter\s*(7|11)\b", r"\bch\.\s*(7|11)\b", r"receiver(ship)?\b", r"foreclos", r"deed[- ]in[- ]lieu",
    r"\bdefault(ed|s)?\b", r"insolven", r"liquidat", r"\bauction", r"stalking[- ]horse", r"debtor[- ]in[- ]possession",
    r"\bclosures?\b", r"\b(to|will|plans? to|set to) close\b", r"\bshut(ting)? down\b", r"\bshutter",
    r"change of ownership", r"\bCHOW\b", r"\bnew operator", r"operator transition", r"\btransition(ed|ing)? (the )?operations",
    r"assumes? management", r"takes? over", r"master lease", r"unpaid rent", r"\bwarn notice", r"\blayoffs?\b",
    r"distress",
]
# A sale/acquisition only counts when it is about a property (not "sales team" or "acquires software firm").
DEAL = [r"\bsold\b", r"\bsells?\b", r"\bsale of\b", r"\bacquir(e|es|ed|ing|ition)", r"\bpurchas(e|ed|es)\b",
        r"\bbuyer\b", r"\btransaction\b", r"portfolio"]
PROPERTY = [r"nursing", r"skilled", r"assisted living", r"memory care", r"senior (living|housing)", r"seniors housing",
            r"\bcommunit(y|ies)\b", r"\bfacilit(y|ies)\b", r"\bbeds?\b", r"\bunits?\b", r"\bccrc\b", r"retirement"]

_strong = re.compile("|".join(STRONG), re.I)
_deal = re.compile("|".join(DEAL), re.I)
_property = re.compile("|".join(PROPERTY), re.I)


def passes(source_id: str | None, title: str | None, body: str | None) -> tuple[bool, str]:
    """Return (send_to_model, reason)."""
    if source_id in ALWAYS_PASS_SOURCES:
        return True, f"source {source_id} always passes"
    text = f"{title or ''}\n{body or ''}"
    m = _strong.search(text)
    if m:
        return True, f"keyword: {m.group(0).lower()}"
    d = _deal.search(text)
    if d and _property.search(text):
        return True, f"deal keyword: {d.group(0).lower()}"
    return False, "no distress/sale/operator keywords"
