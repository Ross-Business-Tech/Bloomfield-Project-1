"""One raw.ingest_run row per loader run.

    with ingest_run(conn, "hud_active") as run:
        run.rows_in += write_records(conn, "hud_active", run.run_id, rows)
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass

from .http import SourceBlocked


@dataclass
class Run:
    run_id: str
    source_id: str
    rows_in: int = 0


@contextmanager
def ingest_run(conn, source_id: str):
    with conn.cursor() as cur:
        cur.execute("insert into raw.ingest_run (source_id) values (%s) returning run_id", (source_id,))
        run = Run(run_id=cur.fetchone()["run_id"], source_id=source_id)
    conn.commit()
    try:
        yield run
    except SourceBlocked as exc:
        conn.rollback()
        _finish(conn, run, "blocked", str(exc))
        raise
    except Exception as exc:
        conn.rollback()
        _finish(conn, run, "failed", f"{type(exc).__name__}: {exc}"[:2000])
        raise
    else:
        conn.commit()
        _finish(conn, run, "success", None)


def _finish(conn, run: Run, status: str, error: str | None):
    with conn.cursor() as cur:
        cur.execute(
            "update raw.ingest_run set finished_at = now(), rows_in = %s, status = %s, error = %s where run_id = %s",
            (run.rows_in, status, error, run.run_id),
        )
        if status == "success":
            cur.execute("update raw.source set last_pulled_at = now() where source_id = %s", (run.source_id,))
    conn.commit()
