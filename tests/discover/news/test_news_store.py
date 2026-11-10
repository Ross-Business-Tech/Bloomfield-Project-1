from datetime import datetime, timezone

import pytest

from src.common.http import SourceBlocked
from src.discover.news import loader as L
from src.discover.news import store


class Conn:
    def __init__(self, log):
        self.log, self.closed = log, False

    def commit(self):
        self.log.append("commit")

    def close(self):
        self.closed = True

    def cursor(self):
        raise AssertionError("cursor not expected")


class Run:
    def __init__(self):
        self.run_id, self.rows_in = "r1", 0


def docs(n):
    return [L.Doc("shn_rss", f"https://x.test/{i}", f"t{i}", "body", datetime(2026, 1, 1, tzinfo=timezone.utc))
            for i in range(n)]


def setup(monkeypatch, fail_on_batch=None):
    log, conns, calls, failed = [], [], [], []

    def fake_connect():
        c = Conn(log)
        conns.append(c)
        return c

    class FakeIngest:
        def __init__(self, conn, sid):
            self.run = Run()

        def __enter__(self):
            return self.run

        def __exit__(self, et, ev, tb):
            log.append("finish:" + ("ok" if et is None else et.__name__))
            return False

    def fake_bulk(conn, table, cols, rows, conflict, update=True):
        calls.append(len(rows))
        if fail_on_batch == len(calls):
            raise RuntimeError("the connection is lost")
        assert table == "raw.document" and conflict == ["source_url"] and update is False
        assert len(rows[0]) == len(cols)
        return len(rows)

    monkeypatch.setattr(store, "connect", fake_connect)
    monkeypatch.setattr(store, "ingest_run", FakeIngest)
    monkeypatch.setattr(store, "bulk_upsert", fake_bulk)
    monkeypatch.setattr(store, "_mark_failed", lambda run, exc: failed.append((run.rows_in, str(exc))))
    return log, conns, calls, failed


def test_batches_commit_each(monkeypatch):
    log, conns, calls, failed = setup(monkeypatch)
    res = L.SourceResult("shn_rss", docs=docs(250))
    assert store.store_source("shn_rss", res, batch_size=100) == 250
    assert calls == [100, 100, 50] and log.count("commit") == 3 and log[-1] == "finish:ok"
    assert conns[0].closed and not failed


def test_failure_keeps_committed_rows_and_marks_failed(monkeypatch):
    log, conns, calls, failed = setup(monkeypatch, fail_on_batch=3)
    with pytest.raises(RuntimeError):
        store.store_source("shn_rss", L.SourceResult("shn_rss", docs=docs(250)), batch_size=100)
    assert log.count("commit") == 2 and failed == [(200, "the connection is lost")] and conns[0].closed


def test_manual_source_is_blocked(monkeypatch):
    log, conns, calls, failed = setup(monkeypatch)
    with pytest.raises(SourceBlocked):
        store.store_source("gnews_rss", L.SourceResult("gnews_rss", manual="robots"))
    assert calls == [] and log == ["finish:SourceBlocked"] and not failed


def test_main_continues_after_failure_and_exits_nonzero(monkeypatch, capsys):
    from src.discover.news import __main__ as M
    seen = []

    def fake_store(sid, res):
        seen.append(sid)
        if sid == "shn_rss":
            raise RuntimeError("connection is closed")
        if sid == "snn_rss":
            raise SourceBlocked("manual")
        return 1
    monkeypatch.setattr(M, "run_source", lambda s, *a: L.SourceResult(s))
    monkeypatch.setattr(store, "store_source", fake_store)
    assert M.main(["--source", "shn_rss"]) == 1
    assert M.main(["--source", "snn_rss"]) == 0
    assert M.main(["--source", "shb_rss"]) == 0
