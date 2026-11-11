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
    assert '<meta name="robots" content="noindex, nofollow">' in body and "<title>Deal Radar</title>" in body
    assert "default-src 'self'" in headers["Content-Security-Policy"]
    code, _, body = get(site + "/api/snapshot.json", PW)
    data = json.loads(body)
    assert code == 200 and set(data) == set(server.SNAPSHOT_KEYS)
    assert {i["property"] for i in data["items"]} == {"<b>Acme Manor</b>", "Birch Home"}   # rendered as text in the UI
    assert get(site + "/static/app.js", PW)[0] == 200 and get(site + "/static/../server.py", PW)[0] == 404
    assert get(site + "/api/list_a.json", PW)[0] == 200


def test_front_end_never_parses_data_as_html_and_names_no_partner():
    static = Path(server.__file__).parent / "static"
    js = (static / "app.js").read_text()
    assert "innerHTML" not in js and "insertAdjacentHTML" not in js and "document.write" not in js
    for f in [*static.glob("*.html"), *static.glob("*.js"), *static.glob("*.css"),
              *Path(server.__file__).parent.glob("*.py"), Path("render.yaml")]:
        assert "bloomfield" not in f.read_text().lower(), f
    html_text = (static / "index.html").read_text()
    for src in re.findall(r'<script src="(https://[^"]+)"', html_text):     # CDN scripts pinned + integrity-checked
        assert re.search(r"@\d+\.\d+\.\d+/", src), src
    assert html_text.count('integrity="sha384-') == html_text.count('<script src="https://')


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
    assert [i["lists"] for i in snap["items"]] == [["A"], ["A"]]
    assert snap["items"][0]["sources"] == ["https://a.example/1"]                  # licensed links dropped
    assert all("breakdown" in i and "licensed_buyer" not in i["breakdown"] for i in snap["items"])
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


def test_packed_upload_round_trips_and_stays_under_the_render_limit(tmp_path):
    snap = snapshot.build(ROWS, {"past-deal"})
    snap.update(explore=[], funnel=[], sources_table=[], rules={})
    out = tmp_path / "site_snapshot.json"
    snapshot.write(snap, out)
    packed = out.with_suffix(".b64")
    assert packed.stat().st_size < snapshot.UPLOAD_LIMIT
    back = server.load_snapshot(str(packed))
    assert back["counts"] == snap["counts"] and back["list_a"][0]["property"] == "<b>Acme Manor</b>"   # rebuilt
    assert set(back) >= {"leads", "list_a", "list_b", "top20"}


def _private_terms() -> list[str]:
    """The private term list (eval/private, gitignored): provider names, partner name, past deals. Empty in CI."""
    p = Path(__file__).resolve().parents[2] / "eval" / "private" / "forbidden_terms.txt"
    if not p.exists():
        return []
    return [t.strip().lower() for t in p.read_text().splitlines() if t.strip() and not t.startswith("#")]


def test_sources_table_names_only_public_sources():
    ids = [s[0] for s in snapshot.SOURCES]
    assert "licensed_data" not in ids and not any("licensed" in s[0] for s in snapshot.SOURCES)
    text = json.dumps(snapshot.SOURCES).lower()
    assert "licensed" not in text
    assert not [t for t in _private_terms() if t in text]


def test_real_extra_pages_have_no_private_traces():
    """Database + score-run check (skipped offline): Explore, funnel and sources carry no licensed traces and no
    past-deal homes."""
    if not snapshot.EXCLUDE_FILE.exists():
        pytest.skip("no private exclusion list")
    try:
        from src.common.db import connect
        conn = connect()
    except Exception:
        pytest.skip("no database connection")
    with conn:
        skip = snapshot.excluded()
        names = {(r["n"].upper(), (r["c"] or "").upper(), r["s"] or "") for r in conn.execute(
            "select canonical_name n, city c, state s from core.facility where facility_id = any(%s::uuid[])", (list(skip),))}
        rows = snapshot.watch_rows(names)
        snap = {"counts": {"leads": 0, "list_a": 0, "list_b": 0, "list_c": 0}}
        extra = {"explore": rows, "funnel": snapshot.funnel(conn, snap, len(rows)), "sources": snapshot.source_rows(conn),
                 "rules": snapshot.rules_block()}
    text = json.dumps({k: v for k, v in extra.items() if k != "explore"}, default=str).lower()
    assert "licensed" not in json.dumps(extra, default=str).lower()
    assert not [t for t in _private_terms() if t in text]          # funnel / sources / rules text
    for n, c, st in names:
        assert not any(r["property"].upper() == n and (r["city"] or "").upper() == c for r in rows), n
    assert all(r["sources"] and all(u.startswith("http") for u in r["sources"]) for r in rows)
    assert all("hard event" in r["reason"] or "broker sale" in r["reason"] for r in rows)
