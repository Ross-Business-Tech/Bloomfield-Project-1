"""Write fetched documents to raw.document: one fresh connection per source, batched COPY, commit per batch."""
from __future__ import annotations

from src.common.bulk import bulk_upsert
from src.common.db import connect
from src.common.http import SourceBlocked
from src.common.ingest_run import ingest_run
from src.common.raw_writer import content_hash

COLUMNS = ["source_id", "run_id", "source_url", "title", "published_at", "body_text", "content_hash", "buyer_evidence"]
BATCH_SIZE = 100


def doc_row(source_id: str, run_id, d) -> list:
    return [source_id, run_id, d.source_url, d.title, d.published_at, d.body_text,
            content_hash(f"{d.title or ''}\n{d.body_text or ''}"), False]


def _mark_failed(run, exc: Exception) -> None:
    """Best effort on a brand-new connection (the old one may be dead). Keeps rows_in of committed batches."""
    try:
        with connect() as c:
            with c.cursor() as cur:
                cur.execute(
                    "update raw.ingest_run set finished_at = now(), rows_in = %s, status = 'failed', error = %s "
                    "where run_id = %s",
                    (run.rows_in, f"{type(exc).__name__}: {exc}"[:2000], run.run_id))
            c.commit()
    except Exception:
        pass


def store_source(source_id: str, res, *, batch_size: int = BATCH_SIZE) -> int:
    """Returns rows inserted. Raises SourceBlocked for manual/blocked sources (run marked blocked),
    any other exception on failure (run marked failed). Batches already committed stay."""
    run = None
    conn = None
    try:
        conn = connect()
        with ingest_run(conn, source_id) as run:
            if res.manual:
                raise SourceBlocked(res.manual)
            docs = res.docs
            for i in range(0, len(docs), batch_size):
                rows = [doc_row(source_id, run.run_id, d) for d in docs[i:i + batch_size]]
                run.rows_in += bulk_upsert(conn, "raw.document", COLUMNS, rows, ["source_url"], update=False)
                conn.commit()
        return run.rows_in
    except SourceBlocked:
        raise
    except Exception as exc:
        if run is not None:
            _mark_failed(run, exc)
        raise
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
