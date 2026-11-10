from contextlib import contextmanager

from src.discover.courts import loader
from src.discover.courts.base import CaseRecord, DocRecord, SourceResult


class FakeCursor:
    def __init__(self, db):
        self.db, self.rowcount, self._row = db, 0, None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass

    def execute(self, sql, params=None):
        s = " ".join(sql.split())
        if s.startswith("insert into raw.ingest_run"):
            self._row = {"run_id": "r1"}
        elif s.startswith("insert into stg.court_case"):
            key = (params["court"], params["docket_no"])
            self._row = {"inserted": key not in self.db["cases"]}
            self.db["cases"].add(key)
        elif s.startswith("insert into raw.document"):
            new = params[2] not in self.db["docs"]
            self.db["docs"].add(params[2])
            self.rowcount = 1 if new else 0

    def fetchone(self):
        return self._row


class FakeConn:
    def __init__(self):
        self.db = {"cases": set(), "docs": set()}

    def cursor(self):
        return FakeCursor(self.db)

    def commit(self):
        pass

    def rollback(self):
        pass


def test_rerun_inserts_nothing_new():
    res = SourceResult("elevenflo",
                       cases=[CaseRecord("elevenflo", "u", "Bankr. N.D. Ga.", "26-1")],
                       docs=[DocRecord("elevenflo", "u", "t", "body", None, False)])
    conn = FakeConn()
    first = loader.load_source(conn, "elevenflo", res)
    second = loader.load_source(conn, "elevenflo", res)
    assert first == {"new_docs": 1, "new_cases": 1, "updated_cases": 0}
    assert second == {"new_docs": 0, "new_cases": 0, "updated_cases": 1}


def test_validate_flags_duplicate_keys():
    c = CaseRecord("stretto", "u", "UNKNOWN", "1")
    assert loader.validate(SourceResult("stretto", cases=[c, c]))


def test_search_only_commits_per_page(monkeypatch):
    from src.discover.courts import courtlistener

    conn = FakeConn()
    commits = []
    conn.commit = lambda: commits.append(1)

    def fake_cur_exec_many(self, sql, rows):
        for r in rows:
            self.db["cases"].add((r["court"], r["docket_no"]))
    FakeCursor.executemany = fake_cur_exec_many

    def fake_run(limit, search_only, on_cases, queries=None):
        assert search_only
        on_cases([CaseRecord("courtlistener", "u", "deb", "1")])
        before = len(commits)
        on_cases([CaseRecord("courtlistener", "u", "deb", "2")])
        assert len(commits) == before + 1          # committed immediately after each page
        return SourceResult("courtlistener")

    monkeypatch.setattr(courtlistener, "run", fake_run)
    out = loader.load_courtlistener_search_only(conn)
    assert out["cases_upserted"] == 2 and conn.db["cases"] == {("deb", "1"), ("deb", "2")}
