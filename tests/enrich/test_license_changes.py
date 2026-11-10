from datetime import date

from src.enrich.steps import license_changes, same_party


def snap(month, name, field="licensee"):
    return {"as_of": date(2024, month, 1), field: name}


def test_spelling_variants_are_same_party():
    assert same_party("4K HOUSING, INC", "4-K HOUSING INC")
    assert same_party("PP ENTERPRISES, LLC", "P P ENTERPRISES LLC")
    assert same_party("HP VICTORIA II OPCO LLC", "HP VICTORIA OPCO II, LLC")
    assert same_party("BROOKDALE SENIOR LIVING COMMUNITIES INC", "BROOKDALE SENIOR LIVING INC PUBLICLY TRADED")


def test_real_changes_and_placeholders():
    assert not same_party("GREENVILLE OPCO LLC", "CSSL 3 TX LLC")
    assert not same_party("DARSEA I, LLC", "DARSEA HOLDINGS, LLC")
    assert same_party("N/A", "LIFE CARE SERVICES")          # placeholder never counts as a change


def test_change_window_and_blip():
    snaps = [snap(1, "A Senior Co"), snap(2, "B Holdings"), snap(3, "A Senior Co"), snap(4, "C Partners"),
             snap(5, "C Partners LLC")]
    pairs = license_changes(snaps, "licensee")
    assert [(a["as_of"].month, b["as_of"].month) for a, b in pairs] == [(3, 4)]   # 1-snapshot blip B dropped


def test_empty_values_skipped():
    snaps = [snap(1, "Alpha Care LLC"), snap(2, None), snap(3, "Beta Living LLC")]
    pairs = license_changes(snaps, "licensee")
    assert [(a["as_of"].month, b["as_of"].month) for a, b in pairs] == [(1, 3)]
