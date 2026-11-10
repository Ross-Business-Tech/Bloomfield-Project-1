"""Fetch RSS feeds and WARN notices politely and turn them into documents."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import quote_plus

import feedparser
import httpx

from src.common.http import PoliteClient, SourceBlocked
from src.common.raw_writer import content_hash

from .text import canonical_url, html_to_text

SHN_FEED = "https://seniorhousingnews.com/feed/"
SNN_FEED = "https://skillednursingnews.com/feed/"
GNEWS_BASE = "https://news.google.com/rss/search"
GNEWS_QUERIES = [
    '"assisted living" receivership',
    '"senior living" "chapter 11"',
    '"nursing home" "new operator"',
    '"memory care" foreclosure',
    '"assisted living" "deed in lieu"',
    '"senior living" "delayed opening"',
    '"assisted living" "change of ownership"',
]
WARN_URL = "https://www.warnfirehose.com/layoffs"
WARN_PAGES = [WARN_URL, WARN_URL + "/industry/healthcare"]
WARN_KEYWORDS = re.compile(
    r"senior|assisted living|nursing|memory care|retirement|care center|rehab|post-acute|"
    r"long[- ]term care|\bopco\b|\bpropco\b|\bmanor\b|\bvillage\b|\bterrace\b",
    re.I)
MCK_SL_FEED = "https://www.mcknightsseniorliving.com/feed/"
MCK_LTC_FEED = "https://www.mcknights.com/feed/"
SHB_FEED = "https://seniorshousingbusiness.com/feed/"
# Wave 3.5 (terms/robots checked Nov 10, 2026): Bondoro = free Ghost tag feeds (Ch. 11 cases > $10M liabilities,
# robots allows all but /ghost/, /email/, /r/, /members/api/); WhatNow = WordPress search feeds (robots disallows
# */page/, so never paginate; no healthcare category exists, so search terms are used).
BONDORO_FEEDS = [("chapter-11-filing-alerts", "https://bondoro.com/tag/chapter-11-filing-alerts/rss/"),
                 ("case-summaries", "https://bondoro.com/tag/case-summaries/rss/")]
WHATNOW_QUERIES = ["senior living", "assisted living", "nursing home", "memory care"]
GDELT_URL = "https://api.gdeltproject.org/api/v2/doc/doc"
GDELT_INTERVAL = 5.0   # GDELT asks for at most 1 request per 5 s
GDELT_TIMESPAN = "3months"   # DOC 2.0 lookback limit
BACKFILL_FEEDS = {"shn_rss", "snn_rss", "shb_rss"}   # WordPress feeds that support ?paged=N
MAX_BACKFILL_PAGES = 400
SOURCES = ["shn_rss", "snn_rss", "gnews_rss", "mcknights_sl_rss", "mcknights_ltc_rss", "shb_rss", "gdelt_doc", "warn",
           "bondoro_rss", "whatnow_rss"]
FULLTEXT_FEED_MIN = 1500   # feed body this long counts as full text


@dataclass
class Doc:
    source_id: str
    source_url: str
    title: str | None
    body_text: str | None
    published_at: datetime | None
    kind: str = "summary"      # full | full_feed | summary
    query: str | None = None


@dataclass
class SourceResult:
    source_id: str
    docs: list[Doc] = field(default_factory=list)
    per_query: dict[str, dict] = field(default_factory=dict)
    blocked: str | None = None
    manual: str | None = None


def whatnow_url(query: str) -> str:
    return f"https://whatnow.com/?s={quote_plus(query)}&feed=rss2"


def gnews_url(query: str) -> str:
    return f"{GNEWS_BASE}?q={quote_plus(query)}&hl=en-US&gl=US&ceid=US:en"


def entry_published(entry) -> datetime | None:
    for key in ("published", "updated"):
        raw = entry.get(key)
        if raw:
            try:
                dt = parsedate_to_datetime(raw)
                return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
            except (TypeError, ValueError):
                pass
    st = entry.get("published_parsed") or entry.get("updated_parsed")
    if st:
        return datetime(*st[:6], tzinfo=timezone.utc)
    return None


def _entry_html(entry) -> str:
    content = entry.get("content")
    if content:
        return max((c.get("value", "") for c in content), key=len, default="")
    return ""


def parse_feed(content: bytes, source_id: str, *, query: str | None = None) -> list[Doc]:
    """Parse feed bytes. Summary-only docs unless the feed carries a long body."""
    feed = feedparser.parse(content)
    docs = []
    for e in feed.entries:
        link = e.get("link")
        if not link:
            continue   # rule: no URL -> no row
        title = html_to_text(e.get("title", ""), prefer_article=False) or None
        summary = html_to_text(e.get("summary", ""), prefer_article=False)
        body = html_to_text(_entry_html(e), prefer_article=False)
        if len(body) >= FULLTEXT_FEED_MIN:
            text, kind = body, "full_feed"
        else:
            text, kind = (summary or body or None), "summary"
        docs.append(Doc(source_id, link.strip(), title, text, entry_published(e), kind, query))
    return docs


def dedupe(docs: list[Doc]) -> list[Doc]:
    seen_urls, seen_hashes, out = set(), set(), []
    for d in docs:
        key = canonical_url(d.source_url)
        h = content_hash(f"{d.title or ''}\n{d.body_text or ''}")
        if key in seen_urls or h in seen_hashes:
            continue
        seen_urls.add(key)
        seen_hashes.add(h)
        d.source_url = key if "news.google.com" not in key else d.source_url
        out.append(d)
    return out


def fetch_fulltext(client: PoliteClient, doc: Doc) -> bool:
    """Try to upgrade a summary doc to full text. Never retries/bypasses; returns True on success."""
    if "news.google.com" in doc.source_url:
        return False   # redirect/JS wrapper; do not try to defeat
    try:
        if not client.allowed(doc.source_url):
            return False
        r = client.get(doc.source_url)
    except SourceBlocked:
        return False
    except Exception:
        return False
    if r.status_code != 200:
        return False
    text = html_to_text(r.text)
    if len(text) < 500 or (doc.body_text and len(text) <= len(doc.body_text)):
        return False
    doc.body_text, doc.kind = text, "full"
    return True


def paged_url(url: str, n: int) -> str:
    return url if n == 1 else f"{url}?paged={n}"


def backfill_feed(client: PoliteClient, source_id: str, url: str, months: int, limit: int | None,
                  now: datetime | None = None) -> tuple[list[Doc], str | None]:
    """Walk WordPress ?paged=N pages back `months` months. Stops at the cutoff, an empty page,
    or any non-200. Returns (docs, note)."""
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=round(months * 30.44))
    docs: list[Doc] = []
    seen: set[str] = set()
    for n in range(1, MAX_BACKFILL_PAGES + 1):
        page = paged_url(url, n)
        if not client.allowed(page):
            return docs, f"robots.txt disallows {page}"
        r = client.get(page)
        if r.status_code != 200:
            return docs, None          # past the last page (404) or an error: stop quietly
        items = [d for d in parse_feed(r.content, source_id) if d.source_url not in seen]
        if not items:
            return docs, None
        seen.update(d.source_url for d in items)
        docs.extend(items)
        dated = [d.published_at for d in items if d.published_at]
        if dated and min(dated) < cutoff:
            return [d for d in docs if not d.published_at or d.published_at >= cutoff], None
        if limit and len(docs) >= limit:
            return docs[:limit], None
    return docs, None


def load_rss(source_id: str, feeds: list[tuple[str | None, str]], *, limit: int | None,
             fulltext_cap: int | None, transport=None, cache_dir=None, backfill_months: int = 0) -> SourceResult:
    """feeds = [(query_label, url)]. limit applies per feed."""
    res = SourceResult(source_id)
    kw = {"transport": transport}
    if cache_dir:
        kw["cache_dir"] = cache_dir
    with PoliteClient(source_id, **kw) as client:
        collected: list[Doc] = []
        for label, url in feeds:
            try:
                r = client.get(url)
            except SourceBlocked as exc:
                res.blocked = str(exc)
                if label:
                    res.per_query[label] = {"items": 0, "error": str(exc)}
                continue
            if r.status_code != 200:
                if label:
                    res.per_query[label] = {"items": 0, "error": f"HTTP {r.status_code}"}
                continue
            if backfill_months and source_id in BACKFILL_FEEDS:
                docs, note = backfill_feed(client, source_id, url, backfill_months, limit)
                if note:
                    res.per_query[label or url] = {"items": len(docs), "note": note}
            else:
                docs = parse_feed(r.content, source_id, query=label)[: limit or None]
            if label:
                res.per_query[label] = {"items": len(docs)}
            elif backfill_months:
                res.per_query.setdefault(url, {"items": len(docs)})
            collected.extend(docs)
        docs = dedupe(collected)
        budget = fulltext_cap
        for d in docs:
            if d.kind == "full_feed":
                continue
            if budget is not None and budget <= 0:
                break
            if budget is not None:
                budget -= 1
            fetch_fulltext(client, d)
        res.docs = docs
        if not docs and res.blocked and all('error' in v for v in res.per_query.values() or [{'error': 1}]):
            res.manual = f'all feeds blocked: {res.blocked}'
    return res


def load_warn(*, limit: int | None, transport=None, cache_dir=None) -> SourceResult:
    """WARN firehose: filter to senior living / nursing employers. Manual if blocked or JS-only.

    """
    res = SourceResult("warn")
    kw = {"transport": transport}   # PoliteClient honours the site's Crawl-delay itself
    if cache_dir:
        kw["cache_dir"] = cache_dir
    docs: list[Doc] = []
    with PoliteClient("warn", **kw) as client:
        for url in WARN_PAGES:
            try:
                r = client.get(url)
            except SourceBlocked as exc:
                res.blocked = str(exc)
                res.manual = f"blocked: {exc}"
                return res
            if r.status_code != 200:
                res.manual = f"HTTP {r.status_code} from {url}"
                return res
            rows = parse_warn_html(r.text)
            res.per_query[url] = {"rows": len(rows)}
            for link, title, body, published in rows:
                if WARN_KEYWORDS.search(f"{title} {body}"):
                    docs.append(Doc("warn", link, title, body, published, "summary"))
    if not any(v["rows"] for v in res.per_query.values()):
        res.manual = "no parseable dated notice rows on any page (likely JavaScript-rendered)"
        return res
    res.docs = dedupe(docs)[: limit or None]
    return res


_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def parse_warn_html(html: str) -> list[tuple[str, str, str, datetime | None]]:
    """Return (unique_url, employer, row_text, notice_date) for each notice row (rows with an ISO date).

    Several notices share one company page, so a row hash is appended to keep source_url unique.
    """
    from html.parser import HTMLParser
    from urllib.parse import urljoin

    class P(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.rows, self.cur, self.cell, self.link = [], None, None, None

        def handle_starttag(self, tag, attrs):
            if tag == "tr":
                self.cur, self.link = [], None
            elif tag in ("td", "th") and self.cur is not None:
                self.cell = []
            elif tag == "a" and self.cur is not None and self.link is None:
                href = dict(attrs).get("href")
                if href and not href.startswith("#"):
                    self.link = urljoin(WARN_PAGES[0], href)

        def handle_endtag(self, tag):
            if tag in ("td", "th") and self.cell is not None and self.cur is not None:
                self.cur.append(" ".join("".join(self.cell).split()))
                self.cell = None
            elif tag == "tr" and self.cur:
                self.rows.append((self.cur, self.link))
                self.cur = None

        def handle_data(self, data):
            if self.cell is not None:
                self.cell.append(data)

    p = P()
    p.feed(html)
    out = []
    for cells, link in p.rows:
        date = next((c for c in cells if _DATE.match(c)), None)
        if not date or len(cells) < 3:
            continue   # header / state / summary rows
        title = cells[0]
        body = " | ".join(c for c in cells if c)
        base = link or WARN_PAGES[0]
        sep = "&" if "?" in base else "?"
        unique = f"{base}{sep}row={content_hash(body)[:12]}"
        out.append((unique, title, body, datetime.fromisoformat(date).replace(tzinfo=timezone.utc)))
    return out


# ── GDELT DOC 2.0 ──────────────────────────────────────────
def parse_gdelt(content: bytes, query: str) -> list[Doc]:
    """GDELT answers errors/throttling with plain text (sometimes HTTP 200); that raises ValueError."""
    import json
    try:
        data = json.loads(content)
    except ValueError:
        raise ValueError(f"non-JSON GDELT reply: {content[:120]!r}")
    docs = []
    for a in data.get("articles") or []:
        url = (a.get("url") or "").strip()
        if not url:
            continue
        seen = None
        if a.get("seendate"):
            try:
                seen = datetime.strptime(a["seendate"], "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
            except ValueError:
                pass
        docs.append(Doc("gdelt_doc", url, (a.get("title") or "").strip() or None, None, seen, "summary", query))
    return docs


def load_gdelt(*, limit: int | None, fulltext_cap: int | None, transport=None, cache_dir=None,
               interval: float = GDELT_INTERVAL, queries: list[str] | None = None) -> SourceResult:
    """published_at holds GDELT's seen-date (first time GDELT saw the article), not the publisher's date."""
    res = SourceResult("gdelt_doc")
    kw = {"transport": transport}
    if cache_dir:
        kw["cache_dir"] = cache_dir
    collected: list[Doc] = []
    with PoliteClient("gdelt_doc", min_interval=interval, **kw) as api:
        for q in queries or GNEWS_QUERIES:
            params = {"query": f"{q} sourcelang:english", "mode": "artlist", "format": "json",
                      "maxrecords": str(min(limit or 250, 250)), "timespan": GDELT_TIMESPAN, "sort": "datedesc"}
            try:
                r = api.get(GDELT_URL, params)
                if r.status_code != 200:
                    res.per_query[q] = {"items": 0, "error": f"HTTP {r.status_code}"}
                    continue
                docs = parse_gdelt(r.content, q)
            except SourceBlocked as exc:
                res.blocked = str(exc)
                res.manual = f"blocked: {exc}"
                return res
            except (ValueError, httpx.HTTPError) as exc:
                res.per_query[q] = {"items": 0, "error": str(exc)}
                continue
            res.per_query[q] = {"items": len(docs)}
            collected.extend(docs)
    docs = dedupe(collected)
    budget = fulltext_cap
    if budget != 0:
        # publisher pages use their own client (1 req/s/host, each host's robots.txt)
        with PoliteClient("gdelt_doc", **kw) as pub:
            for d in docs:
                if budget is not None and budget <= 0:
                    break
                if budget is not None:
                    budget -= 1
                fetch_fulltext(pub, d)
    res.docs = docs
    return res
