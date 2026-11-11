from src.web import publish


def test_city_names_are_masked_only_in_their_own_row():
    snap = {"items": [{"property": "Oak Home of Springvale", "city": "Springvale", "buyer": "Springvale OpCo"}],
            "explore": [{"property": "Elm House", "city": "Elm", "buyer": "Springvale Partners"}],
            "rules": "Springvale"}
    m = publish.mask_place_names(snap)
    assert "springvale" not in (m["items"][0]["property"] + m["items"][0]["city"] + m["items"][0]["buyer"]).lower()
    assert m["explore"][0]["buyer"] == "Springvale Partners"     # another row's city is not masked
    assert m["rules"] == "Springvale" and snap["items"][0]["city"] == "Springvale"   # other text untouched, no mutation


def test_licensed_tag_and_client_name_fail(monkeypatch):
    monkeypatch.setattr(publish, "private_terms", lambda: (["zzprivatezz"], []))
    assert publish.leak_check('{"a": "licensed:x"}') == ["'licensed:' tag (1x)"]
    assert publish.leak_check('{"a": "zzprivatezz"}') == ["term #1 (1x)"]
    assert publish.leak_check('{"a": "' + publish.CLIENT_TERM.title() + ' deal"}') == ["client name (1x)"]


def test_list_c_example_city_slot_is_masked():
    m = publish.mask_place_names({"list_c": [{"buyer": "Springvale Group", "example_homes": "Oak (SPRINGVALE, MO); Elm (X, NOT FOUND)"}]})
    assert m["list_c"][0]["example_homes"] == "Oak (, MO); Elm (, NOT FOUND)"
    assert m["list_c"][0]["buyer"] == "Springvale Group"           # a buyer name is never masked


def test_funnel_last_stage_equals_public_leads_in_the_packed_upload():
    import base64, gzip, json
    from src.web import snapshot
    rows = [{"lead_id": f"l{i}", "facility_id": f"f{i}", "property": f"Home {i}", "city": "X", "state": "TX",
             "care_types": ["AL"], "buyer": "NOT FOUND" if i % 2 else "Acme LLC", "buyer_status": "NOT FOUND" if i % 2 else "named",
             "stage": "late", "score": 100 - i, "flags": [], "source_urls": ["https://a.example/1"], "score_breakdown": {}}
            for i in range(7)]
    snap = snapshot.build(rows, {"f6"})
    snap["explore"], snap["counts"]["explore"] = [], 0
    snap["funnel"] = [{"step": "Shown on Deal Radar", "count": len(snap["items"])}]
    snap["summary"] = snapshot.summary(snap["items"], [], 0)
    unpacked = json.loads(gzip.decompress(base64.b64decode(snapshot.packed(snap))))
    assert snapshot.funnel_problems(unpacked) == [] and unpacked["funnel"][-1]["count"] == 6
    unpacked["funnel"][-1]["count"] = 400
    assert snapshot.funnel_problems(unpacked) == ["funnel 'Shown on Deal Radar' = 400, public leads = 6"]
