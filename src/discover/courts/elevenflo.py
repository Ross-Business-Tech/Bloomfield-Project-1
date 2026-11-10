"""ElevenFlo case pages (robots blocks /search and /api/search/open, so we only read /cases/<slug> pages
listed in the public sitemap https://elevenflo.com/sitemap/cases.xml)."""
from __future__ import annotations

import html
import re

from .base import (HEALTHCARE_NAME, WINDOW_MONTHS, CaseRecord, DocRecord, SourceResult,
                   has_buyer_evidence, months_ago, parse_date, to_datetime)

BASE = "https://elevenflo.com"
SITEMAP = f"{BASE}/sitemap/cases.xml"
HEALTH_INDUSTRY = re.compile(r"health|senior|nursing|medical|hospital|care", re.I)

_META_DESC = re.compile(r'<meta name="description" content="([^"]*)"')
_DESC = re.compile(
    r"No\.\s*(?P<docket>[^\s(]+)\s*\((?P<court>[^)]*)\)\.\s*Chapter\s*(?P<chapter>\d+)(?:,\s*filed\s*(?P<filed>[A-Za-z]+ \d{1,2}, \d{4}))?"
)
_H1 = re.compile(r"<h1[^>]*>(.*?)</h1>", re.S)
_INDUSTRY = re.compile(r'industry\\?"\s*:\s*\\?"([^"\\]+)')
_PETITION = re.compile(r'petitionDate\\?"\s*:\s*\\?"(\d{4}-\d{2}-\d{2})')


def sitemap_urls(xml: str) -> list[str]:
    return re.findall(r"<loc>\s*(https://elevenflo\.com/cases/[^<\s]+)\s*</loc>", xml)


def order_slugs(urls: list[str]) -> list[str]:
    """Healthcare-looking slugs first so a --limit run spends its requests where they matter."""
    hit = [u for u in urls if HEALTHCARE_NAME.search(u.rsplit("/", 1)[-1].replace("-", " "))]
    rest = [u for u in urls if u not in set(hit)]
    return hit + rest


def _visible_text(page: str) -> str:
    t = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", page, flags=re.S)
    t = html.unescape(re.sub(r"<[^>]+>", " ", t))
    return re.sub(r"\s+", " ", t).strip()


def parse_case_page(url: str, page: str, *, today=None):
    """Return (CaseRecord | None, DocRecord | None, reason). reason explains why a page was not kept."""
    m = _META_DESC.search(page)
    d = _DESC.search(html.unescape(m.group(1))) if m else None
    h1 = _H1.search(page)
    name = html.unescape(re.sub(r"<[^>]+>", "", h1.group(1))).strip() if h1 else None
    if not d or not name:
        return None, None, "no docket number on page"
    ind = _INDUSTRY.search(page)
    industry = ind.group(1) if ind else None
    if not ((industry and HEALTH_INDUSTRY.search(industry)) or HEALTHCARE_NAME.search(name)):
        return None, None, "not healthcare"
    filed = parse_date(d.group("filed")) or (parse_date(_PETITION.search(page).group(1)) if _PETITION.search(page) else None)
    if filed and filed < months_ago(WINDOW_MONTHS, today):
        return None, None, "older than window"
    case = CaseRecord(
        "elevenflo", url, d.group("court").strip(), d.group("docket"), case_name=name,
        chapter=d.group("chapter"), filed_date=filed, debtor_name=name, claims_agent_url=None,
    )
    text = _visible_text(page)
    i = text.find(name)
    body = text[i:i + 6000] if i >= 0 else text[:6000]
    doc = DocRecord("elevenflo", url, f"{name} - ElevenFlo case summary", body, to_datetime(filed),
                    has_buyer_evidence(body))
    return case, doc, ""


def fetch_elevenflo(client, limit=None, *, today=None) -> SourceResult:
    res = SourceResult("elevenflo")
    sm = client.get(SITEMAP)
    if sm.status_code != 200:
        res.note = f"HTTP {sm.status_code} for case sitemap"
        return res
    urls = order_slugs(sitemap_urls(sm.text))
    for url in urls[: limit or len(urls)]:
        r = client.get(url)
        if r.status_code != 200:
            res.skipped += 1
            continue
        case, doc, _ = parse_case_page(url, r.text, today=today)
        if case:
            res.cases.append(case)
            res.docs.append(doc)
    return res
