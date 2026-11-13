"""Pre-flight check for any run that may publish: the private inputs must be present and non-empty BEFORE any
work starts, otherwise the run fails and nothing is uploaded.

    python -m src.pipeline.preflight        # prints PASS / FAIL per input (never a value), exit 0 / 2

Each input is read from the environment first (GitHub Actions / Render secrets), then from the laptop's file:

  past-deal exclusion list  EXCLUDE_FACILITIES (the ids, one per line or comma-separated) or EXCLUDE_FACILITIES_PATH,
                            else eval/private/exclude_facilities_PRIVATE.txt
  private term list         PRIVATE_TERMS_PATH, else eval/private/forbidden_terms.txt
  Render API key            RENDER_API_KEY, else the same name in .env
  court-case hand labels    FAMILIES_CLASSIFICATION_CSV, else eval/private/cl_classified.csv

Without the exclusion list, past-deal homes would be published; without the term list the leak check cannot run.
Values are never printed or logged: only names, counts and where the value came from.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from src.common.config import PROJECT_ROOT

EXCLUDE_FILE = PROJECT_ROOT / "eval" / "private" / "exclude_facilities_PRIVATE.txt"
TERMS_FILE = PROJECT_ROOT / "eval" / "private" / "forbidden_terms.txt"
ENV_FILE = PROJECT_ROOT / ".env"
LABELS_FILE = PROJECT_ROOT / "eval" / "private" / "cl_classified.csv"


def _lines(text: str) -> list[str]:
    """Entries of a list: one per line or comma-separated; a line starting with # is a comment (commas and all)."""
    keep = [ln for ln in (text or "").splitlines() if ln.strip() and not ln.strip().startswith("#")]
    return [x.strip() for ln in keep for x in ln.split(",") if x.strip()]


def exclusion_ids(env=None) -> tuple[list[str], str]:
    """(ids, where they came from). Empty list = not available."""
    env = os.environ if env is None else env
    if (env.get("EXCLUDE_FACILITIES") or "").strip():
        return _lines(env["EXCLUDE_FACILITIES"]), "EXCLUDE_FACILITIES"
    p = Path(env["EXCLUDE_FACILITIES_PATH"]) if env.get("EXCLUDE_FACILITIES_PATH") else EXCLUDE_FILE
    if p.is_file():
        return _lines(p.read_text(encoding="utf-8")), "EXCLUDE_FACILITIES_PATH" if env.get("EXCLUDE_FACILITIES_PATH") else "file"
    return [], "missing"


def terms_path(env=None) -> Path:
    env = os.environ if env is None else env
    return Path(env["PRIVATE_TERMS_PATH"]) if env.get("PRIVATE_TERMS_PATH") else TERMS_FILE


def term_count(env=None) -> tuple[int, str]:
    env = os.environ if env is None else env
    p = terms_path(env)
    if not p.is_file():
        return 0, "missing"
    n = sum(1 for x in p.read_text(encoding="utf-8").splitlines()
            if x.strip() and not x.strip().startswith(("#", "!")))
    return n, "PRIVATE_TERMS_PATH" if env.get("PRIVATE_TERMS_PATH") else "file"


def render_key(env=None) -> tuple[str | None, str]:
    env = os.environ if env is None else env
    if (env.get("RENDER_API_KEY") or "").strip():
        return env["RENDER_API_KEY"].strip(), "environment"
    if ENV_FILE.is_file():
        from dotenv import dotenv_values
        v = (dotenv_values(ENV_FILE).get("RENDER_API_KEY") or "").strip()
        if v:
            return v, ".env"
    return None, "missing"


def case_labels(env=None) -> tuple[int, str]:
    """Hand labels of court cases (senior housing: yes / maybe / no). A live run rebuilds the case families from
    them; without the file the families, and with them the leads, would change."""
    env = os.environ if env is None else env
    p = Path(env["FAMILIES_CLASSIFICATION_CSV"]) if env.get("FAMILIES_CLASSIFICATION_CSV") else LABELS_FILE
    if not p.is_file():
        return 0, "missing"
    n = max(0, sum(1 for x in p.read_text(encoding="utf-8").splitlines() if x.strip()) - 1)
    return n, "FAMILIES_CLASSIFICATION_CSV" if env.get("FAMILIES_CLASSIFICATION_CSV") else "file"


def check(env=None) -> list[dict]:
    """One row per input: {name, pass, detail}. The detail never holds a value."""
    ids, id_src = exclusion_ids(env)
    n_terms, t_src = term_count(env)
    key, k_src = render_key(env)
    n_labels, l_src = case_labels(env)
    return [
        {"name": "case_labels", "pass": n_labels > 0,
         "detail": f"{n_labels} hand labels ({l_src})" if n_labels else "missing or empty: a live run would rebuild the court-case families differently"},
        {"name": "exclusion_list", "pass": bool(ids),
         "detail": f"{len(ids)} entries ({id_src})" if ids else "missing or empty: past-deal homes would be published"},
        {"name": "private_terms", "pass": n_terms > 0,
         "detail": f"{n_terms} terms ({t_src})" if n_terms else "missing or empty: the leak check cannot run"},
        {"name": "render_key", "pass": bool(key),
         "detail": f"set ({k_src})" if key else "missing or empty: nothing can be uploaded"},
    ]


def problems(env=None) -> list[str]:
    return [f"{r['name']}: {r['detail']}" for r in check(env) if not r["pass"]]


def main() -> int:
    rows = check()
    for r in rows:
        print(f"   {'PASS' if r['pass'] else 'FAIL'}  {r['name']:15} {r['detail']}")
    return 0 if all(r["pass"] for r in rows) else 2


if __name__ == "__main__":
    sys.exit(main())
