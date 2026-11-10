import json
from datetime import date

import httpx
import pytest

from src.common.http import PoliteClient, SourceBlocked
from src.discover.courts import claims_agents, courtlistener, elevenflo
from src.discover.courts.base import has_buyer_evidence

TODAY = date(2026, 11, 9)


def client(tmp_path, handler, cls=PoliteClient):
    def h(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /wp-admin/\nAllow: /wp-admin/admin-ajax.php\nDisallow: /search\n")
        return handler(req)
    return cls("t", cache_dir=tmp_path, transport=httpx.MockTransport(h), sleep=lambda s: None, min_interval=0)


def test_robots_longest_match_allows_admin_ajax(tmp_path):
    c = client(tmp_path, lambda r: httpx.Response(200, json={}))
    assert c.allowed("https://x.test/wp-admin/admin-ajax.php?action=a")
    assert not c.allowed("https://x.test/wp-admin/options.php")
    assert not c.allowed("https://x.test/search?q=1")
    with pytest.raises(SourceBlocked):
        c.get("https://x.test/search")


def test_buyer_evidence():
    assert has_buyer_evidence("Notice of Stalking Horse bidder")
    assert has_buyer_evidence("Asset Purchase Agreement")
    assert not has_buyer_evidence("Motion to extend time")
    assert not has_buyer_evidence(None)


STRETTO = {"data": [
    {"CASE_NAME": "Sunrise Senior Living LLC", "DEBTOR_NAME": "Sunrise Senior Living LLC", "DB_CASE_NO": "26-10001",
     "NAME": "District of Delaware", "URL": "sunrise", "VANITY_URL": "", "IS_LEGACY": "0", "DATE_FILED": "2026-03-01 07:00:00"},
    {"CASE_NAME": "Acme Widgets", "DEBTOR_NAME": "Acme", "DB_CASE_NO": "26-10002", "NAME": "District of Delaware",
     "URL": "acme", "IS_LEGACY": "0", "DATE_FILED": "2026-03-01 07:00:00"},
    {"CASE_NAME": "Old Care Inc", "DEBTOR_NAME": "Old Care Inc", "DB_CASE_NO": "14-1", "NAME": "District of Delaware",
     "URL": "old", "IS_LEGACY": "1", "DATE_FILED": "2014-03-26 07:00:00"},
    {"CASE_NAME": "No Docket Health", "DEBTOR_NAME": "x", "DB_CASE_NO": None, "CASE_NO": None, "URL": "nd", "IS_LEGACY": "0"},
    {"CASE_NAME": "Vanity Nursing", "DEBTOR_NAME": "Vanity Nursing", "DB_CASE_NO": "26-5", "NAME": None,
     "URL": "v", "VANITY_URL": "vanity.example.com", "IS_LEGACY": "0", "DATE_FILED": None},
]}


def test_stretto_filters_and_urls():
    res = claims_agents.parse_stretto(STRETTO, today=TODAY)
    assert [c.docket_no for c in res.cases] == ["26-10001", "26-5"]
    assert res.cases[0].claims_agent_url == "https://cases.stretto.com/sunrise"
    assert res.cases[1].claims_agent_url == "https://vanity.example.com"
    assert res.cases[1].court == "UNKNOWN" and res.cases[1].filed_date is None
    assert res.skipped == 1


def test_stretto_fetch_uses_ajax_endpoint(tmp_path):
    seen = []

    def h(req):
        seen.append(str(req.url))
        return httpx.Response(200, json=STRETTO)

    res = claims_agents.fetch_stretto(client(tmp_path, h), today=TODAY)
    assert seen == ["https://cases.stretto.com/wp-admin/admin-ajax.php?action=case_list_data"] or "admin-ajax" in seen[0]
    assert len(res.cases) == 2


def test_epiq_parse():
    rows = [
        {"text": "Preferred Care Inc.", "caseNumber": "17-44642", "projectCode": "preferred", "defaultPage": "dockets"},
        {"text": "Widgets Corp", "caseNumber": "1-1", "projectCode": "w", "defaultPage": "info"},
        {"text": "Memory Health LLC", "caseNumber": None, "projectCode": "mh"},
    ]
    res = claims_agents.parse_epiq(rows)
    assert len(res.cases) == 1 and res.skipped == 1
    c = res.cases[0]
    assert c.claims_agent_url == "https://dm.epiq11.com/case/preferred/dockets"
    assert c.filed_date is None and c.chapter is None


PAGE = ('<html><head><meta name="description" content="In re Georgia ProtonCare Center, Inc., No. 26-50882 (Bankr. N.D. Ga.). '
        'Chapter 11, filed January 22, 2026. Recent filings."/></head><body><h1 title="x">Georgia ProtonCare Center, Inc.</h1>'
        '<p>The court approved the sale to Emory as successful bidder under the asset purchase agreement.</p>'
        '<script>{\\"industry\\":\\"Healthcare\\",\\"petitionDate\\":\\"2026-01-22\\"}</script></body></html>')


def test_elevenflo_page():
    case, doc, _ = elevenflo.parse_case_page("https://elevenflo.com/cases/x-8", PAGE, today=TODAY)
    assert (case.court, case.docket_no, case.chapter, case.filed_date) == ("Bankr. N.D. Ga.", "26-50882", "11", date(2026, 1, 22))
    assert doc.buyer_evidence is True
    other = PAGE.replace("Healthcare", "Retail").replace("Georgia ProtonCare Center", "Acme Co")
    assert elevenflo.parse_case_page("u", other, today=TODAY)[2] == "not healthcare"


def test_elevenflo_fetch(tmp_path):
    sm = '<urlset><url><loc>https://elevenflo.com/cases/acme-1</loc></url><url><loc>https://elevenflo.com/cases/care-center-2</loc></url></urlset>'

    def h(req):
        if req.url.path.endswith("cases.xml"):
            return httpx.Response(200, text=sm)
        return httpx.Response(200, text=PAGE)

    res = elevenflo.fetch_elevenflo(client(tmp_path, h), limit=1, today=TODAY)
    assert len(res.cases) == 1 and res.cases[0].source_url.endswith("care-center-2")   # healthcare slug first


# ── CourtListener (mocked) ──────────────────────────────────────────
HIT = {"docket_id": 77, "docketNumber": "26-10010", "caseName": "In re Sunset Assisted Living, LLC", "court_id": "deb",
       "dateFiled": "2026-02-01", "dateTerminated": None, "docket_absolute_url": "/docket/77/in-re-sunset/", "chapter": "11",
       "recap_documents": [{"id": 1, "description": "Motion to approve bidding procedures", "absolute_url": "/docket/77/5/motion/",
                            "document_number": "5", "entry_date_filed": "2026-02-10", "snippet": "stalking horse purchaser"}]}
DISTRICT = {"docket_id": 78, "docketNumber": "1:26-cv-1", "caseName": "Foo v. Bar", "court_id": "nyed",
            "dateFiled": "2026-02-01", "docket_absolute_url": "/docket/78/foo/", "recap_documents": []}
ENTRIES = {"results": [
    {"entry_number": 12, "description": "Order approving sale of substantially all assets", "date_filed": "2026-03-01", "recap_documents": []},
    {"entry_number": 13, "description": "Notice of hearing", "date_filed": "2026-03-02", "recap_documents": []},
], "next": None}


def cl_handler(seen_auth, queries):
    def h(req):
        seen_auth.append(req.headers.get("authorization"))
        if req.url.path.endswith("/courts/"):
            return httpx.Response(200, json={"results": [{"id": "deb"}, {"id": "txsb"}], "next": None})
        if req.url.path.endswith("/search/"):
            assert req.url.params["court"] == "deb txsb"
            assert req.url.params["type"] == "r"
            queries.append(req.url.params["q"])
            return httpx.Response(200, json={"results": [HIT, DISTRICT], "next": None})
        if req.url.path.endswith("/docket-entries/"):
            assert req.url.params["docket"] == "77"
            return httpx.Response(200, json=ENTRIES)
        return httpx.Response(404)
    return h


def test_courtlistener_flow(tmp_path, monkeypatch):
    monkeypatch.setenv("COURTLISTENER_TOKEN", "secret-token")
    auth, queries = [], []
    robots_hits = []

    def route(r):
        if r.url.path == "/robots.txt":
            robots_hits.append(1)
            return httpx.Response(403)           # like the real site: must NOT matter for /api/rest/v4/
        return cl_handler(auth, queries)(r)

    transport = httpx.MockTransport(route)
    c = courtlistener.make_client(transport=transport, cache_dir=tmp_path, sleep=lambda s: None, min_interval=0)
    res = courtlistener.fetch_courtlistener(c, today=TODAY)
    assert not robots_hits
    assert c.requests_made == 1 + 7 + 1 and "requests=9" in res.note
    assert len(queries) == 7 and '"life plan community"' in queries
    assert all(a == "Token secret-token" for a in auth)
    assert len(res.cases) == 1                       # district-court hit dropped, duplicates across queries merged
    case = res.cases[0]
    assert case.source_url == "https://www.courtlistener.com/docket/77/in-re-sunset/"
    assert (case.court, case.chapter, case.debtor_name, case.courtlistener_id) == ("deb", "11", "Sunset Assisted Living, LLC", "77")
    urls = {d.source_url: d for d in res.docs}
    assert urls["https://www.courtlistener.com/docket/77/5/motion/"].buyer_evidence is True
    sale_order = urls["https://www.courtlistener.com/docket/77/in-re-sunset/#entry-12"]
    assert sale_order.buyer_evidence is False        # "sale" only, no purchaser named: unknown stays False
    assert len(res.docs) == 2                        # "Notice of hearing" is not a sale document
    # the token must not be in the on-disk cache
    for f in tmp_path.rglob("*"):
        if f.is_file():
            assert b"secret-token" not in f.read_bytes()
    c.close()


def test_courtlistener_api_403_blocks_and_cap(tmp_path, monkeypatch):
    monkeypatch.setenv("COURTLISTENER_TOKEN", "t")
    t = httpx.MockTransport(lambda r: httpx.Response(403))
    c = courtlistener.make_client(transport=t, cache_dir=tmp_path, sleep=lambda s: None, min_interval=0)
    with pytest.raises(SourceBlocked):
        courtlistener.fetch_courtlistener(c)
    c2 = courtlistener.make_client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"results": [], "next": None})),
                                   cache_dir=tmp_path / "b", sleep=lambda s: None, min_interval=0)
    c2.max_requests = 3
    res = courtlistener.fetch_courtlistener(c2)
    assert "request cap" in res.note


def test_courtlistener_other_hosts_keep_robots(tmp_path, monkeypatch):
    monkeypatch.setenv("COURTLISTENER_TOKEN", "t")
    t = httpx.MockTransport(lambda r: httpx.Response(403) if r.url.path == "/robots.txt" else httpx.Response(200, json={}))
    c = courtlistener.make_client(transport=t, cache_dir=tmp_path, sleep=lambda s: None, min_interval=0)
    with pytest.raises(SourceBlocked):
        c.get("https://www.courtlistener.com/docket/1/x/")


def test_adversary_dropped():
    assert not courtlistener.is_bankruptcy({"caseName": "LTC v. Krzyzewski", "chapter": None})
    assert courtlistener.is_bankruptcy({"caseName": "Acme v. Foo Holdings", "chapter": "11"})


def test_courtlistener_no_token(monkeypatch):
    monkeypatch.delenv("COURTLISTENER_TOKEN", raising=False)
    monkeypatch.setattr(courtlistener, "env", lambda n: (_ for _ in ()).throw(courtlistener.MissingSetting(n)))
    res = courtlistener.run()
    assert res.note == "COURTLISTENER_TOKEN not set, skipping" and not res.cases


def test_courtlistener_search_only_no_entry_fetches(tmp_path, monkeypatch):
    monkeypatch.setenv("COURTLISTENER_TOKEN", "t")
    paths, pages = [], []

    def h(req):
        paths.append(req.url.path)
        if req.url.path.endswith("/courts/"):
            return httpx.Response(200, json={"results": [{"id": "deb"}], "next": None})
        return httpx.Response(200, json={"results": [HIT], "next": None})

    c = courtlistener.make_client(transport=httpx.MockTransport(h), cache_dir=tmp_path, sleep=lambda s: None, min_interval=0)
    res = courtlistener.fetch_courtlistener(c, search_only=True, on_cases=lambda cs: pages.append(list(cs)))
    assert not any("docket-entries" in p for p in paths)
    assert len(paths) == 1 + 7 and len(res.cases) == 1
    assert len(pages) == 1 and pages[0][0].courtlistener_id == "77"     # saved when first seen, not repeated
