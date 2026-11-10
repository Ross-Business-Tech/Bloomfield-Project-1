from datetime import date

import httpx
import pytest

from src.common.http import PoliteClient
from src.discover.licenses import florida, michigan
from src.discover.licenses.__main__ import main
from src.discover.licenses.base import ManualSource

# Fake facility data only. Columns follow LARA's record description (31 fields).
FIXTURE = (
    '73,AH,AH730000001,"Sample Gardens","4850 Test Road","",Saginaw,MI,486030000,9895550100,70,20260801,20270731,REGULAR,'
    'N,N,N,Y,N,Y,N,N,"Sample Gardens LLC",NO,"1 Owner St","",Grand Haven,MI,49417,6165550101,ACTIVE\r\n'
    '81,AL,AL810000002,"Maple Court","Suite 9","12 Elm St.",Ypsilanti,MI,48197,7345550102,19,20250101,20270101,REGULAR,'
    'N,Y,N,N,N,N,Y,N,"Maple Care Inc.",NO,"P.O. Box 1","",Chelsea,MI,48118,,ACTIVE\r\n'
    '22,XH,XH220000003,"Pine View","9 Pine Rd","",Norway,MI,49870,,,,,EXEMPT,'
    'N,N,N,N,N,N,N,N,"Pine View Inc.",NO,"2 Main","",Kingsford,MI,49802,,ACTIVE\r\n'
    '41,AS,AS410000004,"Tiny Home","1 Small St","",Grand Rapids,MI,49508,,6,20260101,20280101,REGULAR,'
    'N,N,N,N,N,N,N,N,"Tiny LLC",NO,"1 Small St","",Grand Rapids,MI,49508,,ACTIVE\r\n'
    'bad,row\r\n'
)
PAGE = '<a href="https://documents.apps.lara.state.mi.us/bchs/afc_sw.txt">AFC</a><a href="/x.pdf">x</a>'


def test_parse_keeps_hfa_and_large_afc_only():
    rows, total, counts = michigan.parse_text(FIXTURE, "http://f", date(2026, 11, 9))
    assert total == 5 and counts["AS"] == 1 and counts["MALFORMED"] == 1
    assert [r.license_no for r in rows] == ["AH730000001", "AL810000002", "XH220000003"]
    a, b, c = rows
    assert a.zip5 == "48603" and a.capacity == 70 and a.licensee == "Sample Gardens LLC"
    assert a.street == "4850 Test Road" and a.license_type == "Home for the Aged (AH)"
    assert b.street == "12 Elm St."            # unit line in column E, street in F
    assert c.capacity is None and c.street == "9 Pine Rd"
    assert c.raw["expires"] is None            # unknown stays NULL


def test_find_file_url():
    assert michigan.find_file_url(PAGE).endswith("/bchs/afc_sw.txt")
    assert michigan.find_file_url("<a href='x'>") is None


def _client(tmp_path, last_modified="Mon, 09 Nov 2026 03:38:41 GMT"):
    def handler(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(404)
        if req.url.path.endswith("afc_sw.txt"):
            h = {"last-modified": last_modified} if last_modified else {}
            return httpx.Response(200, content=FIXTURE.encode(), headers=h)
        return httpx.Response(200, text=PAGE)
    return PoliteClient("mi_lara", cache_dir=tmp_path, transport=httpx.MockTransport(handler),
                        sleep=lambda s: None)


def test_fetch_as_of_from_last_modified_and_limit(tmp_path):
    res = michigan.fetch(_client(tmp_path), limit=2)
    assert res.as_of == date(2026, 11, 9) and res.as_of_basis == "Last-Modified header"
    assert res.total_rows == 5 and res.kept_before_limit == 3 and len(res.rows) == 2


def test_fetch_as_of_falls_back_to_download_date(tmp_path):
    res = michigan.fetch(_client(tmp_path, last_modified=None), today=date(2026, 1, 2))
    assert res.as_of == date(2026, 1, 2) and res.as_of_basis == "download date"


def test_florida_is_manual():
    with pytest.raises(ManualSource):
        florida.fetch(None)


def test_cli_florida_reports_manual_without_db(capsys):
    assert main(["--source", "fl_ahca", "--dry-run"]) == 0
    assert "MANUAL" in capsys.readouterr().out
