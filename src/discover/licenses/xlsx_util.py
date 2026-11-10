"""Shared helpers for state rosters published as Excel or Socrata files."""
from __future__ import annotations

import io
import warnings
from datetime import date, datetime
from email.utils import parsedate_to_datetime

import openpyxl

# Same rule as Michigan: homes with 12 beds or fewer are not investable assets (assumption, thresholds as MI).
MIN_CAPACITY = 13


def as_of_from(resp, today: date | None = None) -> tuple[date, str]:
    lm = {k.lower(): v for k, v in resp.headers.items()}.get("last-modified")
    try:
        return parsedate_to_datetime(lm).date(), "Last-Modified header"
    except (TypeError, ValueError):
        return today or date.today(), "download date"


def sheet_rows(content: bytes, header_marker: str | tuple[str, ...]) -> tuple[list[str], list[tuple]]:
    """Return (headers, data rows) of the first sheet; the header row is the first row containing a marker.
    Older captures spell some headers differently (e.g. Texas 2021-2023 'Facility_ID'); pass all spellings."""
    markers = (header_marker,) if isinstance(header_marker, str) else header_marker
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    it = wb.worksheets[0].iter_rows(values_only=True)
    headers = None
    for i, row in enumerate(it):
        if row and any(isinstance(c, str) and c.strip() in markers for c in row):
            headers = [" ".join(str(c).split()) if c is not None else "" for c in row]
            break
        if i > 15:
            break
    if headers is None:
        raise ValueError(f"header row with {markers!r} not found")
    return headers, [r for r in it if r and any(c not in (None, "") for c in r)]


def json_safe(v):
    return v.isoformat() if isinstance(v, (datetime, date)) else v


def to_int(v) -> int | None:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None
