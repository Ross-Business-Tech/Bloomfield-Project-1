"""Public-repo log rule: what a workflow prints must be counts and status only.

    python -m src.pipeline.log_check FILE [--snapshot JSON ...]     # exit 0 = safe to print, 3 = withheld

FILE is the text about to be written to the (public) Actions log. It is refused when it contains:
  - the name of any building or buyer in the given snapshots (leads and watch list),
  - the name of an excluded building (looked up in the database when a connection is available),
  - a private term, or an id from the exclusion list.
Only the number of problems is reported, never the matching text.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

MIN_LEN = 6                      # shorter names ("Elm") would match ordinary words


def names_from(snap: dict) -> set[str]:
    out = set()
    for key in ("items", "explore"):
        for r in snap.get(key) or []:
            for f in ("property", "buyer"):
                v = (r.get(f) or "").strip().lower()
                if len(v) >= MIN_LEN and v != "not found":
                    out.add(v)
    for r in snap.get("list_c") or []:
        v = (r.get("buyer") or "").strip().lower()
        if len(v) >= MIN_LEN:
            out.add(v)
    return out


def problems(text: str, names: set[str], terms: list[str], ids: list[str]) -> dict:
    low = text.lower()
    return {"names": sum(1 for n in names if n in low), "terms": sum(1 for t in terms if t and t.lower() in low),
            "ids": sum(1 for i in ids if i and i.lower() in low),
            "addresses": len(re.findall(r"\b\d{2,6} [a-z0-9 .]{3,30} (street|st|avenue|ave|road|rd|drive|dr|boulevard|blvd|lane|ln)\b", low))}


def excluded_names(ids: list[str]) -> set[str]:
    if not ids:
        return set()
    try:
        from src.common.db import connect
        with connect() as conn:
            return {r["n"].strip().lower() for r in conn.execute(
                "select canonical_name n from core.facility where facility_id::text = any(%s)", (ids,)).fetchall()
                if r["n"] and len(r["n"].strip()) >= MIN_LEN}
    except Exception:                       # no database here: the snapshot names and the ids are still checked
        return set()


def main(argv=None) -> int:
    from src.pipeline import preflight
    ap = argparse.ArgumentParser(prog="python -m src.pipeline.log_check")
    ap.add_argument("file", type=Path)
    ap.add_argument("--snapshot", type=Path, action="append", default=[])
    a = ap.parse_args(argv)
    names: set[str] = set()
    for p in a.snapshot:
        if p.is_file():
            names |= names_from(json.loads(p.read_text()))
    ids = preflight.exclusion_ids()[0]
    names |= excluded_names(ids)
    tp = preflight.terms_path()
    terms = [x.strip() for x in tp.read_text(encoding="utf-8").splitlines()
             if x.strip() and not x.strip().startswith(("#", "!"))] if tp.is_file() else []
    found = problems(a.file.read_text(encoding="utf-8", errors="replace"), names, terms, ids)
    bad = {k: v for k, v in found.items() if v}
    if bad:
        print(f"log check: WITHHELD ({json.dumps(bad)} found in the text; nothing printed). Names checked: {len(names)}")
        return 3
    print(f"log check: clean ({len(names)} building / buyer names, {len(terms)} private terms, {len(ids)} excluded ids checked)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
