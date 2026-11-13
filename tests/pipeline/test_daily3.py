"""Nightly refresh (daily3): stage transitions, override precedence, roundup -> Unverified, cursors, gates."""
import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from src.pipeline import daily3 as D
from src.pipeline.sources import source_of
from src.score import rules

URL = "https://www.courtlistener.com/docket/72222785/inspired/"


def lead(stage="sale", status=None, buyer=None, buyer_status="NOT FOUND"):
    return {"property": "Home A", "stage": "done" if status else stage, "buyer": buyer, "buyer_status": buyer_status,
            "sale_status": {"status": status} if status else None}


def finding(stage_text="sale order", when="2026-11-05", buyer=None, url=URL):
    return D.Finding("L1", stage_text, url, when, buyer)


# ── stage transitions (code rules: sale order -> approved, sale closed -> closed; never down) ──
def test_sale_order_moves_in_a_sale_to_approved():
    c = D.decide(lead(), finding(), None)
    assert c.kind == "stage_changed" and c.old_stage == "sale" and c.new_stage == "done:approved"
    assert c.sale_status == {"status": "approved", "date": "2026-11-05", "source_url": URL, "basis": "court"}
    assert c.reviewed is False


def test_closing_notice_moves_approved_to_closed():
    c = D.decide(lead(status="approved"), finding("sale closed"), None)
    assert c.new_stage == "done:closed" and c.old_stage == "done:approved"


@pytest.mark.parametrize("cur,step", [("approved", "sale order"), ("closed", "sale order"), ("closed", "sale closed"),
                                      (None, "auction"), (None, "stalking horse"), (None, "sale motion")])
def test_no_change_without_a_later_court_step(cur, step):
    assert D.decide(lead(status=cur), finding(step), None) is None


def test_undated_step_is_reported_not_applied():
    c = D.decide(lead(), finding(when=None), None)
    assert c.kind == "undated" and c.sale_status is None


def test_non_web_source_never_changes_a_stage():
    assert D.decide(lead(), finding(url="licensed:provider"), None) is None


def test_court_buyer_fills_missing_or_candidate_buyer_only():
    assert D.decide(lead(), finding(buyer="Acme Senior LLC"), None).buyer == "Acme Senior LLC"
    assert D.decide(lead(buyer="Bidder LLC", buyer_status="candidate"), finding(buyer="Acme LLC"), None).buyer == "Acme LLC"
    assert D.decide(lead(buyer="Named LLC", buyer_status="named"), finding(buyer="Acme LLC"), None).buyer is None


# ── manual overrides always win; only a newer dated public source supersedes (flagged unreviewed) ──
OV = {"date": "2026-10-13", "sale_status": "approved", "buyer": True}


def test_override_wins_against_older_or_same_day_source():
    for when in ("2026-10-01", "2026-10-13"):
        c = D.decide(lead(), finding(when=when), OV)
        assert c.kind == "kept_override" and c.sale_status is None


def test_newer_source_supersedes_override_and_is_unreviewed():
    c = D.decide(lead(status="approved"), finding("sale closed", when="2026-11-05", buyer="Acme LLC"), OV)
    assert c.kind == "stage_changed" and c.supersedes_override and c.reviewed is False
    assert c.buyer == "Acme LLC"        # superseding: the court buyer may replace the hand-checked one


def test_override_buyer_kept_when_not_superseded():
    c = D.decide(lead(buyer="Hand LLC", buyer_status="candidate"), finding(when="2026-11-05", buyer="Acme LLC"),
                 {"date": "2026-10-13", "buyer": True, "sale_status": None})
    assert c.supersedes_override       # newer source: stage changes, flagged
    assert c.buyer == "Acme LLC"


@pytest.mark.parametrize("src,ov,ovd,expected", [
    ({"status": "closed", "date": "2026-11-05", "source_url": URL}, {"status": "approved"}, "2026-10-13", True),
    ({"status": "approved", "date": "2026-11-05", "source_url": URL}, {"status": "approved"}, "2026-10-13", False),  # same step
    ({"status": "closed", "date": "2026-10-02", "source_url": URL}, {"status": "closed"}, "2026-09-08", False),     # later report
    ({"status": "approved", "date": "2026-11-05", "source_url": URL}, {"status": "closed"}, "2026-10-13", False),  # downgrade
    ({"status": "closed", "date": "2026-10-13", "source_url": URL}, {"status": "approved"}, "2026-10-13", False),  # same day
    ({"status": "closed", "date": "2026-10", "source_url": URL}, {"status": "approved"}, "2026-10-13", False),     # month-only
    ({"status": "closed", "date": "2026-11", "source_url": URL}, {"status": "approved"}, "2026-10-13", True),
    ({"status": "closed", "date": None, "source_url": URL}, {"status": "approved"}, "2026-10-13", False),          # undated
    ({"status": "closed", "date": "2026-11-05", "source_url": "licensed:provider"}, {"status": "approved"}, "2026-10-13", False),
])
def test_scoring_rule_sale_supersedes_override(src, ov, ovd, expected):
    assert rules.sale_supersedes_override(src, ov, ovd) is expected


# ── roundup article -> buyer Unverified ──
DEAL = ("{h}\n\n" + "A senior living community with 120 units in Ohio was sold to a regional operator. " * 3).strip()


def test_roundup_detection():
    assert D.is_roundup("https://seniorhousingnews.com/2026/10/16/dealbook#deal-6", "Marshall Pines", None)
    assert D.is_roundup("https://x.com/a", "Senior Living Dealbook: seven deals", "short")
    assert D.is_roundup("https://skillednursingnews.com/2025/07/skilled-nursing-news-dealbook-ensign-acquires", None)
    # a feature article with several headed sections is not a deal roundup
    body = DEAL.format(h="Acme buys Ohio community in Columbus") + "\n\n" + DEAL.format(h="Beta sells Texas portfolio to Gamma")
    assert not D.is_roundup("https://x.com/inside-the-bankruptcy-trend", "Inside the bankruptcy trend", body)
    assert not D.is_roundup("https://x.com/c", "Acme buys Ohio community", DEAL.format(h="Acme buys Ohio community"))


def test_roundup_buyer_is_unverified():
    assert D.news_buyer_status("named", True) == "Unverified"
    assert D.news_buyer_status("candidate", True) == "Unverified"
    assert D.news_buyer_status("named", False) == "named"
    assert D.news_buyer_status("NOT FOUND", True) == "NOT FOUND"


def test_unverified_shows_in_the_unverified_group():
    from src.web.snapshot import buyer_group
    assert buyer_group({"buyer": "Acme LLC", "buyer_status": "Unverified"}) == "inferred"
    snap = {"items": [{"lead_id": "L1", "buyer": "Acme LLC", "buyer_status": "named", "buyer_group": "confirmed",
                       "flags": [], "sources": [URL]}]}
    out = D.apply_changes(snap, [], {"L1": "https://seniorhousingnews.com/x#deal-2"})
    r = out["items"][0]
    assert (r["buyer_status"], r["buyer_group"]) == ("Unverified", "inferred") and "buyer_from_roundup" in r["flags"]
    assert snap["items"][0]["buyer_status"] == "named"        # the base snapshot is never modified


# ── cursors and de-duplication ──
def test_cursor_newer_than_and_undated():
    t = datetime(2026, 11, 11, 12, tzinfo=timezone.utc)
    assert D.newer_than(t, "2026-11-11T11:00:00+00:00")
    assert not D.newer_than(t, "2026-11-11T12:00:00+00:00")
    assert D.newer_than(None, "2026-11-11T12:00:00+00:00") and D.newer_than(t, None)


def test_docket_cursor_only_new_entries():
    es = [{"entry_number": n} for n in (812, 811, 810)]
    assert [e["entry_number"] for e in D.new_entries(es, 810)] == [812, 811]
    assert len(D.new_entries(es, None)) == 3 and D.new_entries(es, 900) == []


def test_cursor_never_moves_backwards():
    c = D.advance({"last_entry": 812, "checked": "2026-11-11"}, last_entry=700, checked="2026-11-12")
    assert c == {"last_entry": 812, "checked": "2026-11-12"}
    assert D.advance(None, published_after=None) == {}


def test_dedupe_across_feeds_by_url_and_title():
    Doc = lambda u, t: SimpleNamespace(source_url=u, title=t)       # noqa: E731
    docs = [Doc("https://seniorhousingnews.com/a/?utm_source=rss", "Acme Buys Home"),
            Doc("https://seniorhousingnews.com/a/", "other title"),          # same URL
            Doc("https://skillednursingnews.com/b/", "ACME buys home!"),     # same story, other feed
            Doc("https://skillednursingnews.com/c/", "Different story")]
    out, dup = D.dedupe_new(docs, set(), set())
    assert dup == 2 and [d.title for d in out] == ["Acme Buys Home", "Different story"]


class FakeConn:
    def execute(self, sql, params=None):
        return SimpleNamespace(fetchone=lambda: {"ok": False}, fetchall=lambda: [])


def test_dry_run_store_never_advances_current_cursor(tmp_path, monkeypatch):
    monkeypatch.setattr(D, "DRYRUN_DIR", tmp_path)
    s = D.Store(FakeConn(), dry_run=True)
    assert s.load_cursors() == ({}, "none (first run)")
    cur = {"shn_rss|feed": {"cursor": {"published_after": "2026-11-10T00:00:00+00:00"}}}
    s.save(cur, {"shn_rss|feed": {"cursor": {"published_after": "2026-11-12T00:00:00+00:00"}}}, {"run_id": "r"})
    saved = json.loads((tmp_path / "cursors.json").read_text())
    assert saved["current"] == cur and "proposed_not_applied" in saved
    assert s.load_cursors()[0] == cur           # the next dry run starts from the same place


def test_live_store_refuses_without_tables():
    with pytest.raises(SystemExit):
        D.Store(FakeConn(), dry_run=False).save({}, {}, {"run_id": "r"})


# ── gates ──
def test_lead_count_gate():
    assert D.lead_count_ok(485, 533)[0] and not D.lead_count_ok(485, 534)[0]          # 485 x 1.10 = 533.5
    assert D.lead_count_ok(485, 437)[0] and not D.lead_count_ok(485, 436)[0]
    assert D.lead_count_ok(None, 10)[0]


def test_source_link_gate():
    good = {"items": [{"lead_id": "a", "sources": [URL]}]}
    assert D.links_ok(good)[0]
    assert not D.links_ok({"items": [{"lead_id": "a", "sources": []}]})[0]
    assert not D.links_ok({"items": [{"lead_id": "a", "sources": [URL],
                                      "sale_status": {"status": "closed", "source_url": None}}]})[0]


def test_leak_gate_reports_term_numbers_never_terms(tmp_path):
    terms = tmp_path / "terms.txt"
    terms.write_text("# comment\nzebra holdings\n!zebra holdings park\nquokka\n")
    ok, detail = D.leak_scan({"items": [{"property": "Quokka Manor", "city": "X"}]}, str(terms))
    assert not ok and "term #2" in detail and "quokka" not in detail.lower()
    assert D.leak_scan({"items": [{"property": "Zebra Holdings Park"}]}, str(terms))[0]   # allowed phrase
    assert not D.leak_scan({"items": []}, None)[0]                                       # no list: blocks


def test_stage_change_and_top20_diff():
    prev = {"lead_count": 2, "stages": {"a": "sale", "b": "early"}, "top20": ["a"]}
    cand = {"lead_count": 2, "stages": {"a": "done:approved", "c": "early"}, "top20": ["a", "c"]}
    d = D.diff_states(prev, cand)
    assert d["new"] == ["c"] and d["dropped"] == ["b"] and d["stage_changed"] == ["a"] and d["top20_entrants"] == ["c"]


def test_source_classification():
    assert source_of(URL) == "courtlistener"
    assert source_of("https://dm.epiq11.com/case/IHC/info") == "epiq"
    assert source_of("https://data.cms.gov/data-api/v1/dataset/f557a6ed-95b3-4a22-8433-4175db2dec1c/data") == "cms_chow"
    assert source_of("https://www.mychesco.com/a/news") == "web:mychesco.com"
    assert source_of("licensed:provider") is None


# ── roundup rule in the snapshot build (opt-in) and the reconciliation ──
def test_snapshot_roundup_rule_is_opt_in_and_reconciles():
    from src.web import snapshot as S
    rows = [{"buyer": "Acme LLC", "buyer_status": "named", "flags": [],
             "buyer_source_url": "https://skillednursingnews.com/2026/10/skilled-nursing-dealbook-two-systems"},
            {"buyer": "Solo LLC", "buyer_status": "named", "flags": [], "buyer_source_url": URL}]
    conn = SimpleNamespace(execute=lambda sql, p=None: SimpleNamespace(fetchall=lambda: []))
    assert S.roundup_unverified(conn, rows) == 1
    assert rows[0]["buyer_status"] == "Unverified" and "buyer_from_roundup" in rows[0]["flags"]
    assert rows[1]["buyer_status"] == "named"
    assert S.other_reason({"buyer": "Acme LLC", "buyer_status": "Unverified", "flags": [], "stage": "sale"}) == "unverified_buyer"
    m = S.summary([{"rank_a": 1}, {"rank_b": 1}, {"buyer": "Acme LLC", "buyer_status": "Unverified", "flags": []}], [], 0)
    assert m["list_a"] + m["list_b"] + m["other"] == 3 and m["other_breakdown"]["unverified_buyer"] == 1


def test_dry_run_patch_moves_unverified_out_of_list_a():
    snap = {"items": [{"lead_id": "L1", "rank": 1, "rank_a": 1, "lists": ["A"], "buyer": "Acme LLC",
                       "buyer_status": "named", "flags": [], "sources": [URL], "stage": "done"}],
            "list_c": [], "summary": {"list_a": 1, "list_b": 0, "other": 0, "list_c_linked_leads": 0}}
    out = D.apply_changes(snap, [], {"L1": URL + "#deal-2"})
    assert out["items"][0]["lists"] == [] and out["summary"]["list_a"] == 0 and out["summary"]["other"] == 1


def test_buyer_change_in_top20_is_tracked():
    prev = {"lead_count": 1, "stages": {"a": "done:approved"}, "buyers": {"a": "Acme LLC|named"}, "top20": ["a"]}
    cand = {"lead_count": 1, "stages": {"a": "done:approved"}, "buyers": {"a": "Acme LLC|Unverified"}, "top20": ["a"]}
    d = D.diff_states(prev, cand)
    assert d["buyer_changed"] == ["a"] and d["top20_buyer_changed"] == ["a"] and d["stage_changed"] == []


def test_rate_limit_queues_the_rest_and_the_run_continues(monkeypatch):
    from src.common.http import RateLimited
    from src.discover.courts import courtlistener as clmod

    class Limited:
        requests_made, max_requests = 0, 100
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def get(self, *a, **k): raise RateLimited("429, wait 2675s")

    targets = [{"lead_id": f"L{i}", "facility_id": f"F{i}", "property": f"Home {i}", "stage": "sale", "flags": [],
                "score": 10 - i, "buyer": None, "buyer_status": "NOT FOUND", "dockets": {str(100 + i): (None, URL)},
                "epiq": [], "manual": [], "stretto": []} for i in range(3)]
    monkeypatch.setattr(D, "follow_targets", lambda conn: targets)
    monkeypatch.setattr(D, "override_map", lambda conn: {})
    monkeypatch.setattr(clmod, "make_client", lambda **k: Limited())
    b = D.Budget(ledger=clmod.CallLedger("daily3"))                         # no caps: the 429 is what stops it
    changes, human, stats = D.follow_through(None, b, {}, {}, log=lambda *a: None)
    assert changes == [] and stats["dockets_read"] == 0 and "RateLimited" in stats["stopped"]
    assert sorted(q["key"] for q in b.queued) == ["100", "101", "102"]          # nothing lost, all queued
    new, st = D.discover_courtlistener(None, b, {}, {}, log=lambda *a: None)     # search waits too
    assert new == [] and "stopped earlier" in st["skipped"]



# ── CourtListener call log and rolling caps ──
def _ledger(history_minutes_ago, hour_cap=45, day_cap=100):
    from datetime import timedelta
    from src.discover.courts.courtlistener import CallLedger
    now = datetime(2026, 11, 13, 1, 0, tzinfo=timezone.utc)
    return CallLedger("daily3", "r1", [now - timedelta(minutes=m) for m in history_minutes_ago], hour_cap, day_cap,
                      now=lambda: now)


def test_rolling_hour_cap_counts_earlier_calls():
    led = _ledger([10] * 40 + [90] * 30)                       # 40 in the last hour, 70 in 24 h
    assert led.used(1) == 40 and led.used(24) == 70 and led.remaining() == 5
    led = _ledger([10] * 45)
    from src.discover.courts.courtlistener import RequestCapReached
    with pytest.raises(RequestCapReached, match="rolling-hour cap 45"):
        led.check()


def test_rolling_day_cap_counts_earlier_calls():
    led = _ledger([120] * 99)                                  # outside the hour, inside 24 h
    assert led.remaining() == 1
    from src.discover.courts.courtlistener import RequestCapReached
    led.history.append(led.history[0])
    with pytest.raises(RequestCapReached, match="rolling-24h cap 100"):
        led.check()


def test_every_http_attempt_is_logged_and_capped_before_sending(tmp_path, monkeypatch):
    import httpx
    from src.discover.courts import courtlistener as clmod
    monkeypatch.setattr(clmod, "env", lambda n: "secret-token")
    sent = []

    def handler(req):
        sent.append(req)
        return httpx.Response(200, json={"results": [], "next": None})
    led = _ledger([5] * 43)                                    # 2 calls left in the rolling hour
    c = clmod.make_client(transport=httpx.MockTransport(handler), ledger=led, cache_dir=tmp_path,
                          sleep=lambda s: None, min_interval=0)
    for d in ("1", "2"):
        c.get(f"{clmod.API}/docket-entries/", params={"docket": d}, use_cache=False)
    with pytest.raises(clmod.RequestCapReached):
        c.get(f"{clmod.API}/docket-entries/", params={"docket": "3"}, use_cache=False)
    assert len(sent) == 2 and len(led.calls) == 2                # the third was never sent
    assert [x["status"] for x in led.calls] == [200, 200]
    assert led.calls[0]["endpoint"] == "/api/rest/v4/docket-entries/" and "docket=1" in led.calls[0]["query"]
    assert "secret-token" not in json.dumps(led.rows())          # headers are never logged


def test_retries_are_logged_as_separate_calls(tmp_path, monkeypatch):
    import httpx
    from src.discover.courts import courtlistener as clmod
    monkeypatch.setattr(clmod, "env", lambda n: "t")
    replies = iter([httpx.Response(429, headers={"retry-after": "1"}), httpx.Response(200, json={})])
    led = _ledger([])
    c = clmod.make_client(transport=httpx.MockTransport(lambda r: next(replies)), ledger=led, cache_dir=tmp_path,
                          sleep=lambda s: None, min_interval=0)
    c.get(f"{clmod.API}/search/", use_cache=False)
    assert [x["status"] for x in led.calls] == [429, 200]


def test_dry_run_keeps_its_call_log_locally(tmp_path, monkeypatch):
    monkeypatch.setattr(D, "DRYRUN_DIR", tmp_path)
    s = D.Store(FakeConn(), dry_run=True)
    calls = [{"at": datetime.now(timezone.utc).isoformat(), "job": "daily3", "endpoint": "/x", "status": 200}]
    s.save({}, {}, {"run_id": "r"}, calls)
    s.save({}, {}, {"run_id": "r2"}, calls)
    assert len(json.loads((tmp_path / "courtlistener_calls.json").read_text())) == 2   # appended, not replaced


def test_courtlistener_client_waits_5_seconds_between_calls(tmp_path, monkeypatch):
    import httpx
    from src.discover.courts import courtlistener as clmod
    monkeypatch.setattr(clmod, "env", lambda n: "t")
    t = {"now": 0.0}
    waits = []

    def sleep(s):
        waits.append(s)
        t["now"] += s
    led = _ledger([])
    c = clmod.make_client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})), ledger=led,
                          cache_dir=tmp_path, clock=lambda: t["now"], sleep=sleep)       # default spacing
    assert c.min_interval == clmod.MIN_INTERVAL == 5.0
    for _ in range(3):
        c.get(f"{clmod.API}/search/", use_cache=False)
    assert waits == [5.0, 5.0] and len(led.calls) == 3          # first call immediate, then 5 s apart
    assert led.used(1) == 3                                      # rolling caps still count every call


# ── top-20 hold: only a material change holds the run; a HOLD exits 0, a real error exits 1 ──
def _state(order, scores):
    return {"lead_count": len(scores), "top20": list(order), "scores": dict(scores),
            "stages": {k: "early" for k in scores}, "buyers": {k: "NOT FOUND|NOT FOUND" for k in scores}}


def _top(n=19):
    return {f"t{i}": 100.0 - i for i in range(n)}


def test_rescoring_swap_at_the_top20_line_passes_and_is_counted():
    # the Nov 13 case: #20 loses 0.05 points to daily date rescoring and trades places with a lead just below it
    old = dict(_top(), a=40.04, b=40.00)
    new = dict({k: v - 0.05 for k, v in _top().items()}, a=39.99, b=40.00)
    prev, cand = _state(list(_top()) + ["a"], old), _state(list(_top()) + ["b"], new)
    d = D.diff_states(prev, cand)
    g = D.top20_gate(prev, cand, D.real_change_ids(d, []), 1.0)
    assert g["pass"] and not g["hold"]
    assert g["counts"]["rescoring_only_swaps"] == 2 and g["counts"]["entered_or_left"] == 2
    assert g["counts"]["entered_or_left_real"] == 0 and g["counts"]["score_moves_over_limit"] == 0
    assert "rescoring only" in g["detail"] and "40" not in g["detail"]            # counts only, never a score
    gates = {"tests": {"pass": True, "detail": ""}, "top20_order": g}
    assert not D.is_hold(gates) and D.exit_code(False, False, True, "published", gates) == 0


def test_new_top20_lead_with_a_change_row_holds_and_exits_0():
    old = dict(_top(), a=40.04)
    new = dict(_top(), a=40.04, n=80.0)                      # n is new on the list and lands in the top 20
    prev = _state(list(_top()) + ["a"], old)
    cand = _state(list(_top())[:5] + ["n"] + list(_top())[5:], new)
    d = D.diff_states(prev, cand)
    assert d["new"] == ["n"] and d["top20_entrants"] == ["n"]
    g = D.top20_gate(prev, cand, D.real_change_ids(d, []), 1.0)
    assert not g["pass"] and g["hold"] and g["counts"]["entered_or_left_real"] == 1
    gates = {"tests": {"pass": True, "detail": ""}, "top20_order": g}
    assert D.is_hold(gates)
    assert D.exit_code(False, False, True, "blocked", gates) == 0       # live, held: success, the issue is the notice
    assert D.exit_code(True, True, False, "blocked", gates) == 0        # strict dry run, held


def test_stage_change_that_moves_a_lead_into_the_top20_holds():
    old = dict(_top(), a=40.04, b=40.00)
    new = dict(_top(), a=40.04, b=40.20)
    prev, cand = _state(list(_top()) + ["a"], old), _state(list(_top()) + ["b"], new)
    cand["stages"]["b"] = "done:approved"
    g = D.top20_gate(prev, cand, D.real_change_ids(D.diff_states(prev, cand), []), 1.0)
    assert g["hold"] and g["counts"]["entered_or_left_real"] == 1 and g["counts"]["score_moves_over_limit"] == 0


def test_score_move_of_1_2_points_holds_even_when_the_order_is_the_same():
    old = dict(_top(), a=40.04)
    new = dict(old, t3=old["t3"] + 1.2)                       # same order, one score jumps
    order = list(_top()) + ["a"]
    prev, cand = _state(order, old), _state(order, new)
    g = D.top20_gate(prev, cand, set(), 1.0)
    assert not g["pass"] and g["hold"] and g["counts"]["score_moves_over_limit"] == 1 and g["counts"]["rank_moves"] == 0
    assert D.top20_gate(prev, _state(order, dict(old, t3=old["t3"] + 0.99)), set(), 1.0)["pass"]
    assert D.top20_gate(prev, cand, set(), 2.0)["pass"]       # the threshold comes from config


def test_hold_threshold_is_read_from_config():
    from src.common.config import thresholds
    assert D.hold_score_move() == float(thresholds()["daily_check"]["top20_hold_score_move"]) == 1.0


def test_order_change_against_a_list_stored_without_scores_is_held():
    prev = {"lead_count": 2, "top20": ["a", "b"], "stages": {}, "buyers": {}}            # run log from before scores
    assert D.top20_gate(prev, _state(["a", "b"], {"a": 2.0, "b": 1.0}), set(), 1.0)["pass"]
    g = D.top20_gate(prev, _state(["b", "a"], {"a": 1.0, "b": 2.0}), set(), 1.0)
    assert g["hold"] and "no scores" in g["detail"]


def test_a_failed_gate_is_never_a_hold():
    hold = {"pass": False, "hold": True, "detail": "HOLD"}
    gates = {"leak_scan": {"pass": False, "detail": "term #2"}, "top20_order": hold}
    assert not D.is_hold(gates) and D.exit_code(False, False, True, "blocked", gates) == 1
    ok = {"top20_order": {"pass": True, "hold": False, "detail": "unchanged"}}
    assert D.exit_code(False, False, True, "failed", ok) == 1                             # the upload itself failed
    assert D.exit_code(True, False, False, "blocked", gates) == 0 and D.exit_code(True, True, False, "blocked", gates) == 1


def test_a_crash_exits_1(monkeypatch, capsys):
    def boom(argv=None):
        raise RuntimeError("database went away")
    monkeypatch.setattr(D, "main", boom)
    assert D.cli(["--live", "--publish"]) == 1
    assert "RuntimeError" in capsys.readouterr().err


def test_snapshot_state_keeps_scores_and_exclusions_drop_them():
    from src.pipeline import changelog as cl
    snap = {"items": [{"lead_id": "a", "rank": 1, "score": 9.5, "stage": "early"},
                      {"lead_id": "b", "rank": 2, "score": None, "stage": "early"}]}
    st = D.snapshot_state(snap)
    assert st["scores"] == {"a": 9.5} and st["top20"] == ["a", "b"]
    assert cl.without(st, {"a"})["scores"] == {}


def test_workflow_files_a_counts_only_issue_on_hold():
    from src.common.config import PROJECT_ROOT
    y = (PROJECT_ROOT / ".github" / "workflows" / "daily3.yml").read_text()
    assert "issues: write" in y and 'title="daily3 hold $(TZ=America/New_York date +%F)"' in y
    assert "gh issue edit" in y and "gh issue create" in y and "grep -q '^Hold: YES'" in y
    assert "(PASS|FAIL|HOLD)" in y
