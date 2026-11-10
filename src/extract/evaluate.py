"""Run the model on the 20 hand-labeled eval cases and score them (eval/extraction/cases.json).

Uses the same local cache as the full run, so re-scoring an unchanged prompt costs nothing.
Writes eval/extraction/results/<model>__<prompt_version>.json.
"""
from __future__ import annotations

import json
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

from src.common.config import PROJECT_ROOT

from . import client as cl
from .prompt import PROMPT_VERSION
from .schema import postprocess

EVAL_DIR = PROJECT_ROOT / "eval" / "extraction"


def load_cases() -> list[dict]:
    return json.loads((EVAL_DIR / "cases.json").read_text(encoding="utf-8"))["cases"]


def _doc_texts(cases: list[dict]) -> dict[int, dict]:
    """Full text per case: stored in cases.json when short, else read from raw.document by id."""
    missing = [c["doc_id"] for c in cases if not c.get("text")]
    out = {c["doc_id"]: {"title": c["title"], "body": c["text"], "content_hash": c["content_hash"]}
           for c in cases if c.get("text")}
    if missing:
        from src.common.db import connect
        with connect() as conn:
            for r in conn.execute("select id, title, body_text, content_hash from raw.document where id = any(%s)",
                                  (missing,)).fetchall():
                out[r["id"]] = {"title": r["title"], "body": r["body_text"], "content_hash": r["content_hash"]}
    return out


def run_eval(model: str = cl.DEFAULT_MODEL, log=print) -> dict:
    from eval.extraction.scorer import score_case

    cases = load_cases()
    texts = _doc_texts(cases)
    spend = cl.Spend(model=model)
    api = None

    def get(case):
        t = texts[case["doc_id"]]
        raw = cl.cache_get(model, t["content_hash"])
        if raw is None:
            nonlocal api
            api = api or cl.client()
            raw = cl.extract_one(api, model, t["title"], t["body"], spend)
            cl.cache_put(model, t["content_hash"], raw)
        return case, raw

    with ThreadPoolExecutor(max_workers=5) as pool:
        outputs = list(pool.map(get, cases))

    per_case, misses = [], Counter()
    for case, raw in outputs:
        t = texts[case["doc_id"]]
        doc_text = f"{t['title'] or ''}\n{t['body'] or ''}"
        fields, _ = postprocess(raw, doc_text)
        s = score_case(case, fields, doc_text)
        s["predicted"] = {k: v for k, v in fields.items()}
        per_case.append(s)
        misses.update(s["misses"])
        log(f"  doc {case['doc_id']:>5} {'OK ' if s['fully_correct'] else 'MISS'} {', '.join(s['misses'])}")
    correct = sum(s["fully_correct"] for s in per_case)
    summary = {"model": model, "prompt_version": PROMPT_VERSION, "fully_correct": correct, "total": len(per_case),
               "field_misses": dict(misses), "api_usd_this_run": round(spend.usd, 4), "cases": per_case}
    out = EVAL_DIR / "results" / f"{model}__{PROMPT_VERSION}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, indent=1, ensure_ascii=False), encoding="utf-8")
    log(f"{model} {PROMPT_VERSION}: {correct}/{len(per_case)} fully correct; field misses {dict(misses)}; "
        f"API spend this run ${spend.usd:.4f}")
    return summary
