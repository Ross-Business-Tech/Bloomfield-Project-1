from datetime import date

from src.discover.licensed.loans import _name_score, _nf
from src.score.signals import _month_back, _run_latest, _run_start
from src.score.takeovers import same_family


def test_not_found_values():
    assert _nf("0") is None and _nf("N/A") is None and _nf("") is None and _nf(" 0.0 ") is None
    assert _nf("12") == "12"


def test_truncated_licensed_name_matches():
    assert _name_score("SUNNY ACRES ASSISTED LIVING AT", "Sunny Acres Assisted Living at Lakeside") >= 90


def test_status_history_most_recent_left():
    hist = "--RRRRXXSSSWWCC"
    assert _run_latest(hist, "R") == 2          # still REO in the latest reported month
    assert _run_start(hist, "R") == 5           # REO started 5 months back
    assert _run_latest("CCCSSS", "S") is None   # current now: not an ongoing special servicing
    assert _month_back(0) == date(2026, 10, 1) and _month_back(13) == date(2025, 9, 1)


def test_same_family_restructurings():
    assert same_family("DARSEA I, LLC", "DARSEA HOLDINGS, LLC")
    assert same_family("PSL TANGLEWOOD TENANT, LLC", "PEGASUS SENIOR LIVING, LLC")
    assert same_family("BROOKDALE SENIOR LIVING INC PUBLICLY TRADED", "EMERITUS CORPORATION")
    assert not same_family("GREENVILLE OPCO LLC", "CSSL 3 TX LLC")
    assert not same_family("CIVITAS SENIOR HEALTHCARE LLC", "SILVERPOINT SENIOR LIVING")


def test_subset_names_do_not_match():
    assert _name_score("The Gardens at Old Mill Willows", "The Willows") < 80
    assert _name_score("Rosewood of Fairview", "Rosewood Senior Living of Fairview") >= 90


def test_public_row_masks_licensed_parts():
    from src.score.export import public_row
    assert public_row({"flags": ["licensed_private", "licensed_only"]}) is None
    assert public_row({"flags": ["licensed_private"], "score_breakdown": {}}) is None      # no public twin
    r = public_row({"flags": ["licensed_private"], "distress_type": "chapter_11, special_servicing", "stage": "sale",
                    "buyer": "X LLC", "buyer_status": "named", "buyer_source_url": "licensed:provider", "score": 185,
                    "score_breakdown": {"stage_from": "special_servicing",
                                        "public": {"score": 173, "distress_type": "chapter_11", "stage": "late",
                                                   "buyer": "Y (unnamed)", "buyer_status": "candidate",
                                                   "buyer_source_url": "https://b.example", "buyer_chain": None}},
                    "source_urls": ["https://a.example", "licensed:provider:x"]})
    assert r["distress_type"] == "chapter_11" and r["stage"] == "late" and r["score"] == 173
    assert r["buyer"] == "Y (unnamed)" and r["buyer_source_url"] == "https://b.example"
    assert r["source_urls"] == ["https://a.example"] and r["flags"] == []
    assert "stage_from" not in r["score_breakdown"] and "public" not in r["score_breakdown"]


def test_backtest_replay_rule():
    from eval.backtest import tier_as_of
    sigs = [{"signal_type": "chapter_11", "event_date": date(2026, 2, 1)},
            {"signal_type": "low_occupancy", "event_date": date(2025, 1, 1)}]
    assert tier_as_of(sigs, date(2026, 6, 1))[0] == "main"      # hard event known by then
    assert tier_as_of(sigs, date(2025, 6, 1))[0] == "watch"     # only the early signal
    assert tier_as_of(sigs, date(2024, 6, 1))[0] == "none"


def test_loans_read_rows_with_example_columns(tmp_path):
    """The loader reads any export through the column map; the committed example map has fake headers."""
    from src.discover.licensed.loans import COLUMNS_EXAMPLE, load_columns, read_rows
    hud, loans = tmp_path / "hud.csv", tmp_path / "loans.csv"
    hud.write_text("Facility,Town,St,Unit Count,Pool,Start Date,End Date,Pay History,Months Late,Source\n"
                   "Sunny Acres,Lakeside,MI,80,Pool A,01/15/2015,2035-01-01,CC12C,2,\n")
    loans.write_text("Facility,Portfolio,Town,St,Unit Count,Status,Original Amount,Start Date,End Date,"
                     "Transfer Reason,Status History,Source\n"
                     "Oak Glen,Group 1,Springfield,OH,0,Special Servicing,1000000,N/A,2030-06-30,Default,SSCC,\n")
    rows = read_rows(hud, loans, load_columns(COLUMNS_EXAMPLE))
    h, l = rows
    assert h["loan_key"] == "lic_hud232:sunny acres:MI" and h["units"] == 80 and h["deal_name"] == "Pool A"
    assert h["orig_date"] == date(2015, 1, 15) and h["status_history"] == "CC12C" and h["source_label"] == "licensed export"
    assert l["loan_group"] == "Group 1" and l["units"] is None and l["orig_date"] is None
    assert l["loan_status"] == "Special Servicing" and l["transfer_reason"] == "Default" and l["orig_balance"] == 1e6
