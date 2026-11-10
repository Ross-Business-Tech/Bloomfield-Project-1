"""python -m src.discover.cms [--source ID] [--limit N] [--dry-run]"""
from __future__ import annotations

import argparse

from src.common.http import PoliteClient, SourceBlocked

from . import catalog
from .api import total_rows
from .sources import CONFLICT, HAS_RAW_ID, HASH_KEYED, SOURCES, STG_COLUMNS, sample_json, to_db_value, validate


def _dataset(source_id, catalog_json, client):
    if source_id == "cms_sff":
        return None
    return catalog.resolve(client, source_id, catalog_json)


def run_source(source_id: str, limit: int | None, dry_run: bool, catalog_json) -> dict:
    table, stream = SOURCES[source_id]
    result = {"source_id": source_id, "table": table, "rows": 0, "invalid": 0, "samples": [], "info": {}, "status": "ok"}
    client = PoliteClient(source_id)
    try:
        ds = _dataset(source_id, catalog_json, client)
        if ds:
            result["dataset"] = f"{ds.title} -> {ds.api_url}" + ("" if ds.resolved else "  [FALLBACK id]")
            result["available"] = total_rows(client, ds.api_url)
        batches = stream(client, ds, limit, result["info"])
        if dry_run:
            for batch in batches:
                for item in batch:
                    errs = validate(table, item.stg)
                    result["rows"] += 1
                    if errs:
                        result["invalid"] += 1
                        result.setdefault("errors", []).append((item.external_id, errs))
                    elif len(result["samples"]) < 3:
                        result["samples"].append(sample_json(item.stg))
        else:
            result.update(_load(source_id, table, batches))
    except SourceBlocked as exc:
        result["status"] = f"BLOCKED (manual): {exc}"
    finally:
        client.close()
    return result


def _load(source_id, table, batches) -> dict:
    from src.common.db import connect
    from src.common.ingest_run import ingest_run
    from src.common.bulk import bulk_upsert
    from src.common.raw_writer import content_hash, write_records

    out = {"rows": 0, "raw_new": 0, "stg_written": 0}
    cols = STG_COLUMNS[table]
    key = CONFLICT[table]
    use_raw = HAS_RAW_ID.get(table, True)
    with connect() as conn, ingest_run(conn, source_id) as run:
        for n, batch in enumerate(batches):
            if not batch:
                continue
            if table in HASH_KEYED:
                for i in batch:   # row fingerprint lives on the stg row (no natural key in these files)
                    i.stg["content_hash"] = content_hash([source_id, i.payload])
            bad = [i for i in batch if validate(table, i.stg)]
            if bad:
                raise ValueError(f"{len(bad)} invalid rows, e.g. {validate(table, bad[0].stg)}")
            # Free-plan size limit: CMS files are huge and public, so raw.record keeps ONE fingerprint per
            # downloaded page (payload omitted). The full download stays in the local cache (data/raw/cache).
            page = [i.payload for i in batch]
            new = write_records(conn, source_id, run.run_id,
                                [(f"page:{n}", batch[0].source_url, page)], keep_payload=False)
            out["raw_new"] += new
            raw_id = None
            if use_raw:
                with conn.cursor() as cur:
                    cur.execute("select id from raw.record where content_hash = %s", (content_hash([source_id, page]),))
                    raw_id = cur.fetchone()["id"]
            all_cols = (["raw_id"] if use_raw else []) + cols
            rows = [([raw_id] if use_raw else []) + [to_db_value(i.stg.get(c)) for c in cols] for i in batch]
            # COPY-based bulk write (src/common/bulk.py): one round trip per batch instead of one per row.
            written = bulk_upsert(conn, table, all_cols, rows, key, update=table not in HASH_KEYED)
            conn.commit()
            run.rows_in += written
            out["rows"] += len(batch)
            out["stg_written"] += written
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m src.discover.cms")
    ap.add_argument("--source", choices=[*SOURCES], help="run one source (default: all)")
    ap.add_argument("--limit", type=int, help="cap rows per source")
    ap.add_argument("--dry-run", action="store_true", help="fetch + parse + validate; no database")
    args = ap.parse_args(argv)

    sources = [args.source] if args.source else list(SOURCES)
    cat = None
    if any(s != "cms_sff" for s in sources):
        with PoliteClient("cms_catalog") as c:
            try:
                cat = catalog.load_catalog(c)
            except SourceBlocked as exc:
                print(f"BLOCKED (manual): data.cms.gov catalog: {exc}")
        if cat is None:
            print("WARNING: could not read data.json; using fallback dataset ids from BUILD_SPEC")

    blocked = failed = 0
    for sid in sources:
        res = run_source(sid, args.limit, args.dry_run, cat)
        print(f"\n== {sid} -> {res['table']}: {res['status']}")
        if "dataset" in res:
            print(f"   dataset: {res['dataset']}")
        if res.get("available") is not None:
            print(f"   rows available in dataset: {res['available']}")
        print(f"   rows {'parsed' if args.dry_run else 'processed'}: {res['rows']}   invalid: {res['invalid']}   {res['info'] or ''}")
        for k in ("raw_new", "stg_written"):
            if k in res:
                print(f"   {k}: {res[k]}")
        for s_ in res["samples"]:
            print("   sample:", s_)
        for ext, errs in res.get("errors", [])[:3]:
            print("   INVALID", ext, errs)
        blocked += res["status"].startswith("BLOCKED")
        failed += res["invalid"] > 0
    return 2 if blocked else (1 if failed else 0)
