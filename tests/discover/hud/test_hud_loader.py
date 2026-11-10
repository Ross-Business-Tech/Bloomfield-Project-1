import io
from datetime import date, datetime
from decimal import Decimal

import openpyxl
import pytest

from src.discover.hud import loader

A_HEADERS = ["HUD PROJECT NUMBER", "PROPERTY NAME", "PROPERTY CITY", "PROPERTY STATE", "PROPERTY ZIP", "UNITS",
             "INITIAL ENDORSEMENT DATE", "FINAL ENDORSEMENT DATE", "ORIGINAL MORTGAGE AMOUNT", "FIRST PAYMENT DATE",
             "MATURITY DATE", "INTEREST RATE", "AMORITIZED PRINCIPAL BALANCE", "HOLDER NAME", "SERVICER NAME", "SECTION OF ACT CODE", "SOA CATEGORY/SUB CATEGORY"]
T_HEADERS = ["HUD PROJECT NUMBER", "PROPERTY NAME", "PROPERTY CITY", "PROPERTY STATE", "PROPERTY ZIP", "UNITS",
             "ORIGINAL MORTGAGE AMOUNT", "SOA CATEGORY/Sub Category", "TERMINATION TYPE DESCRIPTION", "TYPE (Claim/Non Claim)", "TERM_DATE"]


def make_xlsx(headers, rows):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["FHA_BF90_RM_A", " 3"])        # title row, header is row 2
    ws.append(headers)
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def active_file():
    return make_xlsx(A_HEADERS, [
        ["00011189", "Sunny Care", "Ann Arbor", "MI", "02134", 120, datetime(2006, 6, 28), None, 1236100.5,
         None, datetime(2041, 7, 1), 6.15, 833308.24, "Bank A", "Bank B", "RNA", "232/ 223(f)/Pur/Refin/ Assisted Living"],
        ["00011190", "Apartments", "X", "DC", "20009", 20, None, None, 1, None, None, "H", "S", "HRI",
         "207/ 223(F) PUR/ REFIN HSG."],
        ["00011191", "Old Folks", "Y", "OH", None, "n/a", None, None, None, None, None, None, None, "No Data Available",
         None, "HRB", "207 Nursing Homes"],
        ["00011192", "Board Home", "Z", "PA", "15213", 30, None, None, None, None, None, None, None, None, None, "RBC",
         "232/ 223(f)/Pur/Refin/ Board + Care"],
    ])


def parse(content, sid="hud_active", **kw):
    return loader.parse_workbook(content, sid, loader.SOURCES[sid], date(2026, 11, 2), "last-modified header", **kw)


def test_filters_and_types():
    p = parse(active_file())
    assert p.total_rows == 4 and len(p.kept) == 3
    first = p.stg[0]
    assert first["fha_number"] == "00011189" and first["zip5"] == "02134"     # leading zeros kept
    assert first["units"] == 120 and first["original_amount"] == Decimal("1236100.5")
    assert first["endorsement_date"] == date(2006, 6, 28) and first["street"] is None
    assert first["soa_code"] == "RNA"
    assert first["interest_rate"] == Decimal("6.15") and first["amortized_balance"] == Decimal("833308.24")
    second = p.stg[1]                                  # unknowns become NULL, not guesses
    assert second["units"] is None and second["zip5"] is None and second["holder_name"] is None
    assert second["servicer_name"] is None and second["original_amount"] is None


def test_rows_match_stg_columns_and_json_payload():
    p = parse(active_file())
    for r in p.stg:
        assert set(r) - {"_payload"} == set(loader.STG_COLUMNS["hud_active"])
    assert p.kept[0]["INITIAL ENDORSEMENT DATE"] == "2006-06-28T00:00:00"


def test_limit_caps_kept_but_counts_all():
    p = parse(active_file(), limit=1)
    assert len(p.kept) == 1 and p.total_rows == 4


def test_terminated_type_verbatim_and_dedupe():
    content = make_xlsx(T_HEADERS, [
        ["00000001", "A", "C", "VA", "22201", 10, 5.0, "207 Nursing Homes", "Missing termination type-old case", "CLAIM",
         datetime(1990, 1, 1)],
        ["00000001", "A", "C", "VA", "22201", 10, 5.0, "207 Nursing Homes", "Prepayment", "NONCLAIM", datetime(2000, 1, 1)],
        ["00000002", "B", "C", "VA", "22201", 10, 5.0, "608 Veteran Housing", "Prepayment", "NONCLAIM", datetime(2000, 1, 1)],
    ])
    p = parse(content, "hud_terminated")
    assert len(p.kept) == 2 and len(p.stg) == 1 and p.duplicates_dropped == 1
    assert p.stg[0]["termination_type"] == "Prepayment" and p.stg[0]["term_claim_type"] == "NONCLAIM" and p.stg[0]["termination_date"] == date(2000, 1, 1)


def test_missing_header_raises():
    with pytest.raises(ValueError):
        parse(make_xlsx(["foo", "bar"], [["1", "2"]]))


def test_as_of_from_header_or_download_date():
    assert loader._as_of({"Last-Modified": "Mon, 02 Nov 2026 20:18:08 GMT"}) == (date(2026, 11, 2), "last-modified header")
    d, origin = loader._as_of({}, fallback=date(2026, 1, 5))
    assert (d, origin) == (date(2026, 1, 5), "download date")
