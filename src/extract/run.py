"""Run extraction over raw.document -> stg.extraction.

Row conventions in stg.extraction (one row per document x model x prompt_version):
- fields.status = 'extracted'          model output, post-processed (model = Claude model id)
- fields.status = 'skipped_prefilter'  never sent to the API (model = 'prefilter', prompt_version = PREFILTER_VERSION)
- fields.status = 'error'              API/parse failure; retried on the next run
"""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor

from psycopg.types.json import Jsonb

from src.common.bulk import bulk_upsert

from . import client as cl
from .prefilter import PREFILTER_VERSION, passes
from .prompt import PROMPT_VERSION, SYSTEM_PROMPT
from .schema import FIELDS, NOT_FOUND, postprocess

COLUMNS = ["document_id", "model", "prompt_version", "fields", "not_found_fields"]
CONFLICT = ["document_id", "model", "prompt_version"]
BUDGET_USD = 15.0
CHARS_PER_TOKEN = 3.6          # assumption for English news text; checked against real usage in the report
EXPECTED_OUTPUT_TOKENS = 450   # assumption; measured on the eval set


def pending_documents(conn, model: str, limit: int | None, doc_ids: list[int] | None = None) -> list[dict]:
    """doc_ids: only these raw.document ids (the nightly refresh sends tonight's new articles, never the backlog)."""
    sql = """
        select d.id, d.source_id, d.source_url, d.title, d.body_text, d.content_hash
        from raw.document d
        where not exists (select 1 from stg.extraction e where e.document_id = d.id and e.model = %(model)s
                          and e.prompt_version = %(pv)s and e.fields->>'status' = 'extracted')
          and not exists (select 1 from stg.extraction e where e.document_id = d.id and e.model = 'prefilter'
                          and e.prompt_version = %(pfv)s)
    """ + (" and d.id = any(%(ids)s)" if doc_ids is not None else "") + """
        order by d.id
    """ + (" limit %(limit)s" if limit else "")
    return conn.execute(sql, {"model": model, "pv": PROMPT_VERSION, "pfv": PREFILTER_VERSION, "limit": limit,
                              "ids": list(doc_ids or [])}).fetchall()


def db_cached(conn, model: str, hashes: list[str]) -> dict[str, dict]:
    """Extracted fields already stored for another document with the same content_hash (same model + prompt)."""
    if not hashes:
        return {}
    rows = conn.execute("""
        select distinct on (d.content_hash) d.content_hash, e.fields
        from stg.extraction e join raw.document d on d.id = e.document_id
        where e.model = %s and e.prompt_version = %s and e.fields->>'status' = 'extracted'
          and d.content_hash = any(%s)
    """, (model, PROMPT_VERSION, hashes)).fetchall()
    return {r["content_hash"]: r["fields"] for r in rows}


def estimate_usd(model: str, docs: list[dict], batch: bool) -> tuple[float, int]:
    pin, pout = cl.PRICES.get(model, cl.PRICES["claude-sonnet-5-5"])
    sys_tokens = len(SYSTEM_PROMPT) / CHARS_PER_TOKEN
    doc_tokens = sum(len(d["title"] or "") + len(d["body_text"] or "") + 40 for d in docs) / CHARS_PER_TOKEN
    # first request writes the cache, the rest read it (batches cache on a best-effort basis: assume half hit)
    sys_cost = sys_tokens * len(docs) * pin * (0.55 if batch else 0.1)
    usd = (doc_tokens * pin + sys_cost + EXPECTED_OUTPUT_TOKENS * len(docs) * pout) / 1e6
    return usd * (0.5 if batch else 1.0), int(doc_tokens + sys_tokens * len(docs))


def _row(doc_id, model, pv, fields, not_found):
    return [doc_id, model, pv, Jsonb(fields), not_found]


def run(conn, model: str = cl.DEFAULT_MODEL, limit: int | None = None, dry_run: bool = False,
        use_batch: bool | None = None, budget: float = BUDGET_USD, log=print, doc_ids: list[int] | None = None) -> dict:
    docs = pending_documents(conn, model, limit, doc_ids)
    stats = {"pending": len(docs), "skipped_prefilter": 0, "from_cache": 0, "extracted": 0, "errors": 0,
             "api_usd": 0.0}
    to_skip, to_send = [], []
    for d in docs:
        ok, why = passes(d["source_id"], d["title"], d["body_text"])
        (to_send if ok else to_skip).append({**d, "prefilter": why})
    stats["skipped_prefilter"] = len(to_skip)

    # cache hits: local file cache first, then same content_hash already in the DB
    hits, need = {}, []
    dbc = db_cached(conn, model, [d["content_hash"] for d in to_send])
    for d in to_send:
        raw = cl.cache_get(model, d["content_hash"])
        if raw is not None:
            hits[d["id"]] = postprocess(raw, f"{d['title'] or ''}\n{d['body_text'] or ''}")
        elif d["content_hash"] in dbc:
            hits[d["id"]] = (dict(dbc[d["content_hash"]]), None)
        else:
            need.append(d)
    stats["from_cache"] = len(hits)

    batch = (use_batch if use_batch is not None else len(need) >= 50) and not cl.is_gemini(model)
    est, tokens = estimate_usd(model, need, batch)
    stats["estimate_usd"] = round(est, 4)
    log(f"{len(docs)} pending | {len(to_skip)} skipped by pre-filter | {len(hits)} cached | {len(need)} to send")
    log(f"estimated cost for {len(need)} API calls on {model} ({'batch' if batch else 'standard'}): "
        f"~${est:.2f} (~{tokens:,} input tokens)")
    if dry_run:
        return stats
    if est > budget:
        log(f"ESTIMATE ${est:.2f} EXCEEDS BUDGET ${budget:.2f}: not running. Re-run with a --limit.")
        stats["aborted_over_budget"] = True
        return stats

    rows = [_row(d["id"], "prefilter", PREFILTER_VERSION,
                 {"status": "skipped_prefilter", "reason": d["prefilter"], "content_hash": d["content_hash"]}, [])
            for d in to_skip]
    by_id = {d["id"]: d for d in to_send}
    for doc_id, (fields, nf) in hits.items():
        fields = {**fields, "status": "extracted", "content_hash": by_id[doc_id]["content_hash"],
                  "prefilter": by_id[doc_id]["prefilter"]}
        if nf is None:
            nf = [k for k in FIELDS if fields.get(k) == NOT_FOUND]
        rows.append(_row(doc_id, model, PROMPT_VERSION, fields, nf))
    _flush(conn, rows)

    api = cl.client() if need else None
    spend = cl.Spend(model=model, batch=batch)
    results, errors = {}, {}
    if need and batch:
        outcome = cl.extract_batch(api, model, [{"custom_id": str(d["id"]), "title": d["title"], "body": d["body_text"]}
                                                for d in need], spend, log=log)
        results = {int(k): v for k, v in outcome.results.items()}
        errors = {int(k): v for k, v in outcome.errors.items()}
    elif need:
        def one(d):
            try:
                return d["id"], cl.extract_one(api, model, d["title"], d["body_text"], spend), None
            except Exception as exc:
                return d["id"], None, f"{type(exc).__name__}: {exc}"[:500]
        with ThreadPoolExecutor(max_workers=6) as pool:
            for doc_id, raw, err in pool.map(one, need):
                if err:
                    errors[doc_id] = err
                else:
                    results[doc_id] = raw

    rows = []
    need_by_id = {d["id"]: d for d in need}
    for doc_id, raw in results.items():
        d = need_by_id[doc_id]
        cl.cache_put(model, d["content_hash"], raw)
        fields, nf = postprocess(raw, f"{d['title'] or ''}\n{d['body_text'] or ''}")
        fields.update(status="extracted", content_hash=d["content_hash"], prefilter=d["prefilter"])
        rows.append(_row(doc_id, model, PROMPT_VERSION, fields, nf))
    for doc_id, err in errors.items():
        rows.append(_row(doc_id, model, PROMPT_VERSION,
                         {"status": "error", "error": err, "content_hash": need_by_id[doc_id]["content_hash"]}, []))
    _flush(conn, rows)
    stats.update(extracted=len(results) + len(hits), errors=len(errors), api_usd=round(spend.usd, 4),
                 api_calls=spend.calls, input_tokens=spend.input_tokens, cache_read_tokens=spend.cache_read,
                 output_tokens=spend.output_tokens)
    log(json.dumps(stats))
    return stats


def _flush(conn, rows: list) -> None:
    for i in range(0, len(rows), 500):
        bulk_upsert(conn, "stg.extraction", COLUMNS, rows[i:i + 500], CONFLICT, update=True)
        conn.commit()
