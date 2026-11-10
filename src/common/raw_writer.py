"""Write untouched inputs to raw.record / raw.document. Idempotent: duplicates are skipped by content_hash / URL."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Iterable

from psycopg.types.json import Jsonb

from .bulk import bulk_upsert


def content_hash(value) -> str:
    """Stable SHA-256 of a dict/list (key order ignored) or a string."""
    if isinstance(value, (dict, list)):
        value = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()


def write_records(
    conn, source_id: str, run_id, rows: Iterable[tuple[str | None, str, dict]], *, keep_payload: bool = True
) -> int:
    """rows = (external_id, source_url, payload). Returns how many new rows were inserted.

    keep_payload=False stores an empty payload but still hashes the full row, so re-runs stay idempotent.
    Used for very large public datasets (CMS) that would exceed the database plan's size limit; the
    downloads themselves stay in the local cache (data/raw/cache) and can always be fetched again.
    """
    params = []
    for external_id, source_url, payload in rows:
        if not source_url:
            raise ValueError("source_url is required for every raw record")
        stored = Jsonb(payload if keep_payload else {})
        params.append((source_id, run_id, external_id, source_url, stored, content_hash([source_id, payload])))
    return bulk_upsert(
        conn, "raw.record",
        ["source_id", "run_id", "external_id", "source_url", "payload", "content_hash"],
        params, ["content_hash"], update=False,
    )


def write_document(
    conn,
    source_id: str,
    run_id,
    source_url: str,
    *,
    title: str | None,
    body_text: str | None,
    published_at: datetime | None = None,
    buyer_evidence: bool = False,
) -> bool:
    """Insert one text document. Returns True if it was new."""
    if not source_url:
        raise ValueError("source_url is required for every document")
    with conn.cursor() as cur:
        cur.execute(
            """
            insert into raw.document (source_id, run_id, source_url, title, published_at, body_text, content_hash, buyer_evidence)
            values (%s, %s, %s, %s, %s, %s, %s, %s)
            on conflict (source_url) do nothing
            """,
            (source_id, run_id, source_url, title, published_at, body_text,
             content_hash(f"{title or ''}\n{body_text or ''}"), buyer_evidence),
        )
        return cur.rowcount == 1
