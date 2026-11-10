"""Sale-process detection, news place rule, CourtListener cap / 429 stop. All names are made up; no network."""
from datetime import date

import httpx

from src.discover.courts import courtlistener, sale_check as sc
from src.discover.courts.sale_check import FamilyRow


# ── stage + buyer ──────────────────────────────────────────────────────────────────────────────────────────────
def test_stage_keywords():
    assert sc.classify_stage("Motion of the Debtors for Entry of an Order Authorizing the Sale of Substantially All Assets") == "sale motion"
    assert sc.classify_stage("Order (I) Approving Bidding Procedures and (II) Scheduling an Auction") == "auction"
    assert sc.classify_stage("Motion to Approve Bid Procedures") == "bidding procedures"
    assert sc.classify_stage("Notice of Designation of Stalking Horse Bidder") == "stalking horse"
    assert sc.classify_stage("Order Approving the Sale of the Debtors' Assets Free and Clear") == "sale order"
    assert sc.classify_stage("Notice of Closing of Sale") == "sale closed"
    assert sc.classify_stage("Asset Purchase Agreement filed by Debtor") == "sale motion"
    assert sc.classify_stage("Motion to Extend Exclusivity Period") is None
    assert sc.classify_stage("Notice of Transfer of Claim pursuant to Rule 3001(e)") is None
    assert sc.classify_stage("Motion to sell de minimis assets") is None
    assert sc.classify_stage(None) is None


def test_news_stage_and_latest_stage_wins():
    assert sc.classify_stage("Maple Group acquired the community", news=True) == "sale closed"
    assert sc.classify_stage("Maple Group acquired the community") is None
    assert sc.classify_stage("Broker arranges $12.5M sale of portfolio", news=True) == "sale closed"
    items = [sc.Evidence("sale motion", "a", "u1"), sc.Evidence("sale order", "b", "u2"), sc.Evidence("auction", "c", "u3")]
    assert sc.best_evidence(items).stage == "sale order"
    assert sc.best_evidence([]) is None


def test_buyer_only_when_literal():
    assert sc.extract_buyer("The debtors later selected 101 W. Elm Street Holdings, LLC as buyer.") == "101 W. Elm Street Holdings, LLC"
    assert sc.extract_buyer("Notice of Designation of Bluefin Care Partners, LLC as Stalking Horse Bidder") == "Bluefin Care Partners, LLC"
    assert sc.extract_buyer("completed the sale of substantially all assets to Harbor Lane Operations, LLC on May 1") == "Harbor Lane Operations, LLC"
    assert sc.extract_buyer("Order approving sale to the Purchaser") is None
    assert sc.extract_buyer("Notice of Successful Bidder") is None
    assert sc.extract_buyer("Maple Ridge Group acquired the property", news=True) == "Maple Ridge Group"


def test_sentences_keep_abbreviations():
    s = sc.sentences("The court approved the sale under order Dkt. 143 to Elm Co. Inc. as buyer. Next sentence here.")
    assert len(s) == 2 and "Dkt. 143" in s[0]


ELEVENFLO_PAGE = """<html><body><nav>Skip to main content Cases</nav><span>Toggle sidebar ElevenFlo</span>
<p>Willowmere Senior Living is in Chapter 11. The debtors selected Oakfield Care Partners, LLC as stalking horse bidder.
The court approved the sale under order Dkt. 210 .</p><h2>Docket filings per week</h2>
<ul><li class="group/filing relative flex"><span><time dateTime="2026-10-02">Oct 2</time></span>
<span class="line-clamp-2" title="Motion for Entry of an Order Approving Bidding Procedures filed by Debtor">Bidding Procedures Motion</span>
<span><a aria-label="Open Dkt. 150, Bidding Procedures Motion" href="/api/search/open/abc/">Dkt. 150</a></span></li>
<li class="group/filing relative flex"><span><time dateTime="2026-10-05">Oct 5</time></span>
<span class="line-clamp-2" title="Monthly Operating Report">Operating Report</span></li></ul></body></html>"""


def test_scan_elevenflo_page():
    items, scanned = sc.scan_elevenflo("https://elevenflo.com/cases/willowmere-1", ELEVENFLO_PAGE)
    assert scanned >= 2
    best = sc.best_evidence(items)
    assert best.stage == "sale order" and best.url == "https://elevenflo.com/cases/willowmere-1"
    assert "Dkt. 210" in best.text and "/api/search/open" not in best.url
    entries = sc.parse_elevenflo_entries(ELEVENFLO_PAGE)
    assert entries[0]["dkt"] == "150" and entries[0]["date"] == "2026-10-02"
    stalking = [i for i in items if i.stage == "stalking horse"]
    assert stalking and stalking[0].buyer == "Oakfield Care Partners, LLC"


# ── news: name AND place ───────────────────────────────────────────────────────────────────────────────────────
def test_name_phrases():
    assert sc.name_phrases("Quillfeather Senior Living, LLC") == ["Quillfeather Senior Living"]
    assert sc.name_phrases("The Larches Senior Living LLC") == ["The Larches Senior Living"]
    assert sc.name_phrases("Harbor Point of Springfield, LLC") == ["Harbor Point of Springfield", "Harbor Point"]
    assert sc.name_phrases("Courtyard JV LLC") == []          # single word: too common
    assert sc.name_phrases("Senior Care Center LLC") == []    # only generic words
    assert sc.name_phrases("Zinnia Wind Down, Inc., f/k/a Zinnia Care Centers, Inc.") == ["Zinnia Wind Down"]


def test_news_requires_name_and_place():
    phrases = ["Quillfeather Senior Living"]
    places = sc.place_patterns({"Dayton"}, {"OH"})
    body = "Quillfeather Senior Living sold its community to Maple Ridge Group. Terms were not disclosed."
    # name + city -> match
    ev = sc.news_match("Dealbook", body + " The building is in Dayton.", phrases, places, url="https://news.test/a")
    assert ev and ev.stage == "sale closed" and ev.buyer is None or ev.buyer == "Maple Ridge Group"
    # name + state (full name, or ', OH' postal code)
    assert sc.news_match("Dealbook", body + " It sits in Ohio.", phrases, places)
    assert sc.news_match("Dealbook", body + " Located in Kettering, OH.", phrases, places)
    # name without place -> no match (lowercase 'oh' is not a state)
    assert sc.news_match("Dealbook", body + " oh well, located in Austin, Texas.", phrases, places) is None
    # place without name -> no match
    assert sc.news_match("Dealbook", "Another operator sold a building in Dayton, OH.", phrases, places) is None
    # no places known -> no match
    assert sc.news_match("Dealbook", body + " Dayton", phrases, []) is None


def test_news_headline_fallback():
    places = sc.place_patterns(set(), {"PA"})
    ev = sc.news_match("Broker Arranges $50.8M Sale of CCRC Portfolio in Pennsylvania",
                       "A broker arranged the deal. Quillfeather Senior Living…", ["Quillfeather Senior Living"], places)
    assert ev and ev.stage == "sale closed" and ev.text.startswith("Headline:")


# ── CourtListener channel: cap, budget, 429 ────────────────────────────────────────────────────────────────────
def fam(i):
    return FamilyRow(i, f"Made Up Family {i}", date(2026, 1, i % 28 + 1), "txnb", f"26-{10000 + i}", "yes",
                     [{"id": i, "courtlistener_id": str(500 + i), "docket_no": f"26-{10000 + i}",
                       "source_url": f"https://www.courtlistener.com/docket/{500 + i}/x/", "claims_agent_url": None,
                       "name": f"Made Up Family {i}"}])


def cl_client(tmp_path, monkeypatch, handler):
    monkeypatch.setenv("COURTLISTENER_TOKEN", "test-token")
    return courtlistener.make_client(transport=httpx.MockTransport(handler), cache_dir=tmp_path,
                                     sleep=lambda s: None, min_interval=0, max_retries=0)


def patch_db(monkeypatch, fams, used=0):
    saved = []
    monkeypatch.setattr(sc, "courtlistener_candidates", lambda conn, f, now=None: fams)
    monkeypatch.setattr(sc, "requests_used_last_24h", lambda conn, now=None: used)
    monkeypatch.setattr(sc, "save_check", lambda conn, fid, ch, **kw: saved.append((fid, ch, kw)))
    return saved


class NoCommitConn:
    def commit(self):
        pass


def test_courtlistener_cap_15_one_request_each(tmp_path, monkeypatch):
    calls = []

    def h(req):
        calls.append(str(req.url))
        assert req.url.path == "/api/rest/v4/docket-entries/"
        assert req.headers["authorization"] == "Token test-token"
        return httpx.Response(200, json={"results": [
            {"entry_number": 12, "date_filed": "2026-03-01", "description": "Order Approving Bidding Procedures"}],
            "next": "https://www.courtlistener.com/api/rest/v4/docket-entries/?cursor=x"})
    fams = [fam(i) for i in range(1, 31)]
    saved = patch_db(monkeypatch, fams)
    with cl_client(tmp_path, monkeypatch, h) as client:
        s = sc.run_courtlistener(NoCommitConn(), fams, client, max_families=40, log=lambda *a: None)
    assert s["checked"] == 15 and s["requests"] == 15 and len(calls) == 15     # hard cap 15, no pagination
    assert all("docket=" in u for u in calls)
    assert [f for f, _, _ in saved] == list(range(1, 16))                       # newest-first order preserved
    assert saved[0][2]["ev"].stage == "bidding procedures"


def test_courtlistener_paginates_only_with_spare_budget(tmp_path, monkeypatch):
    pages = {"n": 0}

    def h(req):
        pages["n"] += 1
        if "cursor" in str(req.url):
            return httpx.Response(200, json={"results": [{"entry_number": 3, "description": "Sale Order entered"}], "next": None})
        return httpx.Response(200, json={"results": [{"entry_number": 9, "description": "Fee application"}],
                                         "next": "https://www.courtlistener.com/api/rest/v4/docket-entries/?docket=501&cursor=p2"})
    fams = [fam(1)]
    saved = patch_db(monkeypatch, fams)
    with cl_client(tmp_path, monkeypatch, h) as client:
        s = sc.run_courtlistener(NoCommitConn(), fams, client, max_families=15, log=lambda *a: None)
    assert s["requests"] == 2 and saved[0][2]["ev"].stage == "sale order"


def test_courtlistener_stops_on_429_without_retry(tmp_path, monkeypatch):
    calls = []

    def h(req):
        calls.append(1)
        if len(calls) == 3:
            return httpx.Response(429, headers={"Retry-After": "3600"})
        return httpx.Response(200, json={"results": [], "next": None})
    slept = []
    fams = [fam(i) for i in range(1, 10)]
    saved = patch_db(monkeypatch, fams)
    monkeypatch.setenv("COURTLISTENER_TOKEN", "t")
    client = courtlistener.make_client(transport=httpx.MockTransport(h), cache_dir=tmp_path,
                                       sleep=slept.append, min_interval=0, max_retries=0)
    with client:
        s = sc.run_courtlistener(NoCommitConn(), fams, client, max_families=15, log=lambda *a: None)
    assert len(calls) == 3 and "429" in s["stopped"] and "3600" in s["stopped"]
    assert s["checked"] == 2 and len(saved) == 2       # the rate-limited family is not recorded
    assert 3600 not in slept and 3600.0 not in slept   # never waited out Retry-After / retried


def test_courtlistener_respects_daily_budget(tmp_path, monkeypatch):
    calls = []
    fams = [fam(i) for i in range(1, 10)]
    patch_db(monkeypatch, fams, used=12)
    with cl_client(tmp_path, monkeypatch, lambda r: calls.append(1) or httpx.Response(200, json={"results": [], "next": None})) as c:
        s = sc.run_courtlistener(NoCommitConn(), fams, c, max_families=15, log=lambda *a: None)
    assert len(calls) == 3 and s["budget"] == 3
    patch_db(monkeypatch, fams, used=15)
    with cl_client(tmp_path, monkeypatch, lambda r: calls.append(1) or httpx.Response(200, json={})) as c:
        s = sc.run_courtlistener(NoCommitConn(), fams, c, max_families=15, log=lambda *a: None)
    assert len(calls) == 3 and "budget" in s["stopped"]
