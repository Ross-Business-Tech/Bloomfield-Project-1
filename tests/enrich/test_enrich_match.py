"""Pure-function tests for the core-builder matcher (no database)."""
import csv
import uuid

import pytest

from src.enrich import steps
from src.enrich.match import (
    FacilityIndex, care_types_from_text, clean, court_source_id, court_state, name_only_score,
    normalize_court, normalize_docket, pair_score, state_code, to_int,
)
from src.enrich.orgs import OrgResolver


def fac(name, street=None, city="Ann Arbor", state="MI", zip5="48104", fid=None):
    return {"facility_id": fid or str(uuid.uuid4()), "name": name, "street": street, "city": city,
            "state": state, "zip5": zip5}


def test_clean_and_state():
    assert clean("NOT FOUND") is None and clean("  ") is None and clean(["x"]) == "x"
    assert state_code("Colo.") == "CO" and state_code("Pa.") == "PA" and state_code("Calif.") == "CA"
    assert state_code("N.Y.") == "NY" and state_code("W.Va.") == "WV"
    assert state_code("Michigan") == "MI" and state_code("mi") == "MI" and state_code("Narnia") is None


def test_same_building_renamed_still_matches_on_address():
    idx = FacilityIndex()
    a = fac("Sunrise of Ann Arbor", "2200 Washtenaw Ave")
    idx.add(a)
    res = idx.best({"name": "Brookdale Ann Arbor", "street": "2200 Washtenaw Avenue", "city": "Ann Arbor",
                    "state": "MI", "zip5": "48104"})
    assert res.facility_id == a["facility_id"]
    assert res.status in ("auto", "needs_review")


def test_exact_name_and_address_is_auto():
    idx = FacilityIndex()
    a = fac("Pine View Care Center", "100 Main St")
    idx.add(a)
    res = idx.best({"name": "Pine View Care Ctr LLC", "street": "100 Main Street", "city": "Ann Arbor",
                    "state": "MI", "zip5": "48104"})
    assert res.status == "auto" and res.facility_id == a["facility_id"] and res.score >= 90


def test_different_street_number_is_new():
    idx = FacilityIndex()
    idx.add(fac("Pine View Care Center", "100 Main St"))
    res = idx.best({"name": "Oak Grove Manor", "street": "900 Main St", "state": "MI", "zip5": "48104"})
    assert res.status == "new" and res.facility_id is None


def test_blocking_other_state_never_matches():
    idx = FacilityIndex()
    idx.add(fac("Pine View Care Center", "100 Main St"))
    res = idx.best({"name": "Pine View Care Center", "street": "100 Main St", "state": "OH", "zip5": "48104"})
    assert res.status == "new" and res.candidates == 0


def test_city_fallback_when_zip_missing():
    idx = FacilityIndex()
    a = fac("Pine View Care Center", "100 Main St")
    idx.add(a)
    res = idx.best({"name": "Pine View Care Center", "street": "100 Main St", "city": "ANN ARBOR", "state": "MI"})
    assert res.facility_id == a["facility_id"]


def test_name_only_when_no_street():
    # HUD files carry no street: name-only score inside the zip block
    assert pair_score({"name": "PINEVIEW EXTENDED CARE CENTER"}, {"name": "Pineview Extended Care Center", "street": "1 A St"}) == 100
    generic = name_only_score({"name": "Nursing Center"}, {"name": "Saint Mary Mercy Nursing and Rehabilitation Center"})
    assert generic < 75


def test_court_and_docket_normalization():
    assert normalize_court("Bankr. N.D. Tex.") == "txnb"
    assert normalize_court("Bankr. D. Del.") == "deb"
    assert normalize_court("Bankr. S.D.N.Y.") == "nysb"
    assert normalize_court("Bankr. D.N.J.") == "njb"
    assert normalize_court("Northern District of Texas") == "txnb"
    assert normalize_court("District of Delaware") == "deb"
    assert normalize_court("txsb") == "txsb"
    assert normalize_court(None) == "UNKNOWN"
    assert court_state("txnb") == "TX" and court_state("UNKNOWN") is None
    assert normalize_docket("8:26-bk-10925") == "26-10925"
    assert normalize_docket("07-40290(CEC)") == "07-40290"
    assert normalize_docket("24-31596") == "24-31596"
    assert court_source_id("https://elevenflo.com/cases/x") == "elevenflo"
    assert court_source_id("https://dm.epiq11.com/case/CHI/info") == "epiq"


def test_care_types_and_ints():
    assert care_types_from_text("232/ 223(f)/Pur/Refin/ Assisted Living") == ["AL"]
    assert care_types_from_text("232 NURSING HOMES") == ["SNF"]
    assert care_types_from_text("Home for the Aged (AH)") == ["AL"]
    assert care_types_from_text("NOT FOUND") == []
    assert care_types_from_text(["assisted_living", "memory_care"]) == ["AL", "MC"]
    assert to_int("120 units") == 120 and to_int("NOT FOUND") is None and to_int(1,) == 1


def test_org_resolver_merges_on_associate_id_then_name():
    o = OrgResolver()
    a = o.get("Acme Health LLC", "123", "operator_entity")
    assert o.get("ACME HEALTH, L.L.C.", "123") == a              # same associate id
    b = o.get("Acme Health Inc", None, "lender")                  # no id -> same normalized name
    assert b == a
    c = o.get("Acme Health", "999")                                # same name, different associate id
    assert c != a
    d = o.get("Beta Care", None, "licensee")
    assert o.get("Beta Care LLC", "555") == d                     # name-only org gains the associate id
    assert o.rows[d]["cms_associate_id"] == "555"
    assert o.get("", None) is None and o.get(None, None) is None
    ch = o.get_chain("42", "Gamma Chain")
    assert o.get_chain("42", "anything") == ch
    assert o.created == 4


class _FakeConn:
    pass


def test_resolve_reuses_existing_xref_and_creates_new_facility():
    idx = FacilityIndex()
    batch = steps.Batch(_FakeConn())
    from collections import Counter
    stats = Counter()
    existing = {("hud_active", "1"): {"facility_id": "f1", "review_status": "needs_review"},
                ("hud_active", "2"): {"facility_id": "f2", "review_status": "rejected"}}
    assert steps.resolve(batch, idx, existing, "hud_active", "1", {}, stats) == ("f1", "needs_review")
    assert steps.resolve(batch, idx, existing, "hud_active", "2", {}, stats) == (None, None)
    rec = {"name": "New Place", "city": "Lansing", "state": "MI", "zip5": "48910"}
    fid, status = steps.resolve(batch, idx, existing, "hud_active", "3", rec, stats,
                                new_kwargs=dict(name="New Place", city="Lansing", state="MI", zip_="48910"))
    assert status == "auto" and len(batch.facilities) == 1 and batch.xrefs[0][4] is None
    # a second record for the same building now matches the facility created a moment ago
    fid2, status2 = steps.resolve(batch, idx, existing, "hud_terminated", "9", rec, stats)
    assert fid2 == fid and status2 == "auto"
    assert steps.resolve(batch, idx, {}, "hud_terminated", "10",
                         {"name": "Elsewhere", "state": "MI", "zip5": "49999"}, stats, allow_new=False) == (None, None)


def test_fields_list_shapes():
    assert steps._fields_list({"property_name": "A"}) == [{"property_name": "A"}]
    assert steps._fields_list([{"a": 1}, "x"]) == [{"a": 1}]
    assert steps._fields_list({"status": "skipped_prefilter", "reason": "x"}) == []
    main = {"status": "extracted", "property_name": "A", "buyer_or_new_operator": "Buyer Co",
            "other_properties": [{"property_name": "B", "state": "Pa."}]}
    out = steps._fields_list(main)
    assert [f["property_name"] for f in out] == ["A", "B"] and out[1]["buyer_or_new_operator"] == "Buyer Co"
    assert steps._fields_list(None) == []


def test_generic_words_do_not_make_a_match():
    a = {"name": "COLD SPRING HILLS CENTER FOR NURSING & REHAB"}
    b = {"name": "WHITE OAKS REHABILITATION AND NURSING CENTER"}
    assert name_only_score(a, b) < 75
    assert name_only_score({"name": "EVERGREEN COMMONS"},
                           {"name": "EVERGREEN COMMONS REHABILITATION AND NURSING CTR"}) >= 90
    assert name_only_score({"name": "Health Care Inc"}, {"name": "Blue Clouds Health Care Incorporated"}) == 0


class _Rows:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return self.rows


class _CourtConn:
    def execute(self, *a, **k):
        return _Rows([])


def test_unknown_court_rows_fold_into_known_case():
    rows = [
        {"id": 1, "court": "Bankr. E.D. Cal.", "docket_no": "25-26876", "debtor_name": "Oroville Hospital", "case_name": None},
        {"id": 2, "court": "UNKNOWN", "docket_no": "25-26876", "debtor_name": "Oroville Hospital", "case_name": None},
        {"id": 3, "court": "UNKNOWN", "docket_no": "09-40901", "debtor_name": "Caritas Health Care", "case_name": None},
    ]
    keys = steps._case_keys(_CourtConn(), rows)
    assert keys[1] == keys[2] == ("caeb", "25-26876")
    assert keys[3] == ("UNKNOWN", "09-40901")


# ---------------------------------------------------------------- Gate 2: HUD rules + duplicate merge
from src.enrich.match import (
    care_compatible, hud_best, hud_care_classes, hud_name_variants, qualifying_prefixes, split_borrower_prefix,
)

HUD_CFG = {"strip_borrower_prefix": True, "auto_requires_exact_zip": True, "require_care_type_match": True}


def test_borrower_prefix_split_and_qualification():
    assert split_borrower_prefix("NEXCARE- HOLT SENIOR CARE") == ("NEXCARE", "HOLT SENIOR CARE")
    assert split_borrower_prefix("NO DASH HERE") is None
    assert split_borrower_prefix("TRAILING-") is None
    names = ["NEXCARE- HOLT", "NEXCARE-MASON", "TLMGT30-OAK VIEW", "WILKES-BARRE CARE"]
    q = qualifying_prefixes(names)
    assert set(q) == {"NEXCARE", "TLMGT30"}        # >= 2 loans, or a digit; WILKES appears once -> not a code
    assert hud_name_variants("NEXCARE- HOLT SENIOR CARE", q) == (["NEXCARE- HOLT SENIOR CARE", "HOLT SENIOR CARE"], "NEXCARE")
    assert hud_name_variants("WILKES-BARRE CARE", q) == (["WILKES-BARRE CARE"], None)   # legit hyphen untouched


def test_prefix_stripping_lifts_the_score():
    idx = FacilityIndex()
    a = {**fac("Holt Senior Care and Rehab Center", "5091 Willoughby Rd", city="Holt", zip5="48842"),
         "care_types": ["SNF"]}
    idx.add(a)
    rec = {"name": "NEXCARE- HOLT SENIOR CARE & REHAB CENTER", "city": "HOLT", "state": "MI", "zip5": "48842"}
    names, _ = hud_name_variants(rec["name"], {"NEXCARE": 5})
    with_strip = hud_best(idx, rec, names, {"SNF"}, HUD_CFG)
    without = hud_best(idx, rec, [rec["name"]], {"SNF"}, HUD_CFG)
    assert with_strip.score >= without.score and with_strip.status == "auto"


def test_hud_auto_requires_exact_zip():
    idx = FacilityIndex()
    a = {**fac("Pineview Extended Care Center", "1 Main St", city="Clinton", state="MD", zip5="20743"),
         "care_types": ["SNF"]}
    idx.add(a)
    rec = {"name": "PINEVIEW EXTENDED CARE CENTER", "city": "CLINTON", "state": "MD", "zip5": "20735"}
    res = hud_best(idx, rec, [rec["name"]], {"SNF"}, HUD_CFG)
    assert res.facility_id == a["facility_id"] and res.score >= 90 and res.status == "needs_review"
    rec["zip5"] = "20743"
    assert hud_best(idx, rec, [rec["name"]], {"SNF"}, HUD_CFG).status == "auto"


def test_hud_care_type_compatibility():
    assert hud_care_classes("232/ 223(f)/Pur/Refin/ Nursing Hms") == {"SNF"}
    assert hud_care_classes("232 Assisted Living") == {"AL"}
    assert hud_care_classes("232 Board and Care") == {"BC"}
    assert hud_care_classes("223(A)(7)REFIN OF 223F NURSING/ASSISTED") == {"SNF", "AL"}
    assert hud_care_classes("223(a)(7)/241(a)/232 Refi/Improvements +") == set()
    assert not care_compatible({"AL"}, ["SNF"])
    assert not care_compatible({"SNF"}, ["AL"])
    assert care_compatible({"SNF", "AL"}, ["SNF"]) and care_compatible({"SNF", "AL"}, ["AL"])   # mixed = either
    assert care_compatible({"BC"}, ["AL"]) and not care_compatible({"BC"}, ["SNF"])
    assert care_compatible(set(), ["SNF"]) and care_compatible({"AL"}, [])
    idx = FacilityIndex()
    idx.add({**fac("Sunny Acres", "1 Main St"), "care_types": ["SNF"]})
    res = hud_best(idx, {"name": "SUNNY ACRES", "city": "Ann Arbor", "state": "MI", "zip5": "48104"}, ["SUNNY ACRES"],
                   {"AL"}, HUD_CFG)
    assert res.status == "new" and res.facility_id is None       # incompatible pair cannot match at all


def test_plan_merges():
    facs = [
        {"facility_id": "c1", "name": "FORT TRYON CENTER FOR REHABILITATION AND NURSING", "street": "801W 190TH ST",
         "zip5": "10040", "is_enrollment": True},
        {"facility_id": "c2", "name": "WESTLAKE HEALTH CAMPUS", "street": "10735 BOGIE LAKE RD", "zip5": "48386",
         "is_enrollment": True},
        {"facility_id": "x1", "name": "FORT TRYON CENTER FOR REHAB NURSING", "street": "801 WEST 190TH ST",
         "zip5": "10040", "is_enrollment": False},
        {"facility_id": "x2", "name": "Brookside Manor", "street": "10735A Bogie Lake Road", "zip5": "48386",
         "is_enrollment": False},
        {"facility_id": "x3", "name": "Elsewhere", "street": "10735 Bogie Lake Road", "zip5": "48999",
         "is_enrollment": False},
        {"facility_id": "x4", "name": "No street", "street": None, "zip5": "10040", "is_enrollment": False},
    ]
    plan = {m["extra"]: m for m in steps.plan_merges(facs)}
    assert set(plan) == {"x1", "x2"}
    assert plan["x1"]["survivor"] == "c1" and plan["x2"]["survivor"] == "c2"
    assert plan["x2"]["name_ratio"] < 60 <= plan["x1"]["name_ratio"]


# ---------------------------------------------------------------- Gate 2 follow-up: directions, borrower groups
from src.enrich.match import borrower_group_verdict, direction_conflict, directions


def test_direction_words():
    assert directions("AUTUMN HOUSE WEST") == {"W"}
    assert directions("Northeast Manor") == {"NE"}
    assert directions("Pine Lodge N") == {"N"}
    assert directions("E.A. HAWSE HEALTHCARE CENTER") == set()          # initials are not directions
    assert directions("ST. ANN'S COMMUNITY") == set()                   # apostrophe-s is not South
    assert direction_conflict("AUTUMN HOUSE WEST", "AUTUMN HOUSE EAST")
    assert direction_conflict("Pine Lodge N", "Pine Lodge South")
    assert not direction_conflict("AUTUMN HOUSE WEST", "Autumn House")  # only one side -> no change
    assert not direction_conflict("North Shore", "NORTH SHORE CARE")


def test_direction_conflict_blocks_every_matcher():
    idx = FacilityIndex()
    east = {**fac("AUTUMN HOUSE EAST", None, city="York", state="PA", zip5="17402"), "care_types": ["AL"]}
    idx.add(east)
    rec = {"name": "AUTUMN HOUSE WEST", "city": "YORK", "state": "PA", "zip5": "17402"}
    assert idx.best(rec).facility_id is None                           # licenses / news path
    assert hud_best(idx, rec, [rec["name"]], {"AL"}, HUD_CFG).facility_id is None
    rec["name"] = "AUTUMN HOUSE"
    assert hud_best(idx, rec, [rec["name"]], {"AL"}, HUD_CFG).facility_id == east["facility_id"]
    names = ["AUTUMN HOUSE EAST"]
    assert steps._court_candidates([steps.distinctive_name(n) for n in names], names, "Autumn House West LLC") == []


def test_borrower_group_verdicts():
    assert borrower_group_verdict("SENIOR LIVING", {"TX"}) == "drop_generic"
    assert borrower_group_verdict("CENTER MANAGEMENT", {"NY", "NJ"}) == "drop_generic"
    assert borrower_group_verdict("MID", {"IL", "NY"}) == "drop_generic"
    assert borrower_group_verdict("REGENCY", {"TX"}) == "drop_ambiguous"
    assert borrower_group_verdict("REGENCY29", {"TX"}) == "keep"           # digit
    assert borrower_group_verdict("INDEPENDENCE", {"PA", "TX"}) == "keep"  # 2+ states
    for brand in ("FORMATION", "DYNASTY", "TRILOGY", "EXTENDICARE16", "PRUITTHEALTH", "RECHNITZ"):
        assert borrower_group_verdict(brand, {"CA"}) == "keep"
