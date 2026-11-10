import re
from datetime import date
from pathlib import Path

import httpx
import pytest

from src.common.http import PoliteClient, SourceBlocked
from src.discover.cms import catalog, parse, sff
from src.discover.cms.api import iter_pages
from src.discover.cms.sources import CONFLICT, SOURCES, STG_COLUMNS, REQUIRED, sff_stream, validate

MIGRATION_DIR = Path(__file__).parents[3] / "supabase" / "migrations"
MIGRATION = next(Path(__file__).parents[3].glob("supabase/migrations/*raw_and_stg.sql")).read_text()


def client_for(tmp_path, handler):
    return PoliteClient("t", cache_dir=tmp_path, transport=httpx.MockTransport(handler), sleep=lambda s: None, min_interval=0)


# ── schema agreement ────────────────────────────────────────────────────────────
def table_columns(table):
    body = re.search(r"create table " + re.escape(table) + r" \((.*?)\n\);", MIGRATION, re.S).group(1)
    cols = []
    for line in body.splitlines():
        m = re.match(r"\s+([a-z_0-9]+)\s+\w+", line)
        if m and m.group(1) not in ("unique", "id", "raw_id", "primary"):
            cols.append(m.group(1))
    later = "\n".join(f.read_text() for f in sorted(MIGRATION_DIR.glob("*.sql")) if "raw_and_stg" not in f.name)
    for alter in re.finditer(r"alter table " + re.escape(table) + r"\b(.*?);", later, re.S | re.I):
        cols += re.findall(r"add column (?:if not exists )?([a-z_0-9]+)", alter.group(1), re.I)
    return cols


@pytest.mark.parametrize("table", list(STG_COLUMNS))
def test_stg_columns_match_migration(table):
    assert sorted(STG_COLUMNS[table]) == sorted(table_columns(table))


def test_conflict_keys_and_required_exist():
    for t, key in CONFLICT.items():
        assert all(c in STG_COLUMNS[t] for c in key or [])
    for t, cols in REQUIRED.items():
        assert all(c in STG_COLUMNS[t] for c in cols)


# ── catalog ─────────────────────────────────────────────────────────────────────
def ds(title, id_, modified="2026-01-01"):
    return {"title": title, "modified": modified, "landingPage": {"accessURL": "https://data.cms.gov/x"},
            "distribution": [{"format": "CSV", "accessURL": ""},
                             {"format": "API", "accessURL": f"{catalog.API_ROOT}/{id_}/data"}]}


def test_pick_latest_uses_exact_title_and_newest_version():
    cat = {"dataset": [
        ds("Skilled Nursing Facility Change of Ownership : 2026-06-01", "old"),
        ds("Skilled Nursing Facility Change of Ownership : 2026-09-01", "new"),
        ds("Skilled Nursing Facility Change of Ownership - Owner Information : 2026-12-01", "owner"),
    ]}
    d = catalog.resolve(None, "cms_chow", cat)
    assert d.api_url.endswith("/new/data") and d.version_date == "2026-09-01" and d.resolved
    d = catalog.resolve(None, "cms_chow_owner", cat)
    assert d.api_url.endswith("/owner/data")


def test_resolve_falls_back_to_build_spec_id():
    d = catalog.resolve(None, "cms_pbj", {"dataset": []})
    assert not d.resolved and "7e0d53ba-8f02-4c66-98a5-14a1c997c50d" in d.api_url


# ── paging ──────────────────────────────────────────────────────────────────────
def test_iter_pages_respects_limit_and_stops(tmp_path):
    seen = []

    def handler(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(404)
        q = dict(req.url.params)
        seen.append(q)
        n = min(int(q["size"]), 7 - int(q["offset"]))
        return httpx.Response(200, json=[{"i": i} for i in range(n)])

    pages = list(iter_pages(client_for(tmp_path, handler), "https://data.cms.gov/data-api/v1/dataset/x/data", limit=5))
    assert sum(len(r) for _, r in pages) == 5 and seen[0]["size"] == "5"
    pages = list(iter_pages(client_for(tmp_path / "b", handler), "https://data.cms.gov/data-api/v1/dataset/x/data"))
    assert sum(len(r) for _, r in pages) == 7


def test_block_raises(tmp_path):
    def handler(req):
        return httpx.Response(404) if req.url.path == "/robots.txt" else httpx.Response(403)

    with pytest.raises(SourceBlocked):
        list(iter_pages(client_for(tmp_path, handler), "https://data.cms.gov/data-api/v1/dataset/x/data"))


# ── mappers ─────────────────────────────────────────────────────────────────────
def test_chow_keeps_leading_zero_text_and_blank_is_none():
    r = {"CCN - BUYER": "015009", "CCN - SELLER": "", "EFFECTIVE DATE": "2025-08-01", "ENROLLMENT STATE - BUYER": "AL"}
    m = parse.map_chow(r, "http://u")
    assert m["buyer_ccn"] == "015009" and m["seller_ccn"] is None and m["effective_date"] == date(2025, 8, 1)
    assert validate("stg.cms_chow", m) == []


def test_owner_individual_vs_org_and_flags():
    org = {"ENROLLMENT ID": "E1", "TYPE - OWNER": "O", "ORGANIZATION NAME - OWNER": "ACME REIT LLC", "PERCENTAGE OWNERSHIP": "12.5",
           "PRIVATE EQUITY COMPANY - OWNER": "Y", "REIT - OWNER": "N", "HOLDING COMPANY - OWNER": ""}
    m = parse.map_snf_owner(org, "u")
    assert m["owner_type"] == "organization" and m["owner_name"] == "ACME REIT LLC" and m["ownership_pct"] == 12.5
    assert (m["is_private_equity"], m["is_reit"], m["is_holding_company"]) == (True, False, None)
    ind = {"TYPE - OWNER": "I", "FIRST NAME - OWNER": "ANN", "MIDDLE NAME - OWNER": "", "LAST NAME - OWNER": "LEE"}
    assert parse.map_chow_owner(ind, "u")["owner_name"] == "ANN LEE"
    assert parse.map_chow_owner(ind, "u")["chow_side"] is None


def test_enrollment_zip_and_whitespace():
    m = parse.map_enrollment({"ENROLLMENT ID": "E", "CCN": "015009", "ZIP CODE": "027526", "ADDRESS LINE 1": "111  PEMBERTON   DR", "ADDRESS LINE 2": ""}, "u")
    assert m["zip5"] == "02752" and m["street"] == "111 PEMBERTON DR" and m["ccn"] == "015009" and m["phone"] is None


def test_cost_report_occupancy_and_unknown():
    r = {"Provider CCN": "105402", "Fiscal Year End Date": "2023-12-31", "Total Days Total": "10916", "Total Bed Days Available": "12696",
         "Net Income": "-125148", "Net Patient Revenue": "3386027", "Number of Beds": "138"}
    m = parse.map_cost_report(r, "u")
    assert m["occupancy_proxy"] == round(10916 / 12696, 4) and m["net_income"] == -125148 and m["total_revenue"] == 3386027 and m["beds"] == 138
    r["Total Bed Days Available"] = "0"
    assert parse.map_cost_report(r, "u")["occupancy_proxy"] is None
    r["Total Bed Days Available"] = ""
    assert parse.map_cost_report(r, "u")["occupancy_proxy"] is None


def test_chain_perf_measures_and_pct_sff():
    r = {"Chain": "X", "Chain ID": "12", "Number of facilities": "50", "Number of Special Focus Facilities (SFF)": "2",
         "Average overall 5-star rating": "3.1", "Total amount of fines in dollars": "1000", "Average staffing rating": "2.2", "Blank": ""}
    m = parse.map_chain_perf(r, "u", date(2026, 9, 1))
    assert m["pct_sff"] == 4.0 and m["measures"] == {"Number of Special Focus Facilities (SFF)": "2", "Average staffing rating": "2.2"}
    assert validate("stg.cms_chain_perf", m) == []


# ── PBJ ─────────────────────────────────────────────────────────────────────────
def daily(ccn, day, census, rn, lpn, cna, q="2026Q2"):
    return {"PROVNUM": ccn, "CY_Qtr": q, "WorkDate": day, "MDScensus": str(census), "Hrs_RN": str(rn), "Hrs_LPN": str(lpn), "Hrs_CNA": str(cna),
            "Hrs_RNDON": "0", "PROVNAME": "N", "STATE": "AL"}


def test_pbj_aggregates_to_one_row_per_ccn_quarter():
    agg = parse.PbjAggregator()
    agg.add(daily("015009", "20260401", 50, 10, 20, 70))
    agg.add(daily("015009", "20260402", 100, 20, 40, 140))
    agg.add(daily("015009", "20260403", 0, 5, 5, 5))        # zero census day is ignored
    agg.add(daily("015010", "20260401", 10, 5, 0, 5, q="2026Q1"))
    rows = list(agg.rows("http://landing"))
    assert [r[0] for r in rows] == ["015009|2026Q2", "015010|2026Q1"]
    _, stg, payload = rows[0]
    assert stg["days_reported"] == 2 and stg["avg_census"] == 75.0
    assert stg["total_nurse_hprd"] == round((30 + 60 + 210) / 150, 3) and stg["rn_hprd"] == round(30 / 150, 3)
    assert stg["quarter_end"] == date(2026, 6, 30) and rows[1][1]["quarter_end"] == date(2026, 3, 31)
    assert validate("stg.cms_pbj_quarter", stg) == [] and payload["quarter_end"] == "2026-06-30"
    assert agg.daily_rows == 4


# ── SFF PDF ─────────────────────────────────────────────────────────────────────
def make_pdf(pages):
    """Tiny single-font PDF: pages = list of list of (x, y, text)."""
    objs = ["<< /Type /Catalog /Pages 2 0 R >>", None, "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    kids = []
    for lines in pages:
        content = "BT /F1 8 Tf " + " ".join(f"1 0 0 1 {x} {y} Tm ({t}) Tj" for x, y, t in lines) + " ET"
        objs.append(f"<< /Length {len(content)} >>\nstream\n{content}\nendstream")
        cid = len(objs)
        objs.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 700 800] /Resources << /Font << /F1 3 0 R >> >> /Contents {cid} 0 R >>")
        kids.append(f"{len(objs)} 0 R")
    objs[1] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(kids)} >>"
    out, offs = b"%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offs.append(len(out))
        out += f"{i} 0 obj\n{o}\nendobj\n".encode()
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offs)
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode()
    return out


def sff_page(title, rows):
    lines = [(40, 760, title), (40, 740, "Provider"), (110, 740, "Facility Name"), (300, 740, "Address"), (420, 740, "City")]
    y = 720
    for ccn, name, addr, city, st, zp, phone, tail in rows:
        lines += [(40, y, ccn), (110, y, name), (300, y, addr), (420, y, city), (500, y, st), (520, y, zp), (555, y, phone), (630, y, tail)]
        y -= 14
    return lines


PDF = make_pdf([
    [(40, 760, "Introductory text only")],
    sff_page("Table A: Current SFF Facilities", [("015463", "Knollwood Healthcare", "3151-A Knollwood Drive", "Mobile", "AL", "36693", "251-661-7608", "26"),
                                                   ("15E064", "Core Of Dale", "1 Main St", "Dale", "IN", "47523", "812-555-0100", "4")]),
    sff_page("Table B: Facilities That Have Graduated from the SFF Program", [("015217", "Graduate Home", "1 A St", "Birmingham", "AL", "35214", "205-798-8780", "19")]),
    sff_page("Table D: SFF Candidate List", [("015420", "CUMBERLAND HEALTH AND REHAB", "47065 AL HIGHWAY 277", "BRIDGEPORT", "AL", "35740", "256-437-7260", "6")]),
])


def test_parse_pdf_tables_a_and_d_only():
    rows = sff.parse_pdf(PDF)
    assert [(r["ccn"], r["status"], r["months_on_list"], r["state"]) for r in rows] == [
        ("015463", "sff", 26, "AL"), ("15E064", "sff", 4, "IN"), ("015420", "candidate", 6, "AL")]
    assert rows[0]["facility_name"] == "Knollwood Healthcare"
    assert all(r["ccn"] != "015217" for r in rows)


def test_sff_steps_back_months_and_builds_valid_rows(tmp_path):
    asked = []

    def handler(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(404)
        asked.append(req.url.path)
        if req.url.path.endswith("october-2026.pdf"):
            return httpx.Response(200, content=PDF, headers={"content-type": "application/pdf"})
        return httpx.Response(404, text="nope")

    info = {}
    batches = list(sff_stream(client_for(tmp_path, handler), None, None, info, today=date(2026, 11, 9)))
    assert asked[0].endswith("november-2026.pdf") and asked[1].endswith("october-2026.pdf")
    items = batches[0]
    assert len(items) == 3 and info["report_month"] == "2026-10-01"
    assert all(validate("stg.cms_sff", i.stg) == [] for i in items)
    assert items[0].stg["source_url"].endswith("october-2026.pdf") and items[0].stg["report_month"] == date(2026, 10, 1)


def test_sources_registry_covers_all_seeded_source_ids():
    assert set(SOURCES) == {"cms_chow", "cms_chow_owner", "cms_snf_owner", "cms_snf_enrollment", "cms_cost_report", "cms_pbj", "cms_chain_perf", "cms_sff"}
