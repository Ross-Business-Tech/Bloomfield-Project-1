"""Stdlib-only helpers: HTML -> plain text, canonical URLs."""
from __future__ import annotations

import re
from html.parser import HTMLParser
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_SKIP = {"script", "style", "noscript", "nav", "footer", "header", "aside", "form", "svg", "iframe"}
_BLOCK = {"p", "br", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "blockquote", "article", "section"}


class _Extractor(HTMLParser):
    """Collects text. If an <article> exists, only text inside it is kept."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.skip = 0
        self.in_article = 0
        self.all_parts: list[str] = []
        self.article_parts: list[str] = []
        self.has_article = False

    def handle_starttag(self, tag, attrs):
        if tag == "article":
            self.in_article += 1
            self.has_article = True
        if tag in _SKIP:
            self.skip += 1
        if tag in _BLOCK:
            self._add("\n")

    def handle_endtag(self, tag):
        if tag == "article" and self.in_article:
            self.in_article -= 1
        if tag in _SKIP and self.skip:
            self.skip -= 1
        if tag in _BLOCK:
            self._add("\n")

    def handle_data(self, data):
        if not self.skip:
            self._add(data)

    def _add(self, s):
        self.all_parts.append(s)
        if self.in_article:
            self.article_parts.append(s)


def _clean(parts: list[str]) -> str:
    text = "".join(parts)
    text = re.sub(r"[ \t\xa0]+", " ", text)
    lines = [ln.strip() for ln in text.split("\n")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def html_to_text(html: str, prefer_article: bool = True) -> str:
    p = _Extractor()
    p.feed(html or "")
    p.close()
    if prefer_article and p.has_article:
        text = _clean(p.article_parts)
        if text:
            return text
    return _clean(p.all_parts)


def canonical_url(url: str) -> str:
    """Strip utm_* (and similar tracking) params and fragments; drop trailing slash on path."""
    parts = urlsplit(url.strip())
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
             if not k.lower().startswith("utm_") and k.lower() not in {"fbclid", "gclid", "mc_cid", "mc_eid"}]
    path = parts.path
    if len(path) > 1 and path.endswith("/"):
        path = path[:-1]
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, urlencode(query), ""))
