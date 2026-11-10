"""Family grouping rules. All names are made up."""
from datetime import date

from src.discover.courts import families as fm

TODAY = date(2026, 11, 10)


def row(id, court, docket, name, filed=None, url=None, agent=None, cl=None):
    return {"id": id, "court": court, "docket_no": docket, "case_name": name, "debtor_name": None,
            "filed_date": filed, "source_url": url or f"https://www.courtlistener.com/docket/{id}/x/",
            "claims_agent_url": agent, "courtlistener_id": cl if cl is not None else str(1000 + id)}


def by_member(fams):
    return {c.id: f for f in fams for c in f.members}


def test_court_codes_and_dockets():
    assert fm.court_code("txnb") == "txnb"
    assert fm.court_code("Northern District of Texas") == "txnb"
    assert fm.court_code("Bankr. N.D. Tex.") == "txnb"
    assert fm.court_code("Bankr. D.N.J.") == "njb"
    assert fm.court_code("Bankr. E.D.N.Y.") == "nyeb"
    assert fm.court_code("District of Delaware") == "deb"
    assert fm.court_code("Bankr. D. Neb.") == "nebraskab"
    assert fm.court_code("UNKNOWN") is None
    assert fm.court_state("nebraskab") == "NE" and fm.court_state("cacb") == "CA"
    assert fm.norm_docket("2:26-bk-10221") == "26-10221" == fm.norm_docket("26-10221")
    assert fm.joint_lead('Acme Pharmacy, LLC <b><font color="red">Jointly Administered under 25-34698.</font></b>') == "25-34698"
    assert fm.clean_name('Acme LLC <b><font color="red">Jointly Administered under 25-34698.</font></b>') == "Acme LLC"


def test_keyword_label():
    assert fm.keyword_label("Maplewood Assisted Living, LLC") == "yes"
    assert fm.keyword_label("Garden Hospital and Nursing Home") == "yes"      # yes words win
    assert fm.keyword_label("Riverbend Regional Hospital") == "no"
    assert fm.keyword_label("Bluebird Burgers LLC") == "no"                   # no healthcare/housing hint at all
    assert fm.keyword_label("Coastal Health Care, LLC") == "maybe"            # unsure stays maybe


def test_jointly_administered_text_groups_siblings():
    rows = [row(1, "txsb", "4:25-bk-34698", "Zephyr Care Holdings, LLC", date(2025, 8, 13)),
            row(2, "txsb", "25-34700", 'Zephyr Care West, LLC <b>Jointly Administered under 25-34698.</b>', date(2025, 8, 13)),
            row(3, "txsb", "25-34701", 'Quill Senior Living <b>Jointly Administered under 25-34698.</b>', date(2025, 8, 20))]
    fams = fm.build_families(rows, {1: "yes", 2: "yes", 3: "yes"}, today=TODAY)
    assert len(fams) == 1
    f = fams[0]
    assert f.grouping_method == "jointly_administered_text" and f.lead_docket_no == "25-34698"
    assert f.family_name == "Zephyr Care Holdings, LLC"


def test_same_docket_across_sources_is_lead_case_and_claims_agent_names_lead():
    rows = [row(1, "UNKNOWN", "26-90004", "Lumen Senior Living Holdings, LLC", None,
                url="https://dm.epiq11.com/case/LSL/info", agent="https://dm.epiq11.com/case/LSL/info", cl=""),
            row(2, "txnb", "3:26-bk-90004", "Lumen Senior Living Holdings, LLC", date(2026, 2, 2)),
            row(3, "Bankr. N.D. Tex.", "26-90004", "Lumen Senior Living Holdings, LLC", date(2026, 2, 3),
                url="https://elevenflo.com/cases/lumen-1", cl="")]
    fams = fm.build_families(rows, {2: "yes"}, today=TODAY)
    assert len(fams) == 1
    f = fams[0]
    assert f.grouping_method == "lead_case" and f.lead_docket_no == "26-90004"
    assert f.filed_date == date(2026, 2, 2)                 # CourtListener date preferred
    assert f.claims_agent_url == "https://dm.epiq11.com/case/LSL/info"
    assert f.within_36m is True


def test_unknown_court_needs_shared_name_word():
    rows = [row(1, "UNKNOWN", "23-00813", "Orchid Valley Nursing, Inc.", None, url="https://dm.epiq11.com/case/A/info", cl=""),
            row(2, "ilnb", "23-00813", "Totally Different Assisted Living", date(2023, 1, 5))]
    assert len(fm.build_families(rows, {2: "yes"}, today=TODAY)) == 2


def test_two_day_rule_fallback_and_limits():
    rows = [row(1, "cob", "25-17658", "Aspenridge Assisted Living LLC", date(2025, 11, 21)),
            row(2, "cob", "25-17660", "Birchwood Memory Care LLC", date(2025, 11, 22)),   # yes+yes within 2 days
            row(3, "cob", "25-17818", "Cedarline Assisted Living LLC", date(2025, 11, 26)),  # 4 days later
            row(4, "deb", "25-17661", "Dunmore Memory Care LLC", date(2025, 11, 21)),     # other court
            row(5, "cob", "25-17659", "Elmhurst Health Partners", date(2025, 11, 21))]    # maybe, no shared word
    fams = by_member(fm.build_families(rows, {1: "yes", 2: "yes", 3: "yes", 4: "yes", 5: "maybe"}, today=TODAY))
    assert fams[1] is fams[2] and fams[1].grouping_method == "two_day_rule"
    assert fams[3] is not fams[1] and fams[4] is not fams[1] and fams[5] is not fams[1]


def test_two_day_rule_maybe_joins_on_shared_word():
    rows = [row(1, "paeb", "26-14631", "Hollyoak Health", date(2026, 11, 1)),
            row(2, "paeb", "26-14640", "Hollyoak Skilled Nursing Care", date(2026, 11, 1))]
    fams = fm.build_families(rows, {1: "maybe", 2: "yes"}, today=TODAY)
    assert len(fams) == 1 and fams[0].senior_housing == "yes"


def test_two_day_rule_never_merges_two_jointly_administered_groups():
    rows = [row(1, "txsb", "25-100", "Alpha Senior Living <b>Jointly Administered under 25-00100.</b>", date(2025, 1, 1)),
            row(2, "txsb", "25-200", "Beta Senior Living <b>Jointly Administered under 25-00200.</b>", date(2025, 1, 1))]
    assert len(fm.build_families(rows, {1: "yes", 2: "yes"}, today=TODAY)) == 2


def test_same_debtor_name_joins_later_docket_and_notes_date_conflict():
    rows = [row(1, "txeb", "24-40605", "Pinecrest Healthcare of Hollow, LP", date(2024, 3, 20)),
            row(2, "txeb", "24-04021", "Pinecrest Healthcare of Hollow, LP", date(2024, 4, 18)),
            row(3, "cacb", "2:26-bk-10221", "Kindly Hands For Seniors, Inc.", date(2026, 1, 11)),
            row(4, "Bankr. C.D. Cal.", "26-10221", "Kindly Hands For Seniors, Inc.", date(2026, 2, 2),
                url="https://elevenflo.com/cases/kindly-1", cl="")]
    fams = by_member(fm.build_families(rows, {1: "yes", 2: "yes", 3: "yes"}, today=TODAY))
    assert fams[1] is fams[2] and fams[1].grouping_method == "same_debtor_name"
    assert fams[3] is fams[4] and fams[3].filed_date == date(2026, 1, 11)
    assert any("disagree" in n for n in fams[3].notes)


def test_hand_label_beats_keyword_and_no_families_dropped():
    rows = [row(1, "deb", "24-10561", "Sunbeam Uniform Brands, LLC", date(2024, 1, 22)),
            row(2, "Bankr. D. Del.", "24-10561", "Sunbeam Uniform Brands Care, LLC", date(2024, 1, 22),
                url="https://elevenflo.com/cases/s-1", cl="")]
    assert fm.build_families(rows, {1: "no"}, today=TODAY) == []


def test_within_window_uses_docket_year_when_undated():
    assert fm.within_window(None, "18-10467", TODAY) is False
    assert fm.within_window(None, "25-10467", TODAY) is True
    assert fm.within_window(None, "23-10467", TODAY) is None        # cutoff year itself: unknown
    assert fm.within_window(date(2023, 11, 9), None, TODAY) is False


def test_assign_ids_reuses_existing_and_new_cases_join():
    rows = [row(1, "cob", "25-1", "Aspenridge Assisted Living", date(2025, 11, 21)),
            row(2, "cob", "25-2", "Birchwood Assisted Living", date(2025, 11, 21)),
            row(3, "cob", "25-3", "Cedarline Assisted Living", date(2025, 11, 22))]   # new case this run
    fams = fm.build_families(rows, {1: "yes", 2: "yes", 3: "yes"}, today=TODAY)
    released = fm.assign_ids(fams, {1: 7, 2: 7})
    assert len(fams) == 1 and fams[0].family_id == 7 and released == set()
    # re-run: same answer (idempotent)
    fams2 = fm.build_families(rows, {1: "yes", 2: "yes", 3: "yes"}, today=TODAY)
    fm.assign_ids(fams2, {1: 7, 2: 7, 3: 7})
    assert fams2[0].family_id == 7


def test_hand_labels_read_from_file(tmp_path):
    p = tmp_path / "labels.csv"
    p.write_text("stg_court_case_id,name,senior_housing\n5,Made Up Manor,yes\n6,Made Up Widgets,no\n7,x,bogus\n")
    assert fm.load_hand_labels(str(p)) == {5: "yes", 6: "no"}
    assert fm.load_hand_labels(str(tmp_path / "missing.csv")) == {}
