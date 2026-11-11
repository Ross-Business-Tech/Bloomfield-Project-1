"""Wave 3 rules and scoring (no database)."""
from datetime import date

import pytest

from src.score import rules
from src.score.buyers import Buyer, pick
from src.score.leads import score_all, score_facility
from src.score.rules import Sig
from src.score.signals import BuyerHint

TODAY = date(2026, 11, 10)
FAC = {"facility_id": "f1", "city": "Detroit", "state": "MI", "units": None, "beds": 120}


def sig(t, stage=None, fid="f1", key=None, **kw):
    return Sig(fid, t, stage or rules.stage_of(t), key or f"{t}:{fid}:{kw.get('family_id')}", "https://x.org/a", **kw)


def test_metric_thresholds_come_from_config():
    assert rules.is_low_occupancy(0.65) and not rules.is_low_occupancy(0.75)
    assert not rules.is_low_occupancy(3.2)          # >100% occupancy = data error
    assert rules.is_negative_noi(-1) and not rules.is_negative_noi(0)


def test_staffing_drop_needs_history():
    assert rules.census_drop([(date(2026, 6, 30), 80)]) is None
    s = [(date(2025, 6, 30), 100), (date(2025, 9, 30), 95), (date(2025, 12, 31), 90), (date(2026, 3, 31), 85),
         (date(2026, 6, 30), 80)]
    assert rules.is_staffing_drop(s)


def test_operator_turnover_two_changes_within_36_months():
    assert rules.operator_changes_qualify([date(2025, 1, 1), date(2026, 5, 1)], TODAY)
    assert not rules.operator_changes_qualify([date(2020, 1, 1), date(2026, 5, 1)], TODAY)


def test_news_market_sale_vs_distressed_sale():
    assert rules.news_signal_types(["sale"]) == [("market_sale", "sale")]
    assert rules.news_signal_types(["sale", "operator_change"]) == [("market_sale", "sale")]
    assert rules.news_signal_types(["chapter_11", "sale"]) == [("chapter_11", "sale")]
    assert rules.news_signal_types(["receivership"]) == [("receivership", "late")]
    assert rules.news_signal_types(["loan_or_lease_default"]) == [("loan_or_lease_default", "middle")]
    assert rules.news_signal_types("NOT FOUND") == []
    assert rules.news_signal_types(["operator_change"]) == []


@pytest.mark.parametrize("text,excluded", [
    ("Redeemer Health is in Chapter 11 pursuing a sale of its hospital and physician network", True),
    ("proposed sale of substantially all hospital assets to its lender Axios", True),
    ("court-authorized sale of Senior Care Pharmacy assets for $45,000", True),
    ("liquidation plan became effective after the sale of its nursing facility assets", False),
    ("The sale order Dkt. 1981 authorizes the transaction", False),
])
def test_hospital_and_pharmacy_sales_excluded(text, excluded):
    assert rules.is_non_senior_sale(text) is excluded


def test_buyer_status():
    assert rules.court_buyer_status("sale order") == "named"
    assert rules.court_buyer_status("stalking horse") == "candidate"
    assert rules.news_buyer_status("X has acquired Y") == "named"
    assert rules.news_buyer_status("X proposed to acquire Y") == "candidate"


def test_buyer_pick_prefers_named_and_confirmed():
    a = BuyerHint("f1", "A", "candidate", "https://a", "court_sale")
    b = BuyerHint("f1", "B", "named", "https://b", "chow", date(2025, 1, 1))
    c = BuyerHint("f1", "C", "named", "https://c", "news", date(2026, 1, 1), link_status="needs_review")
    assert pick([a, b, c]).name == "B"
    assert pick([]) is None


def test_recency_and_size():
    assert rules.recency_points(date(2026, 10, 1), TODAY) == 15
    assert rules.recency_points(date(2020, 1, 1), TODAY) == 0
    assert 0 < rules.recency_points(date(2026, 1, 1), TODAY) < 15
    assert rules.size_fit_points(None, 120) == (10.0, True)
    assert rules.size_fit_points(None, None) == (0.0, False)
    assert rules.chain_quality_points(5) == 10 and rules.chain_quality_points(1) == 0


def test_market_sale_alone_is_not_a_lead():
    assert score_facility(FAC, [sig("market_sale")], Buyer(), TODAY) is None


def test_old_event_is_context_only():
    assert score_facility(FAC, [sig("hud_loan_terminated", event_date=date(2001, 1, 1))], Buyer(), TODAY) is None


def test_breakdown_sums_and_flags():
    lead = score_facility(FAC, [sig("negative_noi", event_date=date(2024, 12, 31)), sig("market_sale")],
                          Buyer("named", "Buyer LLC", None, "https://b", "news"), TODAY)
    bd = lead.breakdown
    parts = sum(v for k, v in bd.items() if isinstance(v, (int, float)) and k != "total")
    assert bd["total"] == lead.score == pytest.approx(parts)
    assert bd["stage"] == 10 and bd["buyer"] == 20 and bd["market_sale"] == 5 and bd["size_fit"] == 10
    assert "buyer_chain_perf_missing" in lead.flags and lead.stage == "early"


def test_unconfirmed_when_distress_rests_on_needs_review_link():
    s = sig("chapter_11", event_date=date(2026, 5, 1), link_status="needs_review")
    lead = score_facility(FAC, [s], Buyer(), TODAY)
    assert "unconfirmed" in lead.flags and "buyer_not_found" in lead.flags
    ok = score_facility(FAC, [s, sig("receivership", event_date=date(2026, 5, 1))], Buyer(), TODAY)
    assert "unconfirmed" not in ok.flags


def test_court_sale_goes_to_top_and_debtor_org_counts_less():
    full = score_facility(FAC, [sig("court_sale_process", family_id=1, family_name="F")], Buyer(), TODAY)
    weak = score_facility(FAC, [sig("court_sale_process", family_id=1, family_name="F", weight=0.5,
                                    link_method="debtor_org", flags=["portfolio_link"])], Buyer(), TODAY)
    other = score_facility(FAC, [sig("receivership", event_date=TODAY)],
                           Buyer("named", "B", None, "https://b", "chow", chain={"avg_overall_rating": 5},
                                 prior_hud=True), TODAY)
    assert full.stage == "sale" and full.score > other.score
    assert weak.score < full.score and "portfolio_link" in weak.flags


def test_one_family_cannot_flood_the_top():
    facs, by_fac = {}, {}
    for i in range(10):
        fid = f"f{i}"
        facs[fid] = {**FAC, "facility_id": fid}
        by_fac[fid] = [sig("court_sale_process", fid=fid, family_id=16, family_name="Genesis"),
                       sig("chapter_11", fid=fid, family_id=16, family_name="Genesis", event_date=date(2025, 7, 9))]
    ranked = score_all(facs, by_fac, {}, TODAY)
    top_n = rules.wave3("family_top_n")
    assert sum("family_capped" not in l.flags for l in ranked) == top_n
    assert ranked[top_n - 1].score > ranked[top_n].score


def test_hospital_district_buyer_flagged():
    lead = score_facility(FAC, [sig("negative_noi")], Buyer("named", "NORTH BIG HORN HOSPITAL DISTRICT", None,
                                                             "https://b", "chow"), TODAY)
    assert "buyer_hospital_district" in lead.flags


# ── Gate 3 decisions: closure = late; lender takeback stays late ─────────────────────────────────────────────
def test_closure_is_late():
    from src.score.rules import news_signal_types
    assert news_signal_types(["closure"]) == [("closure", "late")]


def test_lender_takeback_is_late_not_sale():
    from src.score.rules import news_signal_types
    assert news_signal_types(["foreclosure", "sale"], "Example Bank", "Example Bank", "acquired") == [("foreclosure", "late")]
    assert news_signal_types(["foreclosure", "sale"], None, None, "sold at sheriff's sale") == [("foreclosure", "late")]
    # a foreclosure followed by a sale to a third party is still a sale
    assert news_signal_types(["foreclosure", "sale"], "Made Up Senior Living", "Example Bank", "sold to a new owner") \
        == [("foreclosure", "sale")]


def test_hospital_district_buyer_gets_no_points_and_weak_early_goes_to_watch():
    from datetime import date
    from src.score.leads import Buyer, score_facility
    from src.score.rules import Sig
    today = date(2026, 11, 10)
    fac = {"facility_id": "f1", "units": None, "beds": 120, "city": "Anytown", "state": "TX"}
    sig = Sig("f1", "sff_or_candidate", "early", "k1", "https://example.org/x", today, None, "test", "operating")
    hosp = Buyer(name="Example County Hospital District", status="named", source_url="https://example.org/b")
    lead = score_facility(fac, [sig], hosp, today)
    assert lead.breakdown["buyer"] == 0 and "buyer_hospital_district" in lead.flags
    assert lead.tier == "watch"     # one early signal < 3


# ── Gate 3 top-5 check: court links by name alone ────────────────────────────────────────────────────────────────
def test_court_link_by_name_only_is_capped_and_unconfirmed():
    cap = rules.court_name_only_cfg()["score_cap"]
    name_only = [sig("chapter_11", family_id=1, link_method="facility_name", flags=["court_name_only"]),
                 sig("court_sale_process", family_id=1, link_method="facility_name", flags=["court_name_only"])]
    lead = score_facility(FAC, name_only, Buyer(), TODAY)
    assert lead.score == cap
    assert {"court_name_only", "unconfirmed"} <= set(lead.flags)
    assert "court_name_only_cap" in lead.breakdown


def test_court_link_confirmed_by_cms_operator_is_not_capped():
    confirmed = [sig("chapter_11", family_id=1, link_method="facility_name", confirmed_by="cms_operator"),
                 sig("court_sale_process", family_id=1, link_method="facility_name", confirmed_by="cms_operator")]
    lead = score_facility(FAC, confirmed, Buyer(), TODAY)
    assert lead.score > rules.court_name_only_cfg()["score_cap"]
    assert "court_name_only" not in lead.flags and "unconfirmed" not in lead.flags


def test_name_only_court_lead_never_ranks_in_top_n(monkeypatch):
    real = rules.court_name_only_cfg
    monkeypatch.setattr(rules, "court_name_only_cfg", lambda: {**real(), "score_cap": 1000, "keep_out_of_top": 2})
    facs, by_fac = {}, {}
    for i in range(3):                                  # three confirmed late-stage leads
        fid = f"ok{i}"
        facs[fid] = {**FAC, "facility_id": fid}
        by_fac[fid] = [sig("chapter_11", fid=fid, family_id=10 + i, confirmed_by="cms_operator")]
    facs["n"] = {**FAC, "facility_id": "n"}            # one name-only court sale: would be #1 without the rule
    by_fac["n"] = [sig("court_sale_process", fid="n", family_id=99, flags=["court_name_only"])]
    ranked = score_all(facs, by_fac, {}, TODAY)
    assert [l.facility_id for l in ranked].index("n") >= 2
    assert "kept out of the top 2" in next(l for l in ranked if l.facility_id == "n").breakdown["court_name_only_cap"]


def test_court_facts_require_source_urls(tmp_path, monkeypatch):
    bad = tmp_path / "court_facts.yaml"
    bad.write_text("links:\n  - {case: 'x:1', ccn: '1'}\n")
    monkeypatch.setattr(rules, "_COURT_FACTS", bad)
    with pytest.raises(ValueError):
        rules.court_facts()


def test_family_homes_ordered_by_size_fit_then_severity_then_proof(monkeypatch):
    """Gate 3: homes in one bankruptcy are not ranked by score; size fit -> distress severity -> proof."""
    real = rules.wave3
    monkeypatch.setattr(rules, "wave3", lambda k: 1 if k == "family_top_n" else real(k))
    facs = {"big": {**FAC, "facility_id": "big", "beds": 900},          # outside the $3-30M size band
            "fit_weak": {**FAC, "facility_id": "fit_weak"},
            "fit_strong": {**FAC, "facility_id": "fit_strong"}}
    fam = lambda fid, conf: [sig("chapter_11", fid=fid, family_id=7, confirmed_by=conf)]   # noqa: E731
    by_fac = {"big": fam("big", "human") + [sig("negative_noi", fid="big"), sig("low_occupancy", fid="big")],
              "fit_weak": fam("fit_weak", "cms_ownership"),
              "fit_strong": fam("fit_strong", "cms_operator")}
    ranked = {l.facility_id: l for l in score_all(facs, by_fac, {}, TODAY)}
    order = sorted(ranked, key=lambda f: ranked[f].breakdown["deal"]["rank_in_family"])
    assert order == ["fit_strong", "fit_weak", "big"]                    # fit first, then proof breaks the tie
    assert "family_capped" not in ranked["fit_strong"].flags
    assert ranked["big"].score < ranked["big"].breakdown["deal"]["home_score_uncapped"]   # outside the top 1: capped


# ── Gate 3 buyer fixes ───────────────────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("name, expected", [
    ("Focus Healthcare Partners", "PE/investor"), ("Strawberry Fields REIT", "REIT"),
    ("FRIO HOSPITAL DISTRICT", "hospital/public"), ("Pulaski Memorial Hospital", "hospital/public"),
    ("First Midwest Bank", "lender"), ("Sylva Operations LLC", "operator"),
])
def test_buyer_type_from_name(name, expected):
    assert rules.buyer_type(name)[0] == expected


def test_buyer_type_weights():
    assert rules.buyer_type_weight("operator") == rules.buyer_type_weight("PE/investor") == 1.0
    assert 0 < rules.buyer_type_weight("REIT") < 1 and 0 < rules.buyer_type_weight("public company") < 1
    assert rules.buyer_type_weight("hospital/public") == rules.buyer_type_weight("lender") == 0


def test_shell_buyers():
    assert rules.is_shell_buyer("RICHLAND PA OPCO LLC", "RICHLAND NURSING AND REHAB")
    assert rules.is_shell_buyer("Arcadia Care Toulon LLC", "ARCADIA CARE TOULON")      # named after the facility
    assert not rules.is_shell_buyer("Focus Healthcare Partners", "HARBORSIDE")


def test_news_change_date_uses_stated_event_dates():
    pub = date(2025, 11, 25)
    assert rules.news_change_date([{"event": "Chapter 11 filing", "date_text": "2023"}], pub) == date(2023, 12, 31)
    assert rules.news_change_date([], pub) == pub
    assert rules.news_change_date([{"date_text": "March 2027"}], pub) == pub          # never after publishing


def test_old_closed_sale_is_penalized_and_buyer_goes_stale():
    old = Buyer("named", "Encore Healthcare Services", event_date=date(2023, 12, 31), buyer_type="operator")
    new = Buyer("named", "Encore Healthcare Services", event_date=date(2026, 6, 1), buyer_type="operator")
    sigs = [sig("court_sale_process", family_id=1, confirmed_by="cms_operator")]
    l_old, l_new = score_facility(FAC, sigs, old, TODAY), score_facility(FAC, sigs, new, TODAY)
    assert l_old.breakdown["closed_sale_penalty"] < 0 and "sale_closed_over_12m" in l_old.flags
    assert l_new.breakdown["closed_sale_penalty"] == 0


def test_main_list_needs_three_signals_or_one_hard_event():
    capped_ch11 = [sig("negative_noi"), sig("chapter_11", family_id=5, confirmed_by="cms_ownership")]
    two_metrics = [sig("negative_noi"), sig("low_occupancy")]
    three = [sig("negative_noi"), sig("low_occupancy"), sig("sff_or_candidate")]
    assert score_facility(FAC, capped_ch11, Buyer(), TODAY, frozenset({5})).tier == "main"   # hard event, even capped
    assert score_facility(FAC, two_metrics, Buyer(), TODAY).tier == "watch"
    assert score_facility(FAC, three, Buyer(), TODAY).tier == "main"


def test_public_event_needs_a_web_link_and_never_makes_a_buyer(monkeypatch):
    import pytest
    from src.score import rules, signals
    monkeypatch.setattr(rules, "court_facts", lambda: {"public_events": [{"case": "x:1", "signal_type": "foreclosure",
                                                                         "source_url": "licensed:provider"}]})
    with pytest.raises(ValueError):
        signals.public_event_signals(conn=None)


def test_case_loader_prefers_the_courtlistener_docket_link():
    from src.discover.courts.loader import UPSERT
    assert "courtlistener.com/docket/" in UPSERT and "source_url       = case when" in UPSERT
