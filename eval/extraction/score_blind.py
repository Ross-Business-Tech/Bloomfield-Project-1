"""Score the AI's stored extractions against a teammate's blind labels (CSV). Usage:
python -m eval.extraction.score_blind eval/extraction/blind_set_2/labels_<initials>.csv"""
from __future__ import annotations

import csv
import sys
from collections import Counter

from eval.extraction.scorer import LIST_KEYS, field_correct, norm_text
from src.common.db import connect
from src.extract.schema import FIELDS, NOT_FOUND


def to_expected(row: dict) -> dict:
    exp = {}
    for f in FIELDS:
        v = (row.get(f) or "").strip() or NOT_FOUND
        if f in LIST_KEYS:
            exp[f] = NOT_FOUND if v == NOT_FOUND else {"required": [x.strip() for x in v.split(";") if x.strip()], "optional": []}
        else:
            exp[f] = v
    return exp


def main(path: str) -> int:
    rows = [r for r in csv.DictReader(open(path, encoding="utf-8")) if r.get("doc_id")]
    unfilled = [r["n"] for r in rows if not any((r.get(f) or "").strip() for f in FIELDS)]
    if unfilled:
        print(f"Rows not labeled yet: {', '.join(unfilled)}"); return 2
    ids = [int(r["doc_id"]) for r in rows]
    with connect() as c:
        pred = {r["document_id"]: r["fields"] for r in c.execute(
            "select document_id, fields from stg.extraction where document_id = any(%s) and fields->>'status' = 'extracted'", (ids,))}
        text = {r["id"]: f"{r['title']}\n{r['body_text'] or ''}" for r in c.execute(
            "select id, title, body_text from raw.document where id = any(%s)", (ids,))}
    per_field, full, buyer_ok, invented = Counter(), 0, 0, []
    for r in rows:
        d = int(r["doc_id"]); exp = to_expected(r); p = pred.get(d, {})
        misses = [f for f in FIELDS if not field_correct(f, exp[f], p.get(f, NOT_FOUND), text.get(d, ""))]
        per_field.update(f for f in FIELDS if f not in misses)
        full += not misses
        buyer_ok += "buyer_or_new_operator" not in misses
        b = p.get("buyer_or_new_operator", NOT_FOUND)
        if b != NOT_FOUND and any(norm_text(x) not in norm_text(text.get(d, "")) for x in b.split(";") if x.strip()):
            invented.append((r["n"], b))
        print(f"#{r['n']:>2} doc {d}: {'OK' if not misses else 'miss: ' + ', '.join(misses)}")
    n = len(rows)
    print(f"\nFully correct: {full}/{n} (need >= 18)")
    print(f"Buyer accuracy: {buyer_ok}/{n}")
    print("Buyer names from the AI that are not in the article text:", invented or "none")
    print("Per-field accuracy:")
    for f in FIELDS:
        print(f"  {f:24} {per_field[f]}/{n}")
    return 0 if full >= 18 else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
