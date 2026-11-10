"""Load HUD FHA insured multifamily loans (Section 232 healthcare) into raw.record + stg.hud_*_loan.

    python -m src.discover.hud [--source hud_active|hud_terminated] [--limit N] [--dry-run]

Findings from inspecting the real files (Nov 2026):
- Row 1 is a title/row-count line, row 2 is the header (found by looking for "HUD PROJECT NUMBER").
- Neither file has a street address column, so street stays NULL (never guessed).
- The files carry no as-of date inside; as_of = the HTTP Last-Modified date, else the download date.
"""
from __future__ import annotations

import argparse
import io
import re
import warnings
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from email.utils import parsedate_to_datetime

import openpyxl

from src.common.http import PoliteClient, SourceBlocked
from src.common.normalize import zip5
from src.common.raw_writer import content_hash, write_records

BASE = "https://www.hud.gov/sites/default/files/Housing/documents/"
SOURCES = {
    "hud_active": BASE + "FHA-BF90-RM-A.xlsx",
    "hud_terminated": BASE + "FHA-BF90-RM-T.xlsx",
}
KEEP_PATTERN = re.compile(r"232|nursing|assisted|board", re.I)

STG_COLUMNS = {
    "hud_active": ["fha_number", "property_name", "street", "city", "state", "zip5", "units", "soa_code",
                   "soa_category", "endorsement_date", "maturity_date", "original_amount", "holder_name",
                   "servicer_name", "amortized_balance", "interest_rate"],
    "hud_terminated": ["fha_number", "property_name", "city", "state", "zip5", "units", "soa_category",
                       "original_amount", "termination_date", "termination_type", "term_claim_type"],
}
STG_TABLE = {"hud_active": "stg.hud_active_loan", "hud_terminated": "stg.hud_terminated_loan"}

# file header (upper-case) -> stg column
HEADER_MAP = {
    "HUD PROJECT NUMBER": "fha_number",
    "PROPERTY NAME": "property_name",
    "PROPERTY CITY": "city",
    "PROPERTY STATE": "state",
    "PROPERTY ZIP": "zip5",
    "UNITS": "units",
    "SECTION OF ACT CODE": "soa_code",
    "SOA CATEGORY/SUB CATEGORY": "soa_category",
    "INITIAL ENDORSEMENT DATE": "endorsement_date",
    "MATURITY DATE": "maturity_date",
    "ORIGINAL MORTGAGE AMOUNT": "original_amount",
    "HOLDER NAME": "holder_name",
    "SERVICER NAME": "servicer_name",
    "AMORITIZED PRINCIPAL BALANCE": "amortized_balance",
    "INTEREST RATE": "interest_rate",
    "TYPE (CLAIM/NON CLAIM)": "term_claim_type",
    "TERM_DATE": "termination_date",
    "TERMINATION TYPE DESCRIPTION": "termination_type",
}
PLACEHOLDERS = {"", "NO DATA AVAILABLE"}


@dataclass
class ParsedFile:
    source_id: str
    url: str
    as_of: date
    as_of_origin: str          # "last-modified header" | "download date"
    headers: list[str]
    total_rows: int
    kept: list[dict]           # raw payloads (all columns, JSON-safe) of kept rows
    stg: list[dict]            # typed stg rows (deduped on fha_number)
    duplicates_dropped: int = 0


# ── value cleaning ──────────────────────────────────────────
def _text(v) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return None if s.upper() in PLACEHOLDERS else s


def _date(v) -> date | None:
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    return None


def _int(v) -> int | None:
    try:
        return int(float(v)) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _amount(v) -> Decimal | None:
    try:
        return Decimal(str(v)) if v not in (None, "") else None
    except InvalidOperation:
        return None


def _json_safe(v):
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    return v


def _as_of(headers: dict, fallback: date | None = None) -> tuple[date, str]:
    lm = {k.lower(): v for k, v in headers.items()}.get("last-modified")
    if lm:
        try:
            return parsedate_to_datetime(lm).date(), "last-modified header"
        except (TypeError, ValueError):
            pass
    return fallback or date.today(), "download date"


# ── parsing ─────────────────────────────────────────────────
def parse_workbook(content: bytes, source_id: str, url: str, as_of: date, as_of_origin: str,
                   limit: int | None = None) -> ParsedFile:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        wb = openpyxl.load_workbook(io.BytesIO(content), data_only=True)
    ws = wb.worksheets[0]
    rows = ws.iter_rows(values_only=True)
    header_idx, headers = None, None
    buffered = []
    for i, row in enumerate(rows):
        if row and any(str(c).strip().upper() == "HUD PROJECT NUMBER" for c in row if c is not None):
            header_idx, headers = i, [str(c).strip() if c is not None else "" for c in row]
            break
        buffered.append(row)
        if i > 10:
            break
    if headers is None:
        raise ValueError(f"{url}: header row with 'HUD PROJECT NUMBER' not found in first 12 rows")
    upper = [h.upper() for h in headers]
    cat_col = next((i for i, h in enumerate(upper) if h.startswith("SOA CATEGORY")), None)
    if cat_col is None:
        raise ValueError(f"{url}: SOA category column not found; headers={headers}")
    colmap = {i: HEADER_MAP[h] for i, h in enumerate(upper) if h in HEADER_MAP}

    total, kept, stg_by_key, dups = 0, [], {}, 0
    for row in rows:
        if not row or all(c is None for c in row):
            continue
        total += 1
        category = row[cat_col]
        if not category or not KEEP_PATTERN.search(str(category)):
            continue
        if limit is not None and len(kept) >= limit:
            continue                      # keep counting total rows
        payload = {h: _json_safe(c) for h, c in zip(headers, row) if h}
        kept.append(payload)
        rec = {name: None for name in STG_COLUMNS[source_id]}
        for i, name in colmap.items():
            if name not in rec:
                continue
            v = row[i] if i < len(row) else None
            if name in ("endorsement_date", "maturity_date", "termination_date"):
                rec[name] = _date(v)
            elif name == "units":
                rec[name] = _int(v)
            elif name in ("original_amount", "amortized_balance", "interest_rate"):
                rec[name] = _amount(v)
            elif name == "zip5":
                rec[name] = zip5(_text(v))
            elif name == "fha_number":
                rec[name] = _text(v)          # stays text; leading zeros kept
            else:
                rec[name] = _text(v)
        if not rec["fha_number"]:
            continue
        rec["_payload"] = payload
        if rec["fha_number"] in stg_by_key:
            dups += 1
            prev = stg_by_key[rec["fha_number"]]
            # keep the latest termination (or the later row if no date)
            if (rec.get("termination_date") or date.min) < (prev.get("termination_date") or date.min):
                continue
        stg_by_key[rec["fha_number"]] = rec
    return ParsedFile(source_id, url, as_of, as_of_origin, headers, total, kept,
                      list(stg_by_key.values()), dups)


def fetch_and_parse(source_id: str, limit: int | None = None) -> ParsedFile:
    url = SOURCES[source_id]
    with PoliteClient(source_id) as client:
        resp = client.get(url)
    if resp.status_code != 200:
        raise RuntimeError(f"{url} returned HTTP {resp.status_code}")
    as_of, origin = _as_of(resp.headers)
    return parse_workbook(resp.content, source_id, url, as_of, origin, limit)


# ── database ────────────────────────────────────────────────
def load_to_db(conn, parsed: ParsedFile) -> dict:
    from src.common.ingest_run import ingest_run

    sid = parsed.source_id
    from src.common.bulk import bulk_upsert

    cols = ["raw_id", "source_url", "as_of"] + STG_COLUMNS[sid]
    with ingest_run(conn, sid) as run:
        new_raw = write_records(conn, sid, run.run_id,
                                ((p["HUD PROJECT NUMBER"], parsed.url, p) for p in parsed.kept))
        run.rows_in += new_raw
        hashes = {r["_payload"]["HUD PROJECT NUMBER"]: content_hash([sid, r["_payload"]]) for r in parsed.stg}
        with conn.cursor() as cur:
            cur.execute("select content_hash, id from raw.record where source_id = %s and content_hash = any(%s)",
                        (sid, list(hashes.values())))
            ids = {r["content_hash"]: r["id"] for r in cur.fetchall()}
            params = []
            for r in parsed.stg:
                raw_id = ids.get(hashes[r["fha_number"]])
                params.append([raw_id, parsed.url, parsed.as_of] + [r[c] for c in STG_COLUMNS[sid]])
        # COPY-based bulk upsert (src/common/bulk.py): one round trip instead of one per row.
        bulk_upsert(conn, STG_TABLE[sid], cols, params, ["fha_number", "as_of"])
    return {"raw_new": new_raw, "stg_upserted": len(parsed.stg)}


# ── CLI ─────────────────────────────────────────────────────
def _report(p: ParsedFile, sample: int = 3):
    print(f"[{p.source_id}] rows in file: {p.total_rows}; kept (232/Nursing/Assisted/Board): {len(p.kept)}; "
          f"stg rows: {len(p.stg)}; duplicate fha_numbers dropped: {p.duplicates_dropped}")
    print(f"[{p.source_id}] as_of = {p.as_of} (from {p.as_of_origin})")
    for r in p.stg[:sample]:
        print({k: (str(v) if v is not None else None) for k, v in r.items() if k != "_payload"})


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m src.discover.hud")
    ap.add_argument("--source", choices=sorted(SOURCES))
    ap.add_argument("--limit", type=int)
    ap.add_argument("--dry-run", action="store_true", help="download + parse only; no database connection")
    args = ap.parse_args(argv)
    sources = [args.source] if args.source else list(SOURCES)
    conn = None
    try:
        if not args.dry_run:
            from src.common.db import connect
            conn = connect()
        for sid in sources:
            try:
                parsed = fetch_and_parse(sid, args.limit)
            except SourceBlocked as exc:
                print(f"[{sid}] BLOCKED -> manual source: {exc}")
                return 2
            _report(parsed)
            if conn is not None:
                print(f"[{sid}] {load_to_db(conn, parsed)}")
    finally:
        if conn is not None:
            conn.close()
    return 0
