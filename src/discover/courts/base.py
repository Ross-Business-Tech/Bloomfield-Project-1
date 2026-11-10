"""Shared pieces for the court loaders: record types, keyword rules, polite client with RFC 9309 robots matching."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

UNKNOWN_COURT = "UNKNOWN"   # stg.court_case.court is NOT NULL; the claims agents do not always publish it
WINDOW_MONTHS = 36

# Terms that mark a case/debtor as healthcare or senior living (matched on names; Unknown stays NULL elsewhere).
HEALTHCARE_NAME = re.compile(
    r"senior|assisted\s+living|memory\s+care|nursing|skilled|continuing\s+care|life\s+plan|retirement|"
    r"elder|geriatric|hospice|rehab|health|\bcare\b|caring|medical|hospital|clinic|\bliving\b|manor|"
    r"\bvillage\b|methodist|lutheran|care\s*center|home\s+health",
    re.I,
)
# Docket entries / documents about a sale process.
SALE_TEXT = re.compile(
    r"\bsale\b|bidding\s+procedures|stalking[\s-]*horse|asset\s+purchase|purchase\s+agreement|"
    r"successful\s+bidder|\bauction\b|\bpurchaser\b|section\s+363|\b363\b",
    re.I,
)
# Buyer evidence: the document names or implies a buyer.
BUYER_EVIDENCE = re.compile(
    r"stalking[\s-]*horse|\bpurchaser\b|successful\s+bidder|asset\s+purchase\s+agreement|"
    r"\bAPA\b",
    re.I,
)


def has_buyer_evidence(*texts: str | None) -> bool:
    return any(t and BUYER_EVIDENCE.search(t) for t in texts)


def months_ago(n: int, today: date | None = None) -> date:
    today = today or date.today()
    m = today.year * 12 + (today.month - 1) - n
    y, mo = divmod(m, 12)
    return date(y, mo + 1, min(today.day, 28))


def parse_date(value) -> date | None:
    if not value:
        return None
    if isinstance(value, date):
        return value
    s = str(value).strip()
    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%B %d, %Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(s[:19] if "%H" in fmt else s, fmt).date()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def to_datetime(d: date | None) -> datetime | None:
    return datetime(d.year, d.month, d.day, tzinfo=timezone.utc) if d else None


@dataclass
class CaseRecord:
    source_id: str
    source_url: str
    court: str
    docket_no: str
    case_name: str | None = None
    chapter: str | None = None
    filed_date: date | None = None
    terminated_date: date | None = None
    debtor_name: str | None = None
    claims_agent_url: str | None = None
    courtlistener_id: str | None = None


@dataclass
class DocRecord:
    source_id: str
    source_url: str
    title: str | None
    body_text: str | None
    published_at: datetime | None = None
    buyer_evidence: bool = False


@dataclass
class SourceResult:
    source_id: str
    cases: list[CaseRecord] = field(default_factory=list)
    docs: list[DocRecord] = field(default_factory=list)
    skipped: int = 0
    note: str | None = None   # e.g. "COURTLISTENER_TOKEN not set, skipping"
