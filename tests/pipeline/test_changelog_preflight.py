"""Daily check: pre-flight (no publish without the exclusion list, the term list and the Render key) and the change
log (public rows only; a lead that left the list is a count, never a name). Made-up data."""
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.pipeline import changelog as cl
from src.pipeline import daily3, preflight
from src.web import publish, snapshot

WHEN = datetime(2026, 11, 13, 2, 5, tzinfo=timezone.utc)          # 9:05 PM ET on Nov 12
SALE = {"status": "closed", "date": "2026-10-14", "source_url": "https://news.example/portfolio", "portfolio": True}


# ── pre-flight ──────────────────────────────────────────────────────────────────────────────────────────────────
@pytest.fixture()
def laptop_files(tmp_path, monkeypatch):
    """Point the laptop fallbacks at an empty folder, so only the given env counts."""
    monkeypatch.setattr(preflight, "EXCLUDE_FILE", tmp_path / "exclude.txt")
    monkeypatch.setattr(preflight, "TERMS_FILE", tmp_path / "terms.txt")
    monkeypatch.setattr(preflight, "ENV_FILE", tmp_path / ".env")
    monkeypatch.setattr(preflight, "LABELS_FILE", tmp_path / "labels.csv")
    for k in ("EXCLUDE_FACILITIES", "EXCLUDE_FACILITIES_PATH", "PRIVATE_TERMS_PATH", "RENDER_API_KEY", "FAMILIES_CLASSIFICATION_CSV"):
        monkeypatch.delenv(k, raising=False)
    return tmp_path


def full_env(tmp_path) -> dict:
    (tmp_path / "t.txt").write_text("# private\nzzterm\n!allowed phrase\n")
    (tmp_path / "l.csv").write_text("stg_court_case_id,senior_housing\n1,yes\n2,no\n")
    return {"EXCLUDE_FACILITIES": "id-1\nid-2", "PRIVATE_TERMS_PATH": str(tmp_path / "t.txt"), "RENDER_API_KEY": "k" * 12,
            "FAMILIES_CLASSIFICATION_CSV": str(tmp_path / "l.csv")}


def test_preflight_passes_only_when_all_three_inputs_are_present(laptop_files):
    env = full_env(laptop_files)
    assert preflight.problems(env) == []
    assert [r["name"] for r in preflight.check({}) if not r["pass"]] == ["case_labels", "exclusion_list", "private_terms", "render_key"]
    for missing in ("EXCLUDE_FACILITIES", "PRIVATE_TERMS_PATH", "RENDER_API_KEY", "FAMILIES_CLASSIFICATION_CSV"):
        assert len(preflight.problems({k: v for k, v in env.items() if k != missing})) == 1
    assert len(preflight.problems(dict(env, EXCLUDE_FACILITIES="  \n"))) == 1            # present but empty
    assert len(preflight.problems(dict(env, RENDER_API_KEY=" "))) == 1


def test_preflight_never_shows_a_value(laptop_files):
    env = full_env(laptop_files)
    text = str(preflight.check(env))
    assert "id-1" not in text and "zzterm" not in text and "k" * 12 not in text


def test_exclusion_list_formats(laptop_files):
    assert preflight.exclusion_ids({"EXCLUDE_FACILITIES": "a\n# old, list\nb, c\n"})[0] == ["a", "b", "c"]
    (laptop_files / "exclude.txt").write_text("# Excluded homes (private, gitignored)\nf-1\nf-2\n")
    assert preflight.exclusion_ids({}) == (["f-1", "f-2"], "file")
    assert snapshot.excluded() == {"f-1", "f-2"}                   # the snapshot builder reads the same list
    (laptop_files / "exclude.txt").write_text("# only a comment\n")
    assert preflight.exclusion_ids({})[0] == []


def test_a_run_without_the_exclusion_list_cannot_publish(laptop_files, monkeypatch, capsys):
    env = full_env(laptop_files)
    monkeypatch.setenv("PRIVATE_TERMS_PATH", env["PRIVATE_TERMS_PATH"])
    monkeypatch.setenv("RENDER_API_KEY", env["RENDER_API_KEY"])              # everything is there EXCEPT the list
    monkeypatch.setenv("FAMILIES_CLASSIFICATION_CSV", env["FAMILIES_CLASSIFICATION_CSV"])
    calls = []
    monkeypatch.setattr(publish, "main", lambda argv=None: calls.append(("publish", argv)) or 0)
    monkeypatch.setattr(publish, "build", lambda *a, **k: calls.append(("build", a)) or (None, {}))
    import src.common.db as db
    monkeypatch.setattr(db, "connect", lambda *a, **k: calls.append(("connect",)) or (_ for _ in ()).throw(AssertionError("no work may start")))
    assert daily3.main(["--live", "--publish"]) == 2
    out = capsys.readouterr().out
    assert "FAIL  exclusion_list" in out and "Publish: NO (blocked by pre-flight" in out
    assert calls == []                                             # no database work, no build, no upload
    (laptop_files / "exclude.txt").write_text("\n")                # an empty file is the same as no file
    assert daily3.main(["--live", "--publish"]) == 2 and calls == []


def test_publish_command_refuses_to_upload_without_the_exclusion_list(laptop_files, monkeypatch, capsys):
    env = full_env(laptop_files)
    monkeypatch.setenv("PRIVATE_TERMS_PATH", env["PRIVATE_TERMS_PATH"])
    monkeypatch.setenv("RENDER_API_KEY", env["RENDER_API_KEY"])
    monkeypatch.setenv("FAMILIES_CLASSIFICATION_CSV", env["FAMILIES_CLASSIFICATION_CSV"])
    built = []
    monkeypatch.setattr(publish, "build", lambda *a, **k: built.append(1) or (None, {}))
    monkeypatch.setattr(publish, "client", lambda key: (_ for _ in ()).throw(AssertionError("no upload")))
    assert publish.main(["--confirm"]) == 2
    assert built == [] and "PRE-FLIGHT FAILED" in capsys.readouterr().out


def test_gate_list_carries_the_preflight_result(laptop_files, monkeypatch):
    snap = {"items": [], "counts": {"leads": 0}, "funnel": [{"step": "Shown on Deal Radar", "count": 0}]}
    g = daily3.gates(snap, None, run_tests_too=False)
    assert g["preflight"]["pass"] is False and "exclusion_list" in g["preflight"]["detail"]
    assert g["leak_scan"]["pass"] is False                         # no term list: the leak check cannot pass either


# ── change log ──────────────────────────────────────────────────────────────────────────────────────────────────
def lead(i, **kw):
    base = {"lead_id": f"l{i}", "property": f"Home {i}", "city": "X", "state": "PA", "stage": "early", "sale_status": None,
            "buyer": None, "buyer_status": "NOT FOUND", "buyer_group": "none", "sources": [f"https://a.example/{i}"], "flags": []}
    return dict(base, **kw)


BEFORE = {"items": [lead(1, stage="done", sale_status=SALE, buyer="Portfolio Holdings LLC", buyer_status="named"),
                    lead(2, stage="sale"), lead(3), lead(4, property="Left The List Home")]}
AFTER = {"items": [lead(1, stage="done", sale_status=SALE, buyer="Portfolio Holdings LLC", buyer_status="inferred_portfolio",
                        buyer_group="inferred_portfolio"),
                   lead(2, stage="done", sale_status={"status": "approved", "date": "2026-11-12", "source_url": "https://court.example/order"},
                        buyer="Court Buyer LLC", buyer_status="named", buyer_group="confirmed"),
                   lead(3), lead(5)]}


def test_rows_carry_property_change_reason_source_and_date():
    rows, removed = cl.rows_between(cl.state_of(BEFORE), AFTER, "2026-11-12", 1)
    by = {(r["id"], r["reason"]): r for r in rows}
    r = by[("l1", "buyer_inferred_portfolio")]
    assert r["change"] == "Buyer status: Named → Inferred · portfolio sale" and r["src"] == SALE["source_url"]
    assert (r["p"], r["d"], r["no"]) == ("Home 1", "2026-11-12", 1)
    assert by[("l2", "stage_sale_approved")]["change"] == "Stage: In a sale → Sale approved"
    assert by[("l2", "stage_sale_approved")]["src"] == "https://court.example/order"
    assert by[("l2", "buyer_named")]["change"] == "Buyer: NOT FOUND → Court Buyer LLC"
    assert by[("l5", "new_lead")]["src"] == "https://a.example/5"
    assert "l3" not in {r["id"] for r in rows}                     # nothing changed: no row
    assert set(r["reason"] for r in rows) <= set(cl.REASONS)


def test_a_removed_lead_is_counted_but_never_named():
    rows, removed = cl.rows_between(cl.state_of(BEFORE), AFTER, "2026-11-12", 1)
    assert removed == 1
    assert "Left The List Home" not in str(rows) and "l4" not in {r["id"] for r in rows}
    # a lead that changed in an earlier check and has since left the list loses its earlier rows too
    old = cl.run_entry(1, WHEN - timedelta(days=1), "published", [], 0, {}, {}, 4)
    old_rows = [{"d": "2026-11-11", "no": 1, "id": "l4", "p": "Left The List Home", "c": "X", "s": "PA",
                 "change": "Stage: Early distress → In a sale", "reason": "stage_changed", "src": "https://a.example/4"}]
    entry = cl.run_entry(2, WHEN, "published", rows, removed, {"tests": {"pass": True, "detail": "ok"}}, {"new_articles": 3}, 4)
    block = cl.build([dict(old, rows=old_rows)], entry, rows, AFTER, WHEN)
    assert "Left The List Home" not in str(block) and "l4" not in str(block)
    assert [r["no"] for r in block["runs"]] == [2, 1] and block["runs"][0]["removed"] == 1


def test_a_confidential_exclusion_is_removed_from_the_baseline_too():
    """The past-deal home (l4) was on the hand-over list and is on the exclusion list today: it must not show as a
    change, must not be counted as removed, and the baseline lead count is the list without it."""
    baseline = cl.state_of(BEFORE)
    assert cl.rows_between(baseline, AFTER, "2026-11-12", 1)[1] == 1                 # without the rule: counted
    clean = cl.without(dict(baseline, top20=["l1", "l4"]), {"l4"})
    rows, removed = cl.rows_between(clean, AFTER, "2026-11-12", 1)
    assert removed == 0 and clean["lead_count"] == 3 and clean["top20"] == ["l1"]
    assert "l4" not in str(clean) and "l4" not in str(rows) and "Left The List Home" not in str(rows)
    entry = cl.run_entry(1, WHEN, "passed", rows, removed, {}, {}, 4)
    block = cl.build([], entry, rows, AFTER, WHEN, baseline_leads=clean["lead_count"])
    assert block["runs"][0]["removed"] == 0
    assert block["baseline"]["leads"] == 3 and block["leads_delta"] == 4 - 3
    assert cl.without(baseline, set()) is baseline and cl.without(None, {"x"}) is None
    assert baseline["lead_count"] == 4                                                # the input is not edited


def test_header_tiles_and_check_number():
    rows, removed = cl.rows_between(cl.state_of(BEFORE), AFTER, "2026-11-12", 1)
    entry = cl.run_entry(cl.next_number([]), WHEN, "dry_run", rows, removed, {"leak_scan": {"pass": True, "detail": "clean"}},
                         {"dockets_read": 4, "new_articles": 2, "ignored": {"x": 1}}, 4)
    block = cl.build([], entry, rows, AFTER, WHEN)
    assert block["updated_et"] == "Nov 12, 2026, 9:05 PM ET" and block["check_no"] == 1
    assert block["leads"] == 4 and block["leads_delta"] == 4 - cl.BASELINE["leads"] and block["baseline"]["label"] == "Nov 11, 2026"
    assert (block["buyers_named"], block["buyers_inferred"]) == (1, 1)          # two counts, never added together
    run = block["runs"][0]
    assert (run["stages_changed"], run["buyers_changed"], run["new"], run["removed"]) == (1, 2, 1, 1)
    assert run["gates"] == [{"name": "leak_scan", "pass": True, "detail": "clean"}]
    assert run["checked"] == {"dockets_read": 4, "new_articles": 2}
    assert "buyer_inferred_portfolio" in block["reasons"]
    assert cl.next_number([{"no": 3}, {"no": 7}]) == 8


def test_history_keeps_thirty_days_and_dry_runs_do_not_grow_it():
    old = dict(cl.run_entry(1, WHEN - timedelta(days=45), "published", [], 0, {}, {}, 4), rows=[])
    recent = dict(cl.run_entry(2, WHEN - timedelta(days=3), "published", [], 0, {}, {}, 4), rows=[])
    entry = cl.run_entry(3, WHEN, "published", [], 0, {}, {}, 4)
    assert [e["no"] for e in cl.history_after([old, recent], entry, [], WHEN)] == [2, 3]
    assert [r["no"] for r in cl.build([old, recent], entry, [], AFTER, WHEN)["runs"]] == [3, 2]


def test_changelog_is_uploaded_only_when_present_and_stays_small():
    assert "changelog" in snapshot.UPLOAD_KEYS
    import base64, gzip, json
    items = [lead(i, stage="done", sale_status=SALE, buyer="Portfolio Holdings LLC", buyer_status="inferred_portfolio",
                  buyer_group="inferred_portfolio") for i in range(500)]
    prev = {"items": [dict(x, buyer_status="named") for x in items]}
    rows, _ = cl.rows_between(cl.state_of(prev), {"items": items}, "2026-11-12", 1)
    block = cl.build([], cl.run_entry(1, WHEN, "published", rows, 0, {}, {}, 500), rows, {"items": items}, WHEN)
    without = snapshot.packed({"items": items})
    with_log = snapshot.packed({"items": items, "changelog": block})
    assert "changelog" not in json.loads(gzip.decompress(base64.b64decode(without)))
    assert len(json.loads(gzip.decompress(base64.b64decode(with_log)))["changelog"]["rows"]) == 500
    assert len(with_log) - len(without) < 40_000 and daily3.SNAPSHOT_MAX_BYTES == 800 * 1024


def test_page_and_workflow_wiring():
    static = Path(snapshot.__file__).parent / "static"
    js, html = (static / "app.js").read_text(), (static / "index.html").read_text()
    assert '"Updated " + c.updated_et + " · Daily check #" + c.check_no' in js
    assert '"#/changes": "changes"' in js and 'id="view-changes"' in html and '"Checks run"' in js
    assert 'if (!c) return;' in js                                  # no change log in the snapshot: the site is unchanged
    css = (static / "app.css").read_text()
    assert ".asof.has-log { display: block;" in css and '$("asof").classList.add("has-log")' in js   # phone top bar
    assert ".stage > span:last-child { min-width: 0; overflow-wrap: anywhere; }" in css                # long stage text wraps
    assert "white-space: nowrap" not in css.split(".stage {")[1].split("}")[0]
    wf = (Path(snapshot.__file__).parents[2] / ".github" / "workflows" / "daily3.yml").read_text()
    live = "\n".join(ln for ln in wf.splitlines() if not ln.strip().startswith("#"))
    assert "workflow_dispatch" in live and "EXCLUDE_FACILITIES_GZ_B64" in live and "src.pipeline.preflight" in live
    assert '    - cron: "0 11 * * *"' in live and "ref: development" in live   # daily at 6 am Eastern (winter)
    # public-repo log rule: nothing but the checked summary is printed, and no data artifact is uploaded
    assert "upload-artifact" not in wf and "src.pipeline.log_check" in live
    assert 'python -m src.pipeline.daily3 $args > "$LOG" 2>&1' in live and 'cat "$LOG"' not in live
    assert "--live --publish --publish-changelog" in live and "--dry-run --strict" in live


# ── public-repo log rule and cloud inputs ───────────────────────────────────────────────────────────────────────
def test_log_check_refuses_names_addresses_terms_and_ids():
    from src.pipeline import log_check
    names = log_check.names_from({"items": [{"property": "Oak Manor Nursing Home", "buyer": "Acme Holdings LLC"}, {"property": "Elm"}],
                                  "explore": [{"property": "Birch Court Assisted Living", "buyer": None}], "list_c": [{"buyer": "Repeat Buyer Group"}]})
    assert names == {"oak manor nursing home", "acme holdings llc", "birch court assisted living", "repeat buyer group"}
    ok = "   PASS  leak_scan      clean\nChange log: Daily check #2; leads 484 (+0 vs Nov 11, 2026); buyers 31 named, 176 inferred\nPublish: NO (dry_run)"
    assert not any(log_check.problems(ok, names, ["zzterm"], ["id-1"]).values())
    assert log_check.problems("finding  OAK MANOR NURSING HOME sale order", names, [], [])["names"] == 1
    assert log_check.problems("buyer=Acme Holdings LLC", names, [], [])["names"] == 1
    assert log_check.problems("x zzTerm y", names, ["zzterm"], [])["terms"] == 1
    assert log_check.problems("lead id-1 dropped", names, [], ["id-1"])["ids"] == 1
    assert log_check.problems("at 120 North Main Street, Springvale", names, [], [])["addresses"] == 1


def test_log_check_command_withholds_and_never_repeats_the_match(tmp_path, laptop_files, capsys):
    from src.pipeline import log_check
    snap = tmp_path / "s.json"
    snap.write_text('{"items": [{"property": "Oak Manor Nursing Home", "buyer": null}]}')
    good, bad = tmp_path / "good.txt", tmp_path / "bad.txt"
    good.write_text("   PASS  tests          362 passed\nPublish: NO (dry_run)\n")
    bad.write_text("   new docket for Oak Manor Nursing Home\n")
    assert log_check.main([str(good), "--snapshot", str(snap)]) == 0
    assert log_check.main([str(bad), "--snapshot", str(snap)]) == 3
    out = capsys.readouterr().out
    assert "WITHHELD" in out and "oak manor" not in out.lower()


def test_cloud_inputs_round_trip_without_printing_values(tmp_path, monkeypatch):
    from src.pipeline import cloud_inputs as ci
    monkeypatch.setattr(ci, "OUTPUT_DIR", tmp_path / "output")
    full = tmp_path / "watch.csv"
    full.write_text("property,city,state,flags,source_urls,score,secret_note\n"
                    "Oak Home,X,PA,{},https://a.example/1,10,internal only\n"
                    "Private Home,Y,NY,{licensed_private},https://a.example/2,9,internal only\n")
    slim = ci.slim_watch(full).decode()
    assert "Oak Home" in slim and "Private Home" not in slim and "internal only" not in slim     # public rows, site columns
    blob = ci.pack(slim.encode())
    env = {"EXCLUDE_FACILITIES_GZ_B64": ci.pack(b"# list\nf-1\n"), "PRIVATE_TERMS_GZ_B64": ci.pack(b"zzterm\n"),
           "CASE_LABELS_GZ_B64": ci.pack(b"stg_court_case_id,senior_housing\n1,yes\n"),
           "WATCHLIST_GZ_B64_1": blob[:20], "WATCHLIST_GZ_B64_2": blob[20:], "WATCHLIST_GZ_B64_3": ci.UNUSED, "WATCHLIST_GZ_B64_4": "-",
           "WATCHLIST_NAME": "INTERNAL_watch_2026-11-11.csv"}
    paths = ci.restore(tmp_path / "priv", env)
    assert set(paths) == {"EXCLUDE_FACILITIES_PATH", "PRIVATE_TERMS_PATH", "FAMILIES_CLASSIFICATION_CSV", "WATCHLIST_FILE"}
    assert (tmp_path / "output" / "INTERNAL_watch_2026-11-11.csv").read_text() == slim
    assert preflight.exclusion_ids({"EXCLUDE_FACILITIES_PATH": paths["EXCLUDE_FACILITIES_PATH"]})[0] == ["f-1"]
    assert preflight.case_labels({"FAMILIES_CLASSIFICATION_CSV": paths["FAMILIES_CLASSIFICATION_CSV"]})[0] == 1
    with pytest.raises(SystemExit):
        ci.restore(tmp_path / "priv", dict(env, WATCHLIST_NAME="../../etc/x.csv"))
    assert ci.restore(tmp_path / "none", {}) == {}                 # nothing set: nothing written; pre-flight then fails
