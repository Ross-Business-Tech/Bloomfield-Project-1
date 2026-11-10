from src.common.normalize import (
    classify_match, match_score, normalize_name, normalize_phone, normalize_street, street_number, zip5,
)


def test_name_drops_legal_suffixes_and_expands():
    assert normalize_name("Maple Ridge Rehab Ctr, LLC") == "maple ridge rehabilitation center"
    assert normalize_name("Cedar Hollow OpCo, Inc.") == "cedar hollow"
    assert normalize_name(None) == ""


def test_street_expands_and_drops_suites():
    assert normalize_street("123 N. Main St., Suite 200") == "123 north main street"
    assert normalize_street("45 Oak Ave #3") == "45 oak avenue"
    assert street_number("123A Main St") == "123a"
    assert street_number("Main St") is None


def test_zip_and_phone():
    assert zip5("48104-1234") == "48104"
    assert zip5(2134) == "02134"   # leading zero restored
    assert zip5("") is None
    assert normalize_phone("+1 (734) 555-0100") == "7345550100"
    assert normalize_phone("555-0100") is None


def test_renamed_facility_same_address_still_matches():
    old = {"name": "Maple Ridge Senior Living", "street": "100 Elm Street", "phone": "734-555-0100"}
    new = {"name": "Lakeshore Care of Grand Rapids", "street": "100 Elm St", "phone": "(734) 555-0100"}
    assert classify_match(match_score(old, new)) in ("auto", "needs_review")


def test_same_name_different_building_does_not_auto_match():
    a = {"name": "Brookdale Ann Arbor", "street": "100 Elm Street"}
    b = {"name": "Brookdale Ann Arbor", "street": "2400 Washtenaw Ave"}
    assert classify_match(match_score(a, b)) == "new"


def test_identical_records_auto_match():
    a = {"name": "Willow Bend Memory Care", "street": "5 Willow Bend Rd"}
    assert classify_match(match_score(a, dict(a))) == "auto"
