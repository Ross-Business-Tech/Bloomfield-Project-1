"""Website: password on every page, noindex, robots disallow; the snapshot holds public-view fields only, no
licensed facts and no past-deal homes."""
import base64
import json
import re
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from src.web import server, snapshot

PW = "test-" + "pw-12345"
ROWS = [  # shaped like app.v_lead_list rows, with things that must never reach the snapshot
    {"facility_id": "keep-1", "property": "<b>Acme Manor</b>", "city": "X", "state": "IL", "care_types": ["AL"],
     "units": 90, "beds": None, "distress_type": "chapter_11", "stage": "late", "buyer": "Buyer Co",
     "buyer_status": "named", "score": 80, "flags": [], "source_urls": ["https://a.example/1", "licensed:x"],
     "buyer_source_url": "licensed:x", "score_breakdown": {"licensed_buyer": True}, "review_status": "new"},
    {"facility_id": "keep-2", "property": "Birch Home", "city": "Y", "state": "OH", "care_types": ["SNF"],
     "units": None, "beds": 100, "distress_type": "receivership", "stage": "late", "buyer": "Buyer Co",
     "buyer_status": "named", "score": 70, "flags": [], "source_urls": ["https://a.example/2"],
     "buyer_source_url": "https://a.example/b", "score_breakdown": {}},
    {"facility_id": "past-deal", "property": "Past Deal Home", "city": "Z", "state": "NY", "care_types": [],
     "units": None, "beds": None, "distress_type": "receivership", "stage": "late", "buyer": "NOT FOUND",
     "buyer_status": "NOT FOUND", "score": 60, "flags": [], "source_urls": ["https://a.example/3"],
     "buyer_source_url": None, "score_breakdown": {}},
]


@pytest.fixture()
def site():
    snap = snapshot.build(ROWS, {"past-deal"})
    srv = ThreadingHTTPServer(("127.0.0.1", 0), server.make_handler(snap, PW))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def get(url, pw=None):
    req = urllib.request.Request(url)
    if pw is not None:
        req.add_header("Authorization", "Basic " + base64.b64encode(f"team:{pw}".encode()).decode())
    try:
        r = urllib.request.urlopen(req)
        return r.status, dict(r.headers), r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read().decode()


@pytest.mark.parametrize("path", ["/", "/api/leads.json", "/api/list_c.json", "/exports/list_a.csv", "/nope"])
def test_every_page_needs_the_password(site, path):
    code, headers, body = get(site + path)
    assert code == 401 and "WWW-Authenticate" in headers and "Acme" not in body
    assert get(site + path, "wrong-password")[0] == 401


def test_pages_with_password_are_noindex(site):
    code, headers, body = get(site + "/", PW)
    assert code == 200 and headers["X-Robots-Tag"] == "noindex, nofollow"
    assert '<meta name="robots" content="noindex, nofollow">' in body
    assert "&lt;b&gt;Acme" in body and "<b>Acme" not in body
    assert get(site + "/api/list_a.json", PW)[0] == 200


def test_robots_and_health_are_the_only_open_paths(site):
    code, _, body = get(site + "/robots.txt")
    assert code == 200 and "Disallow: /" in body
    code, _, body = get(site + "/healthz")
    assert code == 200 and body == "ok"


def test_refuses_to_start_without_a_password(monkeypatch):
    monkeypatch.delenv("SITE_PASSWORD", raising=False)
    with pytest.raises(server.ConfigError):
        server.site_password()


def _check_snapshot(snap, banned_names=()):
    text = json.dumps(snap)
    assert "licensed" not in text.lower()
    assert "score_breakdown" not in text and "facility_id" not in text and "review_status" not in text
    for n in banned_names:
        assert n.lower() not in text.lower(), n
    for r in snap["leads"]:
        assert str(r["public_source_link"]).startswith("http") or r["public_source_link"] == "NOT FOUND"


def test_snapshot_has_no_licensed_fields_and_no_past_deal_homes():
    snap = snapshot.build(ROWS, {"past-deal"})
    _check_snapshot(snap, ["Past Deal Home"])
    assert snap["counts"] == {"leads": 2, "list_a": 2, "list_b": 0, "list_c": 1}
    assert snap["list_c"][0]["buyer"] == "Buyer Co" and snap["list_c"][0]["homes_in_list"] == 2
    assert snap["list_a"][0]["public_source_link"] == "https://a.example/1"      # licensed buyer link dropped


def test_real_snapshot_from_the_public_view():
    """Database check (skipped offline / without the private exclusion list)."""
    if not snapshot.EXCLUDE_FILE.exists():
        pytest.skip("no private exclusion list")
    try:
        from src.common.db import connect
        conn = connect()
    except Exception:
        pytest.skip("no database connection")
    with conn:
        rows = [dict(r) for r in conn.execute("select * from app.v_lead_list order by score desc nulls last")]
        skip = snapshot.excluded()
        names = [r["canonical_name"] for r in conn.execute(
            "select canonical_name from core.facility where facility_id = any(%s::uuid[])", (list(skip),))]
    snap = snapshot.build(rows, skip)
    _check_snapshot(snap, names)
    assert snap["counts"]["leads"] == len(rows) - sum(1 for r in rows if str(r["facility_id"]) in skip)


def test_server_has_no_database_or_key_access():
    code = re.sub(r'"""[\s\S]*?"""', "", Path(server.__file__).read_text())
    assert not re.search(r"psycopg|httpx|supabase|SUPABASE|ANTHROPIC|COURTLISTENER|^\s*(from|import) src\.", code, re.M)
