import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location("leak_guard", Path(__file__).parents[2] / "tools" / "leak_guard.py")
lg = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lg)


def _diff(path, *lines):
    return f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n@@ -0,0 +1,{len(lines)} @@\n" + \
        "".join(f"+{x}\n" for x in lines)


def test_clean_change_passes():
    assert lg.check(_diff("src/a.py", "x = 1", "url = 'https://example.org'"), ["src/a.py"], [], []) == []


def test_keys_emails_and_private_files_fail():
    fake_jwt = "eyJ" + "a" * 20 + ".eyJ" + "b" * 20 + "." + "c" * 12
    fake_key = "sk-ant-" + "x" * 24
    d = _diff("src/a.py", f"KEY = '{fake_key}'", f"anon = '{fake_jwt}'", "contact: jane.doe" + "@" + "school.edu",
              "DATABASE_URL=postgresql://user:pw123" + "@" + "db.host:5432/postgres")
    p = lg.check(d, ["src/a.py", "eval/notes_PRIVATE.csv"], [], [])
    assert any("_PRIVATE" in x for x in p) and any("Anthropic" in x for x in p) and any("JWT" in x for x in p)
    assert any("email" in x for x in p) and any("database URL" in x for x in p)
    assert all("src/a.py:" in x or "notes_PRIVATE" in x for x in p)


def test_noreply_emails_are_allowed():
    d = _diff("README.md", "by 123+someone@users.noreply.github.com", "see test@example.com")
    assert lg.check(d, ["README.md"], [], []) == []


def test_private_terms_and_allowed_phrases():
    d = _diff("docs/x.md", "Deal in Springfield Gardens", "Springfield Gardens appears in this public CMS row")
    p = lg.check(d, ["docs/x.md"], ["springfield gardens"], ["public cms row"])
    assert p == ["docs/x.md:1: private term #1 from eval/private/forbidden_terms.txt"]
