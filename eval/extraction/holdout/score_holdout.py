"""Blind holdout test for the AI article reader (Gate 3 requirement #1).

python -m eval.extraction.holdout.score_holdout [--models claude-haiku-5-5,claude-sonnet-5-5]

- Labels: eval/extraction/holdout/holdout_labels.json (human-verified). Free-text distress labels are converted with
  crosswalk.json. Article text is fetched politely at run time (fetch.py) and never stored in the repo.
- Same prompt/schema/post-processing as production (src/extract). Model answers are cached under data/raw/cache.
- Scoring rules (meta.scoring in the labels file), with these normalizations, stated so they can be reviewed:
  * names / places: case, punctuation, LLC/Inc/LP/Corp/Co/Ltd/"The", leading "affiliate of"; text in (parentheses)
    in a label is a note and is dropped; several names separated by "; " compare as a set; accept_soft values also
    count (reported separately as "soft"). US state names == postal codes.
  * units / beds: the predicted number must be one of the numbers in the label (the schema holds one property).
  * care_type: label codes via crosswalk; sets must be equal.
  * stage_text: labels paraphrase, the extractor quotes. Correct when the label is NOT FOUND and the model says NOT
    FOUND, or the label has a stage and the model gives a quote that really appears in the article.
  * event_dates: each label date and each predicted date reduce to month/day/year tokens; every label date must be
    matched by a prediction and every prediction by a label date (token subset either way).
  * amounts: every label item's first dollar figure must be predicted; every predicted figure must be in the label.
  * hallucination: label NOT FOUND (and no soft value) but the model filled the field.
- Negatives (N16-N20): only distress_type and expected_stage are scored (pipeline view, see crosswalk.json).
- expected_stage is computed by the pipeline's own code (src.score.rules.news_signal_types), not by the model.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path

from src.extract import client as cl
from src.extract.schema import FIELDS, NOT_FOUND, loose, postprocess
from src.score.rules import news_signal_types

HERE = Path(__file__).parent
STAGE_ORDER = ["early", "middle", "late", "sale"]
PARTY = ["debtor_or_owner", "receiver_or_trustee", "buyer_or_new_operator", "lender"]
TEXT = ["property_name", "street", "city", "state"] + PARTY
STATES = {"alabama": "al", "alaska": "ak", "arizona": "az", "arkansas": "ar", "california": "ca", "colorado": "co",
          "connecticut": "ct", "delaware": "de", "florida": "fl", "georgia": "ga", "hawaii": "hi", "idaho": "id",
          "illinois": "il", "indiana": "in", "iowa": "ia", "kansas": "ks", "kentucky": "ky", "louisiana": "la",
          "maine": "me", "maryland": "md", "massachusetts": "ma", "michigan": "mi", "minnesota": "mn",
          "mississippi": "ms", "missouri": "mo", "montana": "mt", "nebraska": "ne", "nevada": "nv",
          "new hampshire": "nh", "new jersey": "nj", "new mexico": "nm", "new york": "ny", "north carolina": "nc",
          "north dakota": "nd", "ohio": "oh", "oklahoma": "ok", "oregon": "or", "pennsylvania": "pa",
          "rhode island": "ri", "south carolina": "sc", "south dakota": "sd", "tennessee": "tn", "texas": "tx",
          "utah": "ut", "vermont": "vt", "virginia": "va", "washington": "wa", "west virginia": "wv",
          "wisconsin": "wi", "wyoming": "wy"}
MONTHS = {m: m[:3] for m in ["january", "february", "march", "april", "may", "june", "july", "august", "september",
                             "october", "november", "december"]} | {"sept": "sep"}
_MULT = {"thousand": 1e3, "k": 1e3, "million": 1e6, "m": 1e6, "billion": 1e9, "b": 1e9}


# ── normalization ────────────────────────────────────────────────────────────────────────────────────────────────
def _nf(v) -> bool:
    return v in (None, "", NOT_FOUND, [NOT_FOUND]) or (isinstance(v, str) and v.strip().upper().startswith(NOT_FOUND))


def norm_name(s: str) -> str:
    s = re.sub(r"\(.*?\)", " ", s)
    s = re.sub(r"^\s*(an?\s+)?affiliate of\s+", "", s, flags=re.I)
    s = loose(s)
    s = re.sub(r"\b(llc|l l c|inc|lp|l p|corp|corporation|co|ltd|the)\b", " ", s)
    s = " ".join(s.split())
    return STATES.get(s, s)


def name_set(v) -> set[str]:
    if _nf(v):
        return set()
    items = v if isinstance(v, list) else str(v).split(";")
    return {n for n in (norm_name(x) for x in items) if n}


def numbers(s) -> list[float]:
    out = []
    for m in re.finditer(r"\$?\s*(\d[\d,]*(?:\.\d+)?)\s*(thousand|million|billion|k|m|b)?\b", str(s).lower()):
        out.append(round(float(m.group(1).replace(",", "")) * _MULT.get(m.group(2) or "", 1), 2))
    return out


def date_keys(s: str) -> frozenset:
    s = re.sub(r"\(.*?\)", " ", s.lower())
    s = re.sub(r"\b(chapter|ch\.?)\s*\d+", " ", s)          # "Chapter 11" is not a date
    iso = re.search(r"(\d{4})-(\d{2})-(\d{2})", s)
    if iso:
        y, mo, d = iso.groups()
        return frozenset({list(MONTHS.values())[int(mo) - 1], str(int(d)), y})
    toks = re.findall(r"[a-z]+|\d+", s)
    return frozenset(MONTHS.get(t, t) if t in MONTHS else t for t in toks
                     if t in MONTHS or t in MONTHS.values() or t.isdigit())


# ── field checks ─────────────────────────────────────────────────────────────────────────────────────────────────
def check(field, label, soft, pred, text, case_id, xwalk) -> tuple[bool, bool, bool]:
    """→ (correct, via_soft, hallucinated)"""
    soft_vals = soft.get(field, [])
    if field in TEXT:
        p = name_set(pred)
        if _nf(label):
            ok_soft = bool(p) and any(p == name_set(sv) for sv in soft_vals if not _nf(sv))
            return (not p) or ok_soft, ok_soft, bool(p) and not ok_soft
        if p == name_set(label):
            return True, False, False
        ok_soft = bool(p) and any(p == name_set(sv) for sv in soft_vals)
        return ok_soft, ok_soft, False
    if field in ("units", "beds"):
        p = numbers(pred) if not _nf(pred) else []
        if _nf(label):
            soft_n = [n for sv in soft_vals for n in numbers(sv)]
            return not p or (p and p[0] in soft_n), bool(p), bool(p) and not (p[0] in soft_n)
        allowed = numbers(label)
        if p and p[0] in allowed:
            return True, False, False
        soft_n = [n for sv in soft_vals for n in numbers(sv)]
        return bool(p) and p[0] in soft_n, bool(p) and p[0] in soft_n, False
    if field == "care_type":
        p = set(pred) if isinstance(pred, list) else set()
        want = {xwalk["care_type_codes"].get(c, c) for c in (label if isinstance(label, list) else [])}
        return p == want, False, bool(p) and not want
    if field == "distress_type":
        p = set(pred) if isinstance(pred, list) else set()
        rule = xwalk["distress"].get(case_id)
        if rule is None:   # negatives: pipeline view
            return not (p - {"sale", "operator_change"}), False, bool(p - {"sale", "operator_change"})
        req, allowed = set(rule.get("required", [])), set(rule.get("allowed", []))
        any_of = set(rule.get("any_of", []))
        ok = req <= p and (not any_of or bool(p & any_of)) and p <= (req | allowed | any_of)
        return ok, False, False
    if field == "stage_text":
        if _nf(label):
            return _nf(pred), False, not _nf(pred)
        return (not _nf(pred)) and loose(str(pred)).strip() in loose(text), False, False
    if field == "event_dates":
        p = [date_keys(d.get("date_text", "")) for d in pred] if isinstance(pred, list) else []
        p = [k for k in p if k]
        lab = [date_keys(x) for x in (label if isinstance(label, list) else [])] if not _nf(label) else []
        lab = [k for k in lab if k]
        if not lab:
            return not p, False, bool(p)
        covered = all(any(lk <= pk or pk <= lk for pk in p) for lk in lab)
        no_extra = all(any(lk <= pk or pk <= lk for lk in lab) for pk in p)
        return covered and no_extra, False, False
    if field == "amounts":
        p = [n for a in pred for n in numbers(a.get("amount_text", ""))[:1]] if isinstance(pred, list) else []
        if _nf(label):
            return not p, False, bool(p)
        items = label if isinstance(label, list) else [label]
        lab_all = {n for x in items for n in numbers(x)}
        firsts = [numbers(x)[0] for x in items if numbers(x)]
        return all(f in p for f in firsts) and all(n in lab_all for n in p), False, False
    raise ValueError(field)


def pipeline_stage(distress, pred=None) -> str:
    pred = pred or {}
    stages = [st for t, st in news_signal_types(distress if isinstance(distress, list) else [],
                                                pred.get("buyer_or_new_operator"), pred.get("lender"),
                                                pred.get("stage_text")) if t != "market_sale"]
    return max(stages, key=STAGE_ORDER.index) if stages else NOT_FOUND


# ── run ──────────────────────────────────────────────────────────────────────────────────────────────────────────
def extract(api, model, doc, spend) -> dict:
    h = hashlib.sha256(f"{doc['title']}\n{doc['body']}".encode()).hexdigest()
    raw = cl.cache_get(model, h)
    if raw is None:
        raw = cl.extract_one(api, model, doc["title"], doc["body"], spend)
        cl.cache_put(model, h, raw)
    fields, _ = postprocess(raw, f"{doc['title']}\n{doc['body']}")
    return fields


def score_model(model, cases, docs, xwalk, api) -> dict:
    spend = cl.Spend(model)
    per_field, soft_hits, halluc, rows = Counter(), Counter(), Counter(), []
    for c in cases:
        if c["id"] not in docs:
            rows.append({"id": c["id"], "status": "not available"}); continue
        doc = docs[c["id"]]; text = f"{doc['title']}\n{doc['body']}"
        pred = extract(api, model, doc, spend)
        soft = c.get("accept_soft", {})
        negative = not c["is_distress"]
        scored = ["distress_type"] if negative else FIELDS
        misses = []
        for f in scored:
            ok, via_soft, hall = check(f, c["labels"].get(f, NOT_FOUND), soft, pred.get(f, NOT_FOUND), text, c["id"], xwalk)
            per_field[f] += ok; soft_hits[f] += via_soft; halluc[f] += hall
            if not ok:
                misses.append(f)
        stage = pipeline_stage(pred.get("distress_type"), pred)
        stage_ok = stage == c["expected_stage"]
        if not stage_ok:
            misses.append("expected_stage")
        rows.append({"id": c["id"], "status": "scored", "correct": not misses, "misses": misses,
                     "stage": stage, "expected_stage": c["expected_stage"],
                     "buyer_pred": pred.get("buyer_or_new_operator"), "buyer_label": c["labels"].get("buyer_or_new_operator"),
                     "distress_pred": pred.get("distress_type")})
    scored_rows = [r for r in rows if r["status"] == "scored"]
    return {"model": model, "rows": rows, "per_field": per_field, "soft": soft_hits, "halluc": halluc,
            "n_scored": len(scored_rows), "n_correct": sum(r["correct"] for r in scored_rows),
            "n_distress_scored": sum(1 for r in scored_rows if r["id"].startswith("H")),
            "usd": spend.usd if spend else None}


def report(res: dict, cases) -> str:
    n, nd = res["n_scored"], res["n_distress_scored"]
    core = [r for r in res["rows"] if r["status"] == "scored"]
    core_ok = sum(1 for r in core if not [m for m in r["misses"] if m not in ("event_dates", "amounts", "stage_text")])
    lines = [f"## {res['model']}",
             f"Articles fully correct: **{res['n_correct']}/{n} scored** ({20 - n} not available) — pass bar 18/20 and 0 buyer hallucinations",
             f"Fully correct ignoring event_dates/amounts/stage_text: {core_ok}/{n}",
             f"Buyer accuracy: {res['per_field']['buyer_or_new_operator']}/{nd} distress articles · "
             f"buyer hallucinations: **{res['halluc']['buyer_or_new_operator']}**",
             f"Hallucinations (all fields): {sum(res['halluc'].values())} {dict(+res['halluc'])}",
             f"Stage computed in code correct: {sum(1 for r in core if 'expected_stage' not in r['misses'])}/{n}",
             "", "| Field | Correct | via soft |", "|---|---|---|"]
    for f in FIELDS:
        denom = n if f == "distress_type" else nd
        lines.append(f"| {f} | {res['per_field'][f]}/{denom} | {res['soft'][f]} |")
    lines += ["", "| Case | Result | Misses | Stage (code / label) | Buyer (AI / label) |", "|---|---|---|---|---|"]
    for r in res["rows"]:
        if r["status"] != "scored":
            lines.append(f"| {r['id']} | not available | | | |"); continue
        lines.append(f"| {r['id']} | {'✅' if r['correct'] else '❌'} | {', '.join(r['misses'])} | {r['stage']} / "
                     f"{r['expected_stage']} | {r['buyer_pred']} / {r['buyer_label']} |")
    if res.get("usd") is not None:
        lines.append(f"\nAPI cost this run: ${res['usd']:.3f}")
    return "\n".join(lines)


def main(argv=None) -> int:
    from eval.extraction.holdout.fetch import fetch_all
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", default="claude-haiku-5-5,claude-sonnet-5-5")
    args = ap.parse_args(argv)
    data = json.loads((HERE / "holdout_labels.json").read_text())
    if data["meta"].get("human_verified") is not True:
        print("holdout_labels.json is not marked human_verified: true — not scoring."); return 2
    xwalk = json.loads((HERE / "crosswalk.json").read_text())
    docs = fetch_all(log=lambda m: None)
    api = cl.client()
    out = [f"# Holdout extraction test ({data['meta']['name']})",
           f"Labels human-verified (Aiden, 2026-11-10). Articles fetched: {len(docs)}/20."]
    passed = False
    for model in args.models.split(","):
        res = score_model(model.strip(), data["cases"], docs, xwalk, api)
        out.append(report(res, data["cases"]))
        passed |= res["n_correct"] >= 18 and res["halluc"]["buyer_or_new_operator"] == 0
    text = "\n\n".join(out)
    (Path("eval/reports") / "holdout_extraction.md").write_text(text + "\n")
    print(text)
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
