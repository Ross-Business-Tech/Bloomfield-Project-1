from src.common.normalize import city_alias


def test_alias_applies_per_state_and_keeps_case():
    assert city_alias("Orion", "MI") == "Lake Orion"
    assert city_alias("ORION", "mi") == "LAKE ORION"
    assert city_alias("Orion", "IL") == "Orion"          # other state: unchanged
    assert city_alias(None, "MI") is None and city_alias("Flint", "MI") == "Flint"
