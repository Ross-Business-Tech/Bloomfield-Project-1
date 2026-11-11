"""Contact loader: parsing and cleaning only (no database)."""
from src.enrich import contacts


def test_clean_website():
    assert contacts.clean_website("WWW.Example.org/home/?utm=1#x") == "https://www.example.org/home"
    assert contacts.clean_website("http://example.org/") == "http://example.org"
    for bad in ("javascript:alert(1)", "ftp://example.org", "not a site", "", None, "https://user:pw@example.org"):
        assert contacts.clean_website(bad) is None


def test_parse_cms(tmp_path):
    f = tmp_path / "nh.csv"
    f.write_text("CMS Certification Number (CCN),Provider Name,Telephone Number\n"
                 "015009,A,(205) 555-0101\n12345,B,1-734-555-0102\n999999,C,\n,D,5550103\n111111,E,12-34\n")
    rows, stats = contacts.parse_cms(f)
    assert [(r["ccn"], r["value"]) for r in rows] == [("015009", "2055550101"), ("012345", "7345550102")]
    assert stats == {"rows": 5, "no_ccn": 1, "no_phone": 1, "bad_phone": 1}
    assert all(r["source_url"].startswith("https://") and r["kind"] == "phone" for r in rows)


def test_parse_manual_refuses_unsafe_rows(tmp_path):
    f = tmp_path / "m.csv"
    f.write_text("ccn,facility_id,kind,value,label,source_url\n"
                 "015009,,phone,(205) 555-0101,Admissions,https://example.org/p\n"
                 "015009,,email,boss@example.org,,https://example.org/p\n"     # personal e-mail: never stored
                 "015009,,phone,555,,https://example.org/p\n"                  # not a phone number
                 "015009,,website,example.org,,\n"                            # no public link
                 ",,website,example.org,,https://example.org/p\n")            # no building given
    rows, problems = contacts.parse_manual(f)
    assert len(rows) == 1 and rows[0]["label"] == "Admissions" and rows[0]["value"] == "2055550101"
    assert len(problems) == 4 and "e-mail" in problems[0]
