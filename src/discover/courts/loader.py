"""Orchestration: fetch each source, then (unless dry-run) write raw.document + stg.court_case inside ingest_run."""
from __future__ import annotations

from dataclasses import asdict

from src.common.http import PoliteClient, SourceBlocked
from src.common.ingest_run import ingest_run
from src.common.raw_writer import write_document

from . import claims_agents, courtlistener, elevenflo
from .base import SourceResult

SOURCES = ("courtlistener", "stretto", "epiq", "elevenflo", "court_manual")

UPSERT = """
insert into stg.court_case
  (source_url, court, docket_no, case_name, chapter, filed_date, terminated_date, debtor_name, claims_agent_url, courtlistener_id)
values (%(source_url)s, %(court)s, %(docket_no)s, %(case_name)s, %(chapter)s, %(filed_date)s, %(terminated_date)s,
        %(debtor_name)s, %(claims_agent_url)s, %(courtlistener_id)s)
on conflict (court, docket_no) do update set
  -- the first link is kept, except that a CourtListener docket link always replaces a non-CourtListener one
  source_url       = case when excluded.source_url like 'https://www.courtlistener.com/docket/%%'
                           and stg.court_case.source_url not like 'https://www.courtlistener.com/docket/%%'
                          then excluded.source_url else stg.court_case.source_url end,
  case_name        = coalesce(excluded.case_name, stg.court_case.case_name),
  chapter          = coalesce(excluded.chapter, stg.court_case.chapter),
  filed_date       = coalesce(excluded.filed_date, stg.court_case.filed_date),
  terminated_date  = coalesce(excluded.terminated_date, stg.court_case.terminated_date),
  debtor_name      = coalesce(excluded.debtor_name, stg.court_case.debtor_name),
  claims_agent_url = coalesce(excluded.claims_agent_url, stg.court_case.claims_agent_url),
  courtlistener_id = coalesce(excluded.courtlistener_id, stg.court_case.courtlistener_id)
returning (xmax = 0) as inserted
"""


def manual_cases() -> SourceResult:
    """Hand-entered PUBLIC bankruptcy filings (config/court_facts.yaml `cases`): a case our court sources have not
    picked up yet, entered from a public docket listing (court, case number, debtor, chapter, filing date, link)."""
    from datetime import date as _date

    from src.score import rules
    from .base import CaseRecord
    res = SourceResult("court_manual")
    for c in rules.court_facts().get("cases") or []:
        if not str(c.get("source_url", "")).startswith("http"):
            raise ValueError(f"court_facts.yaml cases: public source_url missing for {c.get('debtor_name')}")
        fd = c.get("filed_date")
        res.cases.append(CaseRecord("court_manual", c["source_url"], c["court"], c["docket_no"], c.get("case_name") or c["debtor_name"],
                                    str(c.get("chapter") or "") or None, fd if isinstance(fd, _date) or fd is None else _date.fromisoformat(fd),
                                    None, c["debtor_name"], c.get("claims_agent_url")))
    return res


def fetch(source_id: str, limit: int | None = None, **cl_kwargs) -> SourceResult:
    if source_id == "courtlistener":
        return courtlistener.run(limit, **cl_kwargs)
    if source_id == "court_manual":
        return manual_cases()
    with PoliteClient(source_id) as client:
        fn = {"stretto": claims_agents.fetch_stretto, "epiq": claims_agents.fetch_epiq,
              "elevenflo": elevenflo.fetch_elevenflo}[source_id]
        return fn(client, limit)


def validate(res: SourceResult) -> list[str]:
    problems = []
    seen = set()
    for c in res.cases:
        if not (c.court and c.docket_no and c.source_url):
            problems.append(f"missing key field: {c}")
        if (c.court, c.docket_no) in seen:
            problems.append(f"duplicate case key {(c.court, c.docket_no)}")
        seen.add((c.court, c.docket_no))
    urls = [d.source_url for d in res.docs]
    if len(urls) != len(set(urls)):
        problems.append("duplicate document URLs")
    return problems


def load_source(conn, source_id: str, res: SourceResult) -> dict:
    with ingest_run(conn, source_id) as run:
        new_docs = 0
        for d in res.docs:
            if write_document(conn, source_id, run.run_id, d.source_url, title=d.title, body_text=d.body_text,
                              published_at=d.published_at, buyer_evidence=d.buyer_evidence):
                new_docs += 1
        new_cases = upd = 0
        with conn.cursor() as cur:
            for c in res.cases:
                cur.execute(UPSERT, asdict(c))
                if cur.fetchone()["inserted"]:
                    new_cases += 1
                else:
                    upd += 1
        run.rows_in = new_docs + new_cases
    return {"new_docs": new_docs, "new_cases": new_cases, "updated_cases": upd}


UPSERT_BATCH = UPSERT.replace("returning (xmax = 0) as inserted", "")


def load_courtlistener_search_only(conn, limit=None, queries=None) -> dict:
    """Search-only run: every search page's cases are upserted and COMMITTED as they arrive (one batched round trip
    per page), so an interrupted run keeps what it fetched. One raw.ingest_run row covers the whole run."""
    written = 0
    with ingest_run(conn, "courtlistener") as run:
        def save(cases):
            nonlocal written
            with conn.cursor() as cur:
                cur.executemany(UPSERT_BATCH, [asdict(c) for c in cases])
            conn.commit()
            written += len(cases)
            run.rows_in = written
        res = courtlistener.run(limit, search_only=True, on_cases=save, queries=queries)
    return {"cases_upserted": written, "note": res.note}


def run_all(sources, limit=None, dry_run=False, search_only=False, queries=None) -> int:
    conn = None
    if not dry_run:
        from src.common.db import connect
        conn = connect()
    exit_code = 0
    try:
        for sid in sources:
            try:
                if sid == "courtlistener" and search_only and not dry_run:
                    print(f"[{sid}] search-only: {load_courtlistener_search_only(conn, limit, queries)}")
                    continue
                res = fetch(sid, limit, **({"search_only": True} if sid == "courtlistener" and search_only else {}))
            except SourceBlocked as exc:
                print(f"[{sid}] BLOCKED, mark manual: {exc}")
                continue
            if res.note and not res.cases and not res.docs:
                print(f"[{sid}] {res.note}")
                continue
            problems = validate(res)
            ev = sum(1 for d in res.docs if d.buyer_evidence)
            print(f"[{sid}] cases={len(res.cases)} documents={len(res.docs)} buyer_evidence={ev} skipped={res.skipped}"
                  + (f" note={res.note}" if res.note else ""))
            for p in problems:
                print(f"  VALIDATION: {p}")
                exit_code = 1
            if dry_run:
                for c in res.cases[:3]:
                    print(f"  sample: {c.court} | {c.docket_no} | {c.case_name} | filed={c.filed_date} | {c.source_url}")
                if not res.cases:
                    for d in res.docs[:3]:
                        print(f"  sample doc: {d.title} | {d.source_url}")
            elif not problems:
                print(f"  wrote: {load_source(conn, sid, res)}")
    finally:
        if conn:
            conn.close()
    return exit_code
