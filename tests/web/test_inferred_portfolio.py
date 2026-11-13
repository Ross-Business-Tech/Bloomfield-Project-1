"""Buyers known only from a portfolio-wide sale report are 'inferred_portfolio': not named, not on List A, counted
under 'other'. The score and the main ranking do not change. (Made-up rows.)"""
from pathlib import Path

from src.score import links
from src.web import final_files, snapshot

SALE = {"status": "closed", "date": "2026-10-14", "source_url": "https://news.example/portfolio-sale", "portfolio": True}
OWN = {"status": "closed", "date": "2026-05-01", "source_url": "https://court.example/order"}


def row(fid, score, **kw):
    base = {"facility_id": fid, "lead_id": fid, "property": f"Home {fid}", "city": "X", "state": "PA", "care_types": ["SNF"],
            "units": None, "beds": 100, "distress_type": "chapter_11", "stage": "sale", "buyer": "NOT FOUND",
            "buyer_status": "NOT FOUND", "score": score, "flags": [], "source_urls": ["https://a.example/" + fid],
            "buyer_source_url": None, "score_breakdown": {}}
    return dict(base, **kw)


ROWS = [
    row("named-1", 90, buyer="Own Buyer LLC", buyer_status="named", buyer_source_url="https://court.example/order",
        flags=["sale_closed"], sale_status=OWN),
    row("port-1", 80, buyer="Portfolio Holdings LLC", buyer_status="named", buyer_source_url=SALE["source_url"],
        flags=["sale_closed", "portfolio_sale_inferred"], sale_status=SALE),
    row("port-2", 70, buyer="Portfolio Holdings LLC", buyer_status="named",
        flags=["sale_closed", "portfolio_sale_inferred"], score_breakdown={"sale_status": SALE}),
    # same portfolio flag, but its buyer has its own source naming the home: stays named
    row("port-own", 60, buyer="Own Buyer LLC", buyer_status="named", buyer_source_url="https://court.example/home-order",
        flags=["sale_closed", "portfolio_sale_inferred"], sale_status=SALE),
    row("nobuyer", 50, stage="early"),
]


def by_id(snap):
    return {x["lead_id"]: x for x in snap["items"]}


def test_only_portfolio_sourced_buyers_are_relabelled():
    it = by_id(snapshot.build(ROWS, set()))
    assert it["port-1"]["buyer_status"] == it["port-2"]["buyer_status"] == "inferred_portfolio"
    assert it["port-1"]["buyer_group"] == "inferred_portfolio"
    assert it["named-1"]["buyer_status"] == "named" and it["named-1"]["buyer_group"] == "confirmed"
    assert it["port-own"]["buyer_status"] == "named"
    assert it["port-1"]["buyer"] == "Portfolio Holdings LLC"          # the name stays visible, labelled as inferred


def test_inferred_leads_leave_list_a_and_are_counted_under_other():
    snap = snapshot.build(ROWS, set())
    it = by_id(snap)
    assert it["port-1"]["lists"] == [] and it["port-1"]["rank_a"] is None
    assert [r["property"] for r in snap["list_a"]] == ["Home named-1", "Home port-own"]
    m = snapshot.summary(snap["items"], [], 0)
    assert (m["list_a"], m["list_b"], m["other"]) == (2, 1, 2)
    assert m["other_breakdown"]["inferred_portfolio"] == 2
    assert m["list_a"] + m["list_b"] + m["other"] == m["leads"] == 5
    assert (m["buyers_named"], m["buyers_inferred_portfolio"]) == (2, 2)     # two separate counts, never one total
    snap["summary"], snap["funnel"] = m, [{"step": "Shown on Deal Radar", "count": 5}]
    assert snapshot.funnel_problems(snap) == []


def test_scores_and_main_order_do_not_change():
    snap = snapshot.build(ROWS, set())
    assert [(x["lead_id"], x["score"]) for x in snap["items"]] == [(r["facility_id"], float(r["score"])) for r in ROWS]
    assert [x["rank"] for x in snap["items"]] == [1, 2, 3, 4, 5]
    assert ROWS[1]["buyer_status"] == "named"                         # the input rows (the database view) are not edited


def test_stage_and_buyer_wording_in_csv_files():
    assert links.stage_text("sale", SALE) == "closed / sold (portfolio, inferred; 2026-10-14)"
    assert links.stage_text("sale", OWN) == "closed / sold (2026-05-01)"
    it = by_id(snapshot.build(ROWS, set()))
    out = final_files.row(it["port-1"])
    assert out["stage"] == "Closed / sold (portfolio, inferred)" and out["buyer_status"] == "Inferred · portfolio sale"
    assert final_files.row(it["named-1"])["stage"] == "Closed / sold"
    leads = {r["property"]: r for r in snapshot.build(ROWS, set())["leads"]}
    assert leads["Home port-1"]["buyer_status"] == "inferred_portfolio"


def test_page_code_shows_the_group_and_two_buyer_counts():
    js = (Path(snapshot.__file__).parent / "static" / "app.js").read_text()
    assert 'inferred_portfolio: "Inferred · portfolio sale"' in js
    assert '["inferred_portfolio", "Inferred · portfolio sale"]' in js            # its own group in the Buyers view
    assert 'fmtInt(named) + " named"' in js and 'fmtInt(inferred) + " inferred"' in js      # home tile: two counts
    assert "have a buyer named in a source" not in js and '"buyer named"]' not in js   # no combined % or count
    assert "o.inferred_portfolio" in js                                             # reconciliation bucket
