import json
from datetime import datetime, timezone

import httpx

from src.discover.news import loader as L


def feed(items):
    body = "".join(
        f"<item><title>{t}</title><link>https://wp.test/{t}</link><pubDate>{d}</pubDate><description>x</description></item>"
        for t, d in items)
    return f'<?xml version="1.0"?><rss version="2.0"><channel>{body}</channel></rss>'.encode()


PAGES = {
    "1": feed([("a", "Mon, 09 Nov 2026 10:00:00 GMT"), ("b", "Sun, 08 Nov 2026 10:00:00 GMT")]),
    "2": feed([("c", "Mon, 09 Mar 2026 10:00:00 GMT"), ("d", "Sat, 07 Mar 2026 10:00:00 GMT")]),
    "3": feed([("e", "Mon, 09 Jun 2025 10:00:00 GMT"), ("f", "Sun, 08 Jun 2025 10:00:00 GMT")]),
    "4": feed([("g", "Mon, 09 Jun 2024 10:00:00 GMT")]),
}
NOW = datetime(2026, 11, 9, tzinfo=timezone.utc)


def wp_handler(allow_paged=True):
    seen = []

    def h(request):
        seen.append(str(request.url))
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\n" + ("" if allow_paged else "Disallow: /feed/?paged=\n"))
        if request.url.path == "/feed/":
            return httpx.Response(200, content=PAGES[request.url.params.get("paged", "1")]) \
                if request.url.params.get("paged", "1") in PAGES else httpx.Response(404)
        return httpx.Response(404)
    return h, seen


def client(h, tmp_path, **kw):
    return L.PoliteClient("shn_rss", transport=httpx.MockTransport(h), cache_dir=tmp_path,
                          min_interval=0, sleep=lambda s: None, **kw)


def test_backfill_stops_at_cutoff(tmp_path):
    h, seen = wp_handler()
    with client(h, tmp_path) as c:
        docs, note = L.backfill_feed(c, "shn_rss", "https://wp.test/feed/", 24, None, now=NOW)
    assert [d.title for d in docs] == ["a", "b", "c", "d", "e", "f"]   # page 4 crosses cutoff, g is dropped
    assert not any("paged=5" in u for u in seen)
    assert note is None


def test_backfill_stops_on_404(tmp_path):
    h, _ = wp_handler()
    with client(h, tmp_path) as c:
        docs, _ = L.backfill_feed(c, "shn_rss", "https://wp.test/feed/", 600, None, now=NOW)
    assert len(docs) == 7


def test_backfill_respects_robots(tmp_path):
    h, seen = wp_handler(allow_paged=False)
    with client(h, tmp_path) as c:
        docs, note = L.backfill_feed(c, "shn_rss", "https://wp.test/feed/", 24, None, now=NOW)
    assert docs == [] or len(docs) == 2          # page 1 (no ?paged) is fine, page 2 is refused
    assert note and "robots" in note


def test_mcknights_403_is_manual(tmp_path):
    def h(request):
        return httpx.Response(200, text="User-agent: *\n") if request.url.path == "/robots.txt" else httpx.Response(403)
    res = L.load_rss("mcknights_ltc_rss", [(None, "https://mck.test/feed/")], limit=None, fulltext_cap=0,
                     transport=httpx.MockTransport(h), cache_dir=tmp_path)
    assert res.manual and not res.docs


def test_shb_feed_parses(tmp_path):
    def h(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow:\n")
        return httpx.Response(200, content=PAGES["1"])
    res = L.load_rss("shb_rss", [(None, "https://shb.test/feed/")], limit=None, fulltext_cap=0,
                     transport=httpx.MockTransport(h), cache_dir=tmp_path)
    assert len(res.docs) == 2 and res.docs[0].published_at.year == 2026


GDELT = json.dumps({"articles": [
    {"url": "https://pub.test/x?utm_source=g", "title": "Operator files Chapter 11", "seendate": "20261108T153000Z"},
    {"url": "https://pub.test/x", "title": "Operator files Chapter 11", "seendate": "20261108T160000Z"},
    {"url": "https://pub.test/y", "title": "Other", "seendate": "bad"},
    {"title": "no url"}]}).encode()


def test_parse_gdelt():
    docs = L.parse_gdelt(GDELT, "q")
    assert len(docs) == 3 and docs[0].published_at == datetime(2026, 11, 8, 15, 30, tzinfo=timezone.utc)
    assert docs[2].published_at is None


def test_load_gdelt_params_dedupe_and_throttle_text(tmp_path):
    calls = []

    def h(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        calls.append(request.url.params)
        if len(calls) == 2:
            return httpx.Response(200, text="Please limit requests to one every 5 seconds.")
        return httpx.Response(200, content=GDELT)
    res = L.load_gdelt(limit=None, fulltext_cap=0, transport=httpx.MockTransport(h), cache_dir=tmp_path,
                       interval=0, queries=['"a b" c', '"d e" f', '"g h" i'])
    assert calls[0]["mode"] == "artlist" and calls[0]["format"] == "json" and calls[0]["timespan"] == "3months"
    assert "error" in res.per_query['"d e" f']
    assert sorted(d.source_url for d in res.docs) == ["https://pub.test/x", "https://pub.test/y"]
    assert res.docs[0].body_text is None            # title + URL + seen date only
