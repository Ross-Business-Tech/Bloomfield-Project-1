"""Special Focus Facility monthly PDF -> rows for stg.cms_sff.

Table A (current SFFs) -> status 'sff'; Table D (candidates) -> 'candidate'. Tables B (graduates) and C (terminated)
are not on the list and are skipped. Parsed with pdfplumber word positions because the PDF table cells run together.
"""
from __future__ import annotations

import io
import re
from collections import defaultdict
from datetime import date

from src.common.http import PoliteClient

PDF_URL = "https://www.cms.gov/files/document/sff-posting-candidate-list-{month}-{year}.pdf"
MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"]

_CCN = re.compile(r"[0-9]{2}[0-9A-Z][0-9]{3}")
_PHONE = re.compile(r"\d{3}-\d{3}-\d{4}")
_STATE_ZIP = re.compile(r"^[A-Z]{2}$")
_TABLES = {"Table A": "sff", "Table D": "candidate"}   # Table B / C intentionally absent


def candidate_months(today: date, back: int = 6):
    y, m = today.year, today.month
    for _ in range(back + 1):
        yield y, m
        m -= 1
        if m == 0:
            y, m = y - 1, 12


def fetch_latest_pdf(client: PoliteClient, today: date | None = None):
    """Try the current month, then step back. Returns (url, report_month, pdf_bytes) or None if none found."""
    for y, m in candidate_months(today or date.today()):
        url = PDF_URL.format(month=MONTHS[m - 1], year=y)
        r = client.get(url)          # SourceBlocked propagates (401/403/robots)
        if r.status_code == 200 and r.content[:4] == b"%PDF":
            return url, date(y, m, 1), r.content
    return None


def parse_pdf(pdf_bytes: bytes) -> list[dict]:
    """Return [{ccn, facility_name, state, status, months_on_list, table}] for Tables A and D."""
    import pdfplumber

    out = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            first = text.strip().split("\n", 1)[0] if text.strip() else ""
            status = next((v for k, v in _TABLES.items() if first.startswith(k + ":")), None)
            if not status:
                continue
            words = page.extract_words(keep_blank_chars=False, use_text_flow=False)
            addr_x = next((w["x0"] for w in words if w["text"] == "Address"), None)
            if addr_x is None:
                continue
            lines = defaultdict(list)
            for w in words:
                lines[round(w["top"])].append(w)
            for top in sorted(lines):
                ws = sorted(lines[top], key=lambda w: w["x0"])
                toks = [w["text"] for w in ws]
                line = " ".join(toks)
                if not _CCN.fullmatch(toks[0]) or not _PHONE.search(line):
                    continue
                name = " ".join(w["text"] for w in ws[1:] if w["x1"] <= addr_x + 1)
                st = next((t for i, t in enumerate(toks[:-1]) if _STATE_ZIP.match(t) and re.fullmatch(r"\d{5}", toks[i + 1])), None)
                months = int(toks[-1]) if toks[-1].isdigit() and not _PHONE.fullmatch(toks[-1]) else None
                out.append({"ccn": toks[0], "facility_name": name or None, "state": st, "status": status,
                            "months_on_list": months, "table": first.split(":")[0]})
    return out
