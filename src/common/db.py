"""Database connection (Supabase Postgres via SUPABASE_DB_URL, service-role access)."""
from __future__ import annotations

import os

import psycopg
from psycopg.rows import dict_row

from .config import env


def connect() -> psycopg.Connection:
    """Open a connection to the dev Supabase database. Use as `with connect() as conn:`."""
    conn = psycopg.connect(env("SUPABASE_DB_URL"), row_factory=dict_row, prepare_threshold=None)
    # Bulk loads can outlast Supabase's default statement timeout on a slow uplink; batch jobs need no cap.
    conn.execute("set statement_timeout = 0")
    if os.environ.get("PIPELINE_FORCE_READ_WRITE") == "1":
        # Supabase's documented recovery step after freeing space while the project is still marked read-only.
        # Only set this by hand for a one-off reload; never in scheduled jobs.
        conn.execute("set session characteristics as transaction read write")
        conn.execute("set default_transaction_read_only = off")
    conn.commit()
    return conn
