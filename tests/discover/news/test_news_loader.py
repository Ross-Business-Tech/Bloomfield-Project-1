import httpx

from src.discover.news import loader as L
from src.discover.news.text import canonical_url, html_to_text

ROBOTS = "User-agent: *\nDisallow: /private/\n"
LONG = "<p>" + ("Receiver appointed for the community. " * 60) + "</p>"

RSS = f"""<?xml version="1.0"?><rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/"><channel>
<item><title>Operator files Chapter 11</title><link>https://pub.test/a?utm_source=x&amp;id=1#top</link>
<pubDate>Mon, 09 Nov 2026 10:00:00 GMT</pubDate><description>&lt;p&gt;Short &amp;amp; sweet&lt;/p&gt;</description></item>
<item><title>Operator files Chapter 11</title><link>https://pub.test/a/?id=1</link><description>Short</description></item>
<item><title>Paywalled</title><link>https://pub.test/paywall</link><description>Teaser text</description></item>
<item><title>Blocked by robots</title><link>https://pub.test/private/x</link><description>Teaser2</description></item>
<item><title>No date</title><link>https://pub.test/nodate</link><description>Nothing</description></item>
<item><title>Full in feed</title><link>https://pub.test/full</link><description>s</description><content:encoded><![CDATA[{LONG}]]></content:encoded></item>
<item><title>No link</title><description>x</description></item>
</channel></rss>"""

ARTICLE = "<html><body><nav>MENU</nav><article><h1>T</h1><p>" + ("Full article body text. " * 40) + "</p></article><footer>F</footer></body></html>"


def handler(request):
    path = request.url.path
    if path == "/robots.txt":
        return httpx.Response(200, text=ROBOTS)
    if path == "/feed":
        return httpx.Response(200, content=RSS.encode())
    if path == "/a":
        return httpx.Response(200, text=ARTICLE)
    if path == "/paywall":
        return httpx.Response(403)
    return httpx.Response(404)


def test_html_to_text_prefers_article_and_strips_chrome():
    t = html_to_text(ARTICLE)
    assert "MENU" not in t and "F" != t[-1] and t.startswith("T")


def test_canonical_url_strips_utm_and_fragment():
    assert canonical_url("https://Pub.test/a/?utm_source=x&id=1#top") == "https://pub.test/a?id=1"


def test_parse_feed_dates_and_links():
    docs = L.parse_feed(RSS.encode(), "shn_rss")
    assert len(docs) == 6                       # entry without link dropped
    assert docs[0].published_at.year == 2026 and docs[4].published_at is None
    assert docs[0].body_text == "Short & sweet"
    assert docs[-1].kind == "full_feed"


def test_dedupe_by_canonical_url(tmp_path):
    docs = L.dedupe(L.parse_feed(RSS.encode(), "shn_rss"))
    assert len([d for d in docs if d.title == "Operator files Chapter 11"]) == 1


def test_load_rss_fulltext_rules(tmp_path):
    res = L.load_rss("shn_rss", [(None, "https://pub.test/feed")], limit=None, fulltext_cap=None,
                     transport=httpx.MockTransport(handler), cache_dir=tmp_path)
    by = {d.title: d for d in res.docs}
    assert by["Operator files Chapter 11"].kind == "full"          # fetched
    assert by["Paywalled"].kind == "summary" and by["Paywalled"].body_text == "Teaser text"  # 403 kept summary
    assert by["Blocked by robots"].kind == "summary"              # robots -> summary only
    assert by["Full in feed"].kind == "full_feed"


def test_gnews_blocked_marks_manual(tmp_path):
    def h(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /rss/search\n")
        return httpx.Response(500)
    res = L.load_rss("gnews_rss", [("q", "https://news.google.com/rss/search?q=x")], limit=None,
                     fulltext_cap=0, transport=httpx.MockTransport(h), cache_dir=tmp_path)
    assert res.manual and not res.docs


WARN_HTML = """<table><tr><th>Company</th><th>Location</th><th>Employees</th><th>Notice Date</th></tr>
<tr><td><a href="/layoffs/company/x">Sunny Senior Living LLC</a></td><td>Tampa, FL</td><td>40</td><td>2026-10-01</td></tr>
<tr><td>Acme Widgets</td><td>Ohio</td><td>9</td><td>2026-10-02</td></tr></table>"""


def test_warn_filters_and_dates(tmp_path):
    def h(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text=ROBOTS)
        return httpx.Response(200, text=WARN_HTML)
    res = L.load_warn(limit=None, transport=httpx.MockTransport(h), cache_dir=tmp_path)
    assert [d.title for d in res.docs] == ["Sunny Senior Living LLC"]
    assert res.docs[0].published_at.day == 1
    assert "/layoffs/company/x" in res.docs[0].source_url


def test_warn_403_is_manual(tmp_path):
    def h(request):
        return httpx.Response(200, text=ROBOTS) if request.url.path == "/robots.txt" else httpx.Response(403)
    res = L.load_warn(limit=None, transport=httpx.MockTransport(h), cache_dir=tmp_path)
    assert res.manual
