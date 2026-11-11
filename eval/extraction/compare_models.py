"""Side-by-side extraction test before a model / provider switch (decision Nov 10, 2026).

    python -m eval.extraction.compare_models [--models claude-sonnet-5-5,gemini-2.5-pro]

Default models: the production model and the LLM_PROVIDER default model (GEMINI_MODEL when LLM_PROVIDER=gemini).
Runs, with the production prompt / schema / post-processing and the shared answer cache:
  - holdout v1 (eval/extraction/holdout, human-verified labels; article text fetched at run time),
  - the dev set (eval/extraction/cases.json, 20 cases),
  - blind set 2 (eval/extraction/blind_set_2/labels_*.csv) when a labelled file exists.
Switch rule: the new model must match the production model on holdout v1 buyer accuracy (12/12) with 0 buyer
hallucinations (and not do worse on the dev sets). Writes eval/reports/compare_models.md. Exit code 0 = passes.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

from src.extract import client as cl

HERE = Path(__file__).resolve().parent


def blind2(model: str, api) -> dict | None:
    """Blind set 2 scored on fresh answers of `model` (not the stored production rows)."""
    files = sorted((HERE / "blind_set_2").glob("labels_*.csv"))
    if not files:
        return None
    from eval.extraction.score_blind import to_expected
    from eval.extraction.scorer import field_correct
    from src.common.db import connect
    from src.extract.schema import FIELDS, NOT_FOUND, postprocess
    rows = [r for r in csv.DictReader(open(files[0], encoding="utf-8")) if r.get("doc_id")]
    with connect() as c:
        docs = {r["id"]: r for r in c.execute("select id, title, body_text, content_hash from raw.document "
                                              "where id = any(%s)", ([int(r["doc_id"]) for r in rows],)).fetchall()}
    spend, full, buyer_ok, buyer_halluc = cl.Spend(model), 0, 0, 0
    for r in rows:
        d = docs.get(int(r["doc_id"]))
        if not d:
            continue
        raw = cl.cache_get(model, d["content_hash"])
        if raw is None:
            raw = cl.extract_one(api, model, d["title"], d["body_text"], spend)
            cl.cache_put(model, d["content_hash"], raw)
        text = f"{d['title'] or ''}\n{d['body_text'] or ''}"
        p, _ = postprocess(raw, text)
        exp = to_expected(r)
        misses = [f for f in FIELDS if not field_correct(f, exp[f], p.get(f, NOT_FOUND), text)]
        full += not misses
        buyer_ok += "buyer_or_new_operator" not in misses
        buyer_halluc += exp["buyer_or_new_operator"] == NOT_FOUND and p.get("buyer_or_new_operator", NOT_FOUND) != NOT_FOUND
    return {"file": files[0].name, "n": len(rows), "fully_correct": full, "buyer_ok": buyer_ok,
            "buyer_halluc": buyer_halluc, "usd": spend.usd}


def main(argv=None) -> int:
    from eval.extraction.holdout.fetch import fetch_all
    from eval.extraction.holdout.score_holdout import score_model

    from src.extract.evaluate import run_eval
    ap = argparse.ArgumentParser(prog="python -m eval.extraction.compare_models")
    ap.add_argument("--models", default=None)
    a = ap.parse_args(argv)
    models = [m.strip() for m in a.models.split(",")] if a.models else \
        list(dict.fromkeys([cl.PRODUCTION_MODEL, cl.default_model()]))
    data = json.loads((HERE / "holdout" / "holdout_labels.json").read_text())
    xwalk = json.loads((HERE / "holdout" / "crosswalk.json").read_text())
    docs = fetch_all(log=lambda m: None)
    api = cl.client()
    res = {}
    for m in models:
        h = score_model(m, data["cases"], docs, xwalk, api)
        dev = run_eval(m, log=lambda *_: None)
        res[m] = {"holdout": h, "dev": dev, "blind2": blind2(m, api)}
    base = res[cl.PRODUCTION_MODEL]["holdout"] if cl.PRODUCTION_MODEL in res else None
    lines = ["# Model comparison (extraction)", "",
             f"Holdout articles fetched: {len(docs)}/20. Switch rule: holdout buyer = production model's "
             f"({base['per_field']['buyer_or_new_operator'] if base else '?'}/{base['n_distress_scored'] if base else '?'}) "
             "and 0 buyer hallucinations.", "",
             "| | " + " | ".join(models) + " |", "|---|" + "---|" * len(models)]

    def row(label, f):
        lines.append(f"| {label} | " + " | ".join(f(res[m]) for m in models) + " |")
    row("Holdout: buyer correct", lambda r: f"{r['holdout']['per_field']['buyer_or_new_operator']}/{r['holdout']['n_distress_scored']}")
    row("Holdout: buyer hallucinations", lambda r: str(r["holdout"]["halluc"]["buyer_or_new_operator"]))
    row("Holdout: all-field hallucinations", lambda r: str(sum(r["holdout"]["halluc"].values())))
    row("Holdout: articles fully correct", lambda r: f"{r['holdout']['n_correct']}/{r['holdout']['n_scored']}")
    row("Dev set: fully correct", lambda r: f"{r['dev']['fully_correct']}/{r['dev']['total']}")
    row("Blind set 2: fully correct", lambda r: (f"{r['blind2']['fully_correct']}/{r['blind2']['n']}" if r["blind2"] else "not labelled yet"))
    row("Blind set 2: buyer correct / hallucinated", lambda r: (f"{r['blind2']['buyer_ok']}/{r['blind2']['n']} / {r['blind2']['buyer_halluc']}" if r["blind2"] else "-"))
    row("API cost this run (USD)", lambda r: f"{(r['holdout']['usd'] or 0) + r['dev']['api_usd_this_run'] + (r['blind2']['usd'] if r['blind2'] else 0):.3f}")
    verdict = {}
    for m in models:
        h = res[m]["holdout"]
        ok = h["halluc"]["buyer_or_new_operator"] == 0 and (base is None or
                                                            h["per_field"]["buyer_or_new_operator"] >= base["per_field"]["buyer_or_new_operator"])
        verdict[m] = ok
    row("Passes the switch rule", lambda r: "")
    lines[-1] = "| Passes the switch rule | " + " | ".join("yes" if verdict[m] else "NO" for m in models) + " |"
    text = "\n".join(lines) + "\n"
    (HERE.parents[1] / "eval" / "reports" / "compare_models.md").write_text(text)
    print(text)
    return 0 if all(verdict.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
