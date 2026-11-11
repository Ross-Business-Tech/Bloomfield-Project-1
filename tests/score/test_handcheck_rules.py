from datetime import date

import pytest

from src.score import links, rules
from src.score.rules import Sig

ROUNDUP = ("Blueprint announced its involvement in the sale of Cottages of Fox Lake, an assisted living community.\n\n"
           "The community was sold to a private equity buyer paired with Charter Senior Living, the release states.\n\n"
           "The Fountains of Hope sold\n\n"
           "Senior Living Investment Brokerage announced it sold The Fountains of Hope, a 107-unit community.\n\n"
           "The community saw over 10 bids, with a publicly traded owner-operator, the release states.")


def test_roundup_buyer_must_be_in_the_property_section():
    assert rules.buyer_in_property_section(ROUNDUP, "The Fountains of Hope", "Charter Senior Living") is False
    assert rules.buyer_in_property_section(ROUNDUP, "Cottages of Fox Lake", "Charter Senior Living") is True
    assert rules.buyer_in_property_section(ROUNDUP, "Some Other Home", "Charter Senior Living") is None
    assert rules.buyer_in_property_section("One paragraph only.", "X Home", "Buyer Co") is None


def test_news_closing_words():
    assert rules.news_reports_closing("was sold Sept. 8 to Strawberry Fields REIT")
    assert rules.news_reports_closing("facilitated the receivership sale of Marshall Pines")
    assert not rules.news_reports_closing("the stalking horse bid was approved; the home will be sold")
    assert not rules.news_reports_closing("filed for Chapter 11")


def test_court_sale_status_and_month_only_date():
    assert rules.court_sale_status("sale order") == "approved"
    assert rules.court_sale_status("sale closed") == "closed"
    assert rules.court_sale_status("stalking horse") is None
    assert rules.sale_date_month_only({"evidence_text": "the court approved the sale in January 2026"})
    assert not rules.sale_date_month_only({"evidence_text": "the sale closed on May 1, 2026"})


def test_docket_of_another_case_is_spotted():
    url = "https://www.courtlistener.com/docket/1/inspired-senior-living-of-delray-beach-dst/"
    assert rules.docket_names_other_case(url, "Inspired Healthcare Capital Holdings, LLC", "Mariella of Grapevine", "Grapevine")
    assert not rules.docket_names_other_case(url, "Inspired Healthcare Capital Holdings, LLC", "Azalea at Delray Beach", "Delray Beach")
    assert not rules.docket_names_other_case("https://www.courtlistener.com/docket/2/spiritrust-lutheran/", "SpiriTrust Lutheran")
    assert not rules.docket_names_other_case("https://elevenflo.com/cases/x", "Anything")


def test_links_care_compare_first_raw_last():
    raw = "https://data.cms.gov/data-api/v1/dataset/abc/data?offset=0&size=5000"
    out = links.ordered([raw, "https://www.courtlistener.com/docket/2/x/", "https://news.example/a", "https://news.example/a#deal-2"],
                        ["https://buyer.example/proof"], "475027B")
    urls = [x["url"] for x in out]
    assert urls[0] == "https://buyer.example/proof" and urls[-1] == raw and out[-1]["label"] == links.RAW_LABEL
    assert "https://www.medicare.gov/care-compare/details/nursing-home/475027" in urls
    assert "https://news.example/a" not in urls and "https://news.example/a#deal-2" in urls
    assert links.care_compare("075198001") is None and links.care_compare(None) is None
    assert links.stage_text("sale", {"status": "closed", "date": "2026-05-01"}) == "closed / sold (2026-05-01)"
    assert links.stage_text("sale", {"status": "approved", "date": None}) == "sale approved (date NOT FOUND)"
    assert links.stage_text("late", None) == "late"


def test_every_override_has_a_public_source_and_a_date():
    items = rules.manual_overrides()
    assert items and all(str(o["source_url"]).startswith("https://") and o["date"] for o in items)


def test_sale_status_reaches_the_lead_and_old_closings_leave_list_a():
    from src.score.buyers import Buyer
    from src.score.diversity import in_list_a
    from src.score.leads import score_facility
    fac = {"facility_id": "f1", "units": 90, "beds": None, "city": "X", "state": "TX", "care_types": ["AL"]}
    sig = lambda d: Sig("f1", "court_sale_process", "sale", "k", "https://court.example/1", None, 5, "sale closed",   # noqa: E731
                        "legal", sale_status="closed", sale_date=d)
    buyer = Buyer("named", "Buyer Co", None, "https://b.example", "court_sale", event_date=date(2026, 9, 1))
    lead = score_facility(fac, [sig(date(2026, 9, 1))], buyer, date(2026, 11, 11))
    assert lead.breakdown["sale_status"]["status"] == "closed" and "sale_closed" in lead.flags
    assert in_list_a({"buyer_status": "named", "flags": lead.flags})
    old = score_facility(fac, [sig(date(2025, 6, 1))], Buyer(), date(2026, 11, 11))
    assert "sale_closed_over_12m" in old.flags and not in_list_a({"buyer_status": "named", "flags": old.flags})
    licensed = Sig("f1", "court_sale_process", "sale", "k2", "licensed:x", None, 5, "x", "legal", sale_status="closed")
    assert "sale_status" not in score_facility(fac, [licensed], Buyer(), date(2026, 11, 11)).breakdown
