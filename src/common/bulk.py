"""Fast bulk writes: COPY into a temp table, then one INSERT ... ON CONFLICT.

Row-by-row inserts cost one network round trip each; to a remote Supabase pooler that can be 0.1-0.7 s,
so 400k rows would take days. COPY streams a whole batch in one go.
"""
from __future__ import annotations

import uuid
from typing import Iterable, Sequence

from psycopg import sql


def bulk_upsert(
    conn,
    table: str,
    columns: Sequence[str],
    rows: Iterable[Sequence],
    conflict: Sequence[str],
    *,
    update: bool = True,
) -> int:
    """Insert rows into schema.table; on a conflict on `conflict` columns update the other columns
    (update=True) or skip the row (update=False). Returns the number of rows inserted or updated.

    Within one call, rows with the same conflict key keep the LAST occurrence (same as sequential upserts).
    """
    rows = [list(r) for r in rows]
    if not rows:
        return 0
    cols = list(columns)
    if update:
        key_idx = [cols.index(c) for c in conflict]
        last = {}
        for r in rows:
            last[tuple(r[i] for i in key_idx)] = r
        rows = list(last.values())

    schema, name = table.split(".")
    tmp = sql.Identifier(f"_bulk_{uuid.uuid4().hex[:10]}")
    target = sql.Identifier(schema, name)
    col_list = sql.SQL(", ").join(map(sql.Identifier, cols))
    if update and any(c not in conflict for c in cols):
        sets = sql.SQL(", ").join(
            sql.SQL("{c} = excluded.{c}").format(c=sql.Identifier(c)) for c in cols if c not in conflict
        )
        action = sql.SQL("do update set {}").format(sets)
    else:
        action = sql.SQL("do nothing")

    with conn.cursor() as cur:
        cur.execute(sql.SQL("create temp table {tmp} as select {cols} from {target} with no data").format(
            tmp=tmp, cols=col_list, target=target))
        with cur.copy(sql.SQL("copy {tmp} ({cols}) from stdin").format(tmp=tmp, cols=col_list)) as cp:
            for r in rows:
                cp.write_row(r)
        cur.execute(sql.SQL(
            "insert into {target} ({cols}) select {cols} from {tmp} on conflict ({key}) {action}"
        ).format(target=target, cols=col_list, tmp=tmp,
                 key=sql.SQL(", ").join(map(sql.Identifier, conflict)), action=action))
        n = max(cur.rowcount, 0)
        cur.execute(sql.SQL("drop table {}").format(tmp))
    return n
