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
