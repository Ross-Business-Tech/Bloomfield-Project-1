"""Per-source streams. Each yields batches of Item; the runner writes raw + stg (or just validates in --dry-run)."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from typing import Callable, Iterator

from src.common.http import PoliteClient

from . import parse, sff
from .api import iter_pages
from .catalog import Dataset

# stg table -> columns the loader may set (everything but id/raw_id), checked against the migration in tests.
STG_COLUMNS = {
    "stg.cms_chow": ["source_url", "effective_date", "chow_type", "state", "buyer_enrollment_id", "buyer_ccn", "buyer_associate_id",
                     "buyer_org_name", "buyer_dba_name", "seller_enrollment_id", "seller_ccn", "seller_associate_id",
                     "seller_org_name", "seller_dba_name", "content_hash"],
    "stg.cms_chow_owner": ["source_url", "chow_side", "enrollment_id", "associate_id", "organization_name", "owner_associate_id",
                           "owner_type", "owner_name", "role_text", "ownership_pct", "association_date", "content_hash"],
    "stg.cms_snf_owner": ["source_url", "enrollment_id", "associate_id", "organization_name", "owner_associate_id", "owner_type",
                          "owner_name", "role_text", "ownership_pct", "association_date", "is_private_equity", "is_reit",
                          "is_holding_company", "content_hash"],
    "stg.cms_snf_enrollment": ["source_url", "enrollment_id", "ccn", "npi", "associate_id", "organization_name", "dba_name", "street",
                               "city", "state", "zip5", "phone", "organization_type", "proprietary_nonprofit",
                               "provider_name", "affiliation_entity_name", "affiliation_entity_id"],
    "stg.cms_cost_report": ["source_url", "ccn", "fiscal_year_end", "facility_name", "street", "city", "state", "zip5", "beds",
                            "total_days", "total_bed_days_available", "occupancy_proxy", "net_income", "total_revenue"],
    "stg.cms_pbj_quarter": ["source_url", "ccn", "quarter", "quarter_end", "days_reported", "avg_census", "total_nurse_hprd", "rn_hprd"],
    "stg.cms_chain_perf": ["source_url", "chain_id", "chain_name", "report_date", "num_facilities", "num_states", "avg_overall_rating",
                           "pct_sff", "total_fines", "measures"],
    "stg.cms_sff": ["source_url", "report_month", "ccn", "facility_name", "state", "status", "months_on_list"],
}
REQUIRED = {  # NOT NULL columns without defaults
    "stg.cms_snf_enrollment": ["enrollment_id"], "stg.cms_cost_report": ["ccn", "fiscal_year_end"],
    "stg.cms_pbj_quarter": ["ccn", "quarter", "quarter_end"], "stg.cms_chain_perf": ["chain_id"],
    "stg.cms_sff": ["report_month", "ccn", "status"],
}
# conflict key for idempotent upserts. Tables without a natural key use the row's content_hash (insert-only).
CONFLICT = {
    "stg.cms_chow": ["content_hash"], "stg.cms_chow_owner": ["content_hash"], "stg.cms_snf_owner": ["content_hash"],
    "stg.cms_snf_enrollment": ["enrollment_id"], "stg.cms_cost_report": ["ccn", "fiscal_year_end"],
    "stg.cms_pbj_quarter": ["ccn", "quarter"], "stg.cms_chain_perf": ["chain_id", "report_date"],
    "stg.cms_sff": ["report_month", "ccn"],
}
HASH_KEYED = {t for t, k in CONFLICT.items() if k == ["content_hash"]}
HAS_RAW_ID = {"stg.cms_pbj_quarter": False, "stg.cms_sff": False}


@dataclass
class Item:
    external_id: str | None
    source_url: str
    payload: dict      # goes to raw.record
    stg: dict          # goes to the stg table


@dataclass
class SourceSpec:
    source_id: str
    table: str
    stream: Callable[..., Iterator[list[Item]]]
    info: dict          # filled by the stream (skipped counts, notes) for the report


def _jsonable(v):
    return v.isoformat() if isinstance(v, (date, datetime)) else v


def _simple(mapper, ext_id):
    def stream(client: PoliteClient, ds: Dataset, limit, info):
        for url, rows in iter_pages(client, ds.api_url, limit):
            yield [Item(ext_id(r), url, r, mapper(r, url)) for r in rows]
    return stream


def _enrollment_ext(r):
    return r.get("ENROLLMENT ID")


def _chain_stream(client, ds, limit, info):
    rd = parse.dt(ds.version_date)   # report date = release date in the dataset title (the data itself carries none)
    info["skipped_no_chain_id"] = 0
    for url, rows in iter_pages(client, ds.api_url, limit):
        items = []
        for r in rows:
            if not parse.s(r.get("Chain ID")):    # the "National" summary row has no chain id
                info["skipped_no_chain_id"] += 1
                continue
            items.append(Item(parse.s(r["Chain ID"]), url, r, parse.map_chain_perf(r, url, rd)))
        yield items


def _cost_stream(client, ds, limit, info):
    """Buffer then dedupe: keep the highest rpt_rec_num per (CCN, fiscal year end) so amended filings win."""
    best: dict[tuple, tuple[int, Item]] = {}
    info["skipped_missing_key"] = 0
    info["duplicate_filings_dropped"] = 0
    for url, rows in iter_pages(client, ds.api_url, limit):
        for r in rows:
            stg = parse.map_cost_report(r, url)
            if not stg["ccn"] or not stg["fiscal_year_end"]:
                info["skipped_missing_key"] += 1
                continue
            key = (stg["ccn"], stg["fiscal_year_end"])
            rec = parse.integer(r.get("rpt_rec_num")) or 0
            if key in best:
                info["duplicate_filings_dropped"] += 1
                if best[key][0] >= rec:
                    continue
            best[key] = (rec, Item(parse.s(r.get("rpt_rec_num")), url, r, stg))
    items = [v[1] for v in best.values()]
    for i in range(0, len(items), 1000):
        yield items[i:i + 1000]


def _pbj_stream(client, ds, limit, info):
    agg = parse.PbjAggregator()
    for url, rows in iter_pages(client, ds.api_url, limit):
        for r in rows:
            agg.add(r)
    info["daily_rows_read"] = agg.daily_rows
    items = [Item(ext, ds.landing_url, payload, stg) for ext, stg, payload in agg.rows(ds.landing_url)]
    for i in range(0, len(items), 1000):
        yield items[i:i + 1000]


def sff_stream(client: PoliteClient, ds, limit, info, today: date | None = None):
    found = sff.fetch_latest_pdf(client, today)
    if not found:
        info["note"] = "no SFF PDF found for the last 7 months"
        return
    url, month, content = found
    rows = sff.parse_pdf(content)
    info["pdf_url"], info["report_month"] = url, month.isoformat()
    info["by_table"] = {t: sum(1 for r in rows if r["table"] == t) for t in ("Table A", "Table D")}
    if limit:
        rows = rows[:limit]
    items = []
    for r in rows:
        stg = {"source_url": url, "report_month": month, "ccn": r["ccn"], "facility_name": r["facility_name"],
               "state": r["state"], "status": r["status"], "months_on_list": r["months_on_list"]}
        payload = {k: _jsonable(v) for k, v in stg.items()}
        items.append(Item(f"{r['ccn']}|{month.isoformat()}", url, payload, stg))
    yield items


SOURCES: dict[str, tuple[str, Callable]] = {
    "cms_chow": ("stg.cms_chow", _simple(parse.map_chow, parse.chow_external_id)),
    "cms_chow_owner": ("stg.cms_chow_owner", _simple(parse.map_chow_owner, parse.owner_external_id)),
    "cms_snf_owner": ("stg.cms_snf_owner", _simple(parse.map_snf_owner, parse.owner_external_id)),
    "cms_snf_enrollment": ("stg.cms_snf_enrollment", _simple(parse.map_enrollment, _enrollment_ext)),
    "cms_cost_report": ("stg.cms_cost_report", _cost_stream),
    "cms_pbj": ("stg.cms_pbj_quarter", _pbj_stream),
    "cms_chain_perf": ("stg.cms_chain_perf", _chain_stream),
    "cms_sff": ("stg.cms_sff", sff_stream),
}


def validate(table: str, stg: dict) -> list[str]:
    errs = []
    extra = set(stg) - set(STG_COLUMNS[table])
    if extra:
        errs.append(f"unknown columns {sorted(extra)}")
    for c in REQUIRED.get(table, []):
        if stg.get(c) is None:
            errs.append(f"{c} is null")
    if not stg.get("source_url"):
        errs.append("source_url missing")
    if table == "stg.cms_sff" and stg.get("status") not in ("sff", "candidate"):
        errs.append("bad status")
    if table == "stg.cms_chow_owner" and stg.get("chow_side") not in (None, "buyer", "seller"):
        errs.append("bad chow_side")
    return errs


def to_db_value(v):
    if isinstance(v, dict):
        from psycopg.types.json import Jsonb
        return Jsonb(v)
    return v


def sample_json(stg: dict) -> str:
    return json.dumps(stg, default=str, ensure_ascii=False)
