"""Leak guard: fail when a change adds keys, email addresses, *_PRIVATE* files or private terms.

    python3 tools/leak_guard.py --staged                  # pre-commit hook (.githooks/pre-commit)
    python3 tools/leak_guard.py --range BASE...HEAD       # CI on every pull request

Generic rules (always on, safe to publish): API / database keys, email addresses, files named *_PRIVATE*.
Private rules: eval/private/forbidden_terms.txt (gitignored, so the list itself stays private); one term per line,
case-insensitive, '#' = comment. A line starting with '!' is an allowed phrase: an added line containing it is not
checked against the private terms (for known public data that happens to share a word). CI has no private list.
Only ADDED lines are checked. Standard library only.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TERMS_FILE = ROOT / "eval" / "private" / "forbidden_terms.txt"

KEY_RULES = [
    ("Anthropic key", r"sk-ant-[A-Za-z0-9_-]{20,}"),
    ("OpenAI / OpenRouter-style key", r"\bsk-(?:or-|proj-)?[A-Za-z0-9_-]{20,}"),
    ("Supabase secret / publishable key", r"\bsb_(?:secret|publishable)_[A-Za-z0-9_-]{16,}"),
    ("JWT (Supabase anon / service_role key)", r"\beyJ[A-Za-z0-9_-]{15,}\.eyJ[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{10,}"),
    ("database URL with password", r"postgres(?:ql)?://[^\s:/@'\"]+:[^\s@'\"]+@"),
    ("Google API key", r"\bAIza[0-9A-Za-z_-]{35}\b"),
    ("GitHub token", r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})"),
    ("AWS access key", r"\bAKIA[0-9A-Z]{16}\b"),
    ("private key block", r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    ("secret assignment", r"(?i)\b(?:api[_-]?key|secret|password|service_role(?:_key)?|token)\s*[:=]\s*['\"][A-Za-z0-9_\-./+]{16,}['\"]"),
]
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
EMAIL_OK = re.compile(r"(?i)@(?:users\.noreply\.github\.com|example\.(?:com|org|net)|anthropic\.com)$|^noreply@")
PRIVATE_NAME = re.compile(r"_PRIVATE", re.I)


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, check=True, capture_output=True, text=True).stdout


def load_private_terms() -> tuple[list[str], list[str]]:
    if not TERMS_FILE.exists():
        return [], []
    terms, allow = [], []
    for line in TERMS_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        (allow if line.startswith("!") else terms).append(line.lstrip("!").strip().lower())
    return terms, allow


def added_lines(diff: str):
    """(path, line number, text) for every added line of a unified diff (-U0)."""
    path, n = None, 0
    for raw in diff.splitlines():
        if raw.startswith("+++ "):
            path = None if raw[4:] == "/dev/null" else raw[6:] if raw.startswith("+++ b/") else raw[4:]
        elif raw.startswith("@@"):
            m = re.search(r"\+(\d+)", raw)
            n = int(m.group(1)) if m else 0
        elif raw.startswith("+") and path:
            yield path, n, raw[1:]
            n += 1


def check(diff: str, names: list[str], terms: list[str], allow: list[str]) -> list[str]:
    problems = []
    for name in names:
        if PRIVATE_NAME.search(Path(name).name):
            problems.append(f"{name}: file name contains _PRIVATE (never commit these)")
    rules = [(label, re.compile(rx)) for label, rx in KEY_RULES]
    for path, n, text in added_lines(diff):
        if path.startswith("tools/leak_guard"):          # this file's own patterns
            continue
        where = f"{path}:{n}"
        for label, rx in rules:
            if rx.search(text):
                problems.append(f"{where}: looks like a {label}")
        for m in EMAIL.finditer(text):
            if not EMAIL_OK.search(m.group(0)) and not re.search(r"\.(?:png|jpe?g|svg|gif|webp)$", m.group(0), re.I):
                problems.append(f"{where}: email address")
        low = text.lower()
        if terms and not any(a in low for a in allow):
            for i, t in enumerate(terms, 1):
                if t in low:
                    problems.append(f"{where}: private term #{i} from eval/private/forbidden_terms.txt")
    return problems


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python3 tools/leak_guard.py")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--staged", action="store_true", help="check what is staged for the next commit")
    g.add_argument("--range", help="check a commit range, e.g. origin/development...HEAD")
    ap.add_argument("--no-private-terms", action="store_true", help="generic rules only (what CI runs)")
    a = ap.parse_args(argv)
    spec = ["--cached"] if a.staged else [a.range]
    diff = git("diff", "-U0", "--no-color", "--no-ext-diff", *spec)
    names = [x for x in git("diff", "--name-only", "--diff-filter=ACR", *spec).splitlines() if x]
    terms, allow = ([], []) if a.no_private_terms else load_private_terms()
    problems = check(diff, names, terms, allow)
    mode = "generic rules only" if not terms else f"generic rules + {len(terms)} private terms"
    if problems:
        print(f"LEAK GUARD: {len(problems)} problem(s) ({mode}). Nothing was committed / this check fails:")
        for p in problems:
            print("  ✖", p)
        print("Remove them. For a false alarm (e.g. public data), ask the maintainer in the pull request.")
        return 1
    print(f"leak guard: clean ({mode}, {len(names)} file(s) checked)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
