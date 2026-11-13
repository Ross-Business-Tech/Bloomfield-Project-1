"""Laptop-only inputs of the daily check, moved to GitHub Actions secrets (nothing private is ever committed).

    python -m src.pipeline.cloud_inputs --pack           # laptop: build the payloads in output/cloud_inputs/ (gitignored)
    python -m src.pipeline.cloud_inputs --pack --set     # ... and store them with `gh secret set` (names + sizes printed)
    python -m src.pipeline.cloud_inputs --restore DIR    # CI: write the files back from the environment, print paths

Each file travels as base64(gzip(file)). GitHub allows 48 KB per secret, so the watch list is split into numbered
parts. Values are never printed: only names, sizes and counts.

  secret                      file on the laptop                              read in CI through
  EXCLUDE_FACILITIES_GZ_B64   eval/private/exclude_facilities_PRIVATE.txt     EXCLUDE_FACILITIES_PATH
  PRIVATE_TERMS_GZ_B64        eval/private/forbidden_terms.txt                PRIVATE_TERMS_PATH
  CASE_LABELS_GZ_B64          eval/private/cl_classified.csv                  FAMILIES_CLASSIFICATION_CSV
  CHANGELOG_BASELINE_B64      output/changelog_baseline_state.json            read directly (already gzip + base64)
  WATCHLIST_GZ_B64_1..N       newest output/INTERNAL_watch_*.csv, public      output/INTERNAL_watch_<date>.csv
                              rows and the columns the site uses only         (a live run then writes a newer one)
  RENDER_API_KEY, ANTHROPIC_API_KEY, SUPABASE_DB_URL, COURTLISTENER_TOKEN     plain values from .env
"""
from __future__ import annotations

import argparse
import base64
import csv
import gzip
import io
import os
import subprocess
import sys
from pathlib import Path

from src.common.config import OUTPUT_DIR, PROJECT_ROOT

PART = 44_000                      # characters per secret (GitHub limit: 48 KB)
MAX_WATCH_PARTS = 12
UNUSED = "WATCHLIST_PART_NOT_USED"
WATCH_COLS = ["property", "city", "state", "care_types", "units", "beds", "distress_type", "stage", "buyer",
              "buyer_source_url", "score", "flags", "source_urls"]          # what src.web.snapshot.watch_rows reads
FILES = {"EXCLUDE_FACILITIES_GZ_B64": ("eval/private/exclude_facilities_PRIVATE.txt", "EXCLUDE_FACILITIES_PATH"),
         "PRIVATE_TERMS_GZ_B64": ("eval/private/forbidden_terms.txt", "PRIVATE_TERMS_PATH"),
         "CASE_LABELS_GZ_B64": ("eval/private/cl_classified.csv", "FAMILIES_CLASSIFICATION_CSV")}
PLAIN = ["RENDER_API_KEY", "ANTHROPIC_API_KEY", "SUPABASE_DB_URL", "COURTLISTENER_TOKEN"]


def pack(data: bytes) -> str:
    return base64.b64encode(gzip.compress(data, 9)).decode()


def unpack(text: str) -> bytes:
    return gzip.decompress(base64.b64decode(text))


def slim_watch(path: Path) -> bytes:
    """Public rows only (anything touched by private evidence is dropped, as the site does), site columns only."""
    out = io.StringIO()
    w = csv.DictWriter(out, fieldnames=WATCH_COLS, extrasaction="ignore", lineterminator="\n")
    w.writeheader()
    for r in csv.DictReader(open(path, encoding="utf-8")):
        if "licensed_private" in (r.get("flags") or "") or "licensed_only" in (r.get("flags") or ""):
            continue
        w.writerow(r)
    return out.getvalue().encode("utf-8")


def payloads() -> dict[str, str]:
    """{secret name: value} for every file input. Raises when a file is missing."""
    out = {name: pack((PROJECT_ROOT / rel).read_bytes()) for name, (rel, _) in FILES.items()}
    out["CHANGELOG_BASELINE_B64"] = pack((OUTPUT_DIR / "changelog_baseline_state.json").read_bytes())
    watch = sorted(OUTPUT_DIR.glob("INTERNAL_watch_*.csv"))[-1]
    blob = pack(slim_watch(watch))
    parts = [blob[i:i + PART] for i in range(0, len(blob), PART)]
    if len(parts) > MAX_WATCH_PARTS:
        raise SystemExit(f"watch list needs {len(parts)} parts (limit {MAX_WATCH_PARTS})")
    for i in range(MAX_WATCH_PARTS):                    # unused parts get a placeholder so an old longer list cannot
        out[f"WATCHLIST_GZ_B64_{i + 1}"] = parts[i] if i < len(parts) else UNUSED     # linger (GitHub masks secret
                                                                                      # values in logs: never a "-")
    out["WATCHLIST_NAME"] = watch.name
    return out


def restore(target: Path, env=None) -> dict[str, str]:
    """Write the file inputs back (CI). Returns {ENV NAME: path} for the caller to export. Missing = not written."""
    env = os.environ if env is None else env
    target.mkdir(parents=True, exist_ok=True)
    paths = {}
    for name, (rel, var) in FILES.items():
        if (env.get(name) or "").strip():
            p = target / Path(rel).name
            p.write_bytes(unpack(env[name]))
            paths[var] = str(p)
    blob = "".join(env.get(f"WATCHLIST_GZ_B64_{i + 1}") or "" for i in range(MAX_WATCH_PARTS)
                   if (env.get(f"WATCHLIST_GZ_B64_{i + 1}") or UNUSED).strip() not in (UNUSED, "-"))
    if blob:
        name = (env.get("WATCHLIST_NAME") or "INTERNAL_watch_2026-11-11.csv").strip()
        if not (name.startswith("INTERNAL_watch_") and name.endswith(".csv") and "/" not in name):
            raise SystemExit("WATCHLIST_NAME is not a watch-list file name")
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        (OUTPUT_DIR / name).write_bytes(unpack(blob))
        paths["WATCHLIST_FILE"] = str(OUTPUT_DIR / name)
    return paths


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m src.pipeline.cloud_inputs", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pack", action="store_true")
    ap.add_argument("--set", action="store_true", help="with --pack: store every secret with `gh secret set`")
    ap.add_argument("--restore", type=Path, default=None)
    a = ap.parse_args(argv)
    if a.restore:
        paths = restore(a.restore)
        for var, p in paths.items():                    # paths only, for $GITHUB_ENV
            print(f"{var}={p}")
        return 0
    if not a.pack:
        ap.error("--pack or --restore")
    secrets = payloads()
    from dotenv import dotenv_values
    env = dotenv_values(PROJECT_ROOT / ".env")
    for k in PLAIN:
        v = (env.get(k) or os.environ.get(k) or "").strip()
        if v:
            secrets[k] = v
        else:
            print(f"   MISSING {k} (not in .env)")
    d = OUTPUT_DIR / "cloud_inputs"
    d.mkdir(parents=True, exist_ok=True)
    rc = 0
    existing = set()
    if a.set:                                           # a plain key that is already a secret is left alone
        r = subprocess.run(["gh", "secret", "list", "--json", "name", "-q", ".[].name"], capture_output=True, text=True, cwd=PROJECT_ROOT)
        existing = set(r.stdout.split())
    for name, value in secrets.items():
        if name not in PLAIN:
            (d / name).write_text(value)
        line = f"   {name:28} {len(value):>7,} characters"
        if a.set and name in PLAIN and name in existing:
            print(line + "  kept (already set)")
            continue
        if a.set:
            r = subprocess.run(["gh", "secret", "set", name], input=value, text=True, capture_output=True, cwd=PROJECT_ROOT)
            line += "  set" if r.returncode == 0 else "  FAILED"
            rc = rc or r.returncode
        print(line)
    return rc


if __name__ == "__main__":
    sys.exit(main())
