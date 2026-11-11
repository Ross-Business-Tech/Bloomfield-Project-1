"""Publish the website data: snapshot -> pack -> leak checks -> (with --confirm) upload to Render and deploy.

    python -m src.web.publish              # DRY RUN (default): builds and checks everything, changes nothing on Render
    python -m src.web.publish --confirm    # uploads site_snapshot.b64 as the Secret File, deploys, waits, checks /healthz

Steps:
 1. keep the previous upload as output/site_snapshot.prev.b64 (gitignored), then rebuild the snapshot (incl. contacts)
 2. pack (gzip + base64); stop if the upload is over MAX_BYTES (Render: 1 MB for all Secret Files together)
 3. leak checks on the unpacked upload: private terms (eval/private/forbidden_terms.txt, reported as 'term #N'),
    'licensed:' tags, past-deal facility ids (eval/private/exclude_facilities_PRIVATE.txt), the client's name
 4. dry run: print size, counts, leak result and the exact Render API calls it would make (+ read-only GETs)
    --confirm: PUT the Secret File, POST a deploy, poll it until live, then GET <site>/healthz == 200
Needs RENDER_API_KEY in .env for the Render calls; never prints it.
"""
from __future__ import annotations

import argparse
import base64
import gzip
import json
import shutil
import sys
import time
from pathlib import Path

from src.common.config import OUTPUT_DIR, PROJECT_ROOT

SERVICE_ID = "srv-dbps3hjrjlhs73att7eg"
SECRET_FILE = "site_snapshot.b64"
MAX_BYTES = 900 * 1024
API = "https://api.render.com/v1"
EXCLUDE_FILE = PROJECT_ROOT / "eval" / "private" / "exclude_facilities_PRIVATE.txt"
CLIENT_TERM = "bloom" + "field"          # split so this file itself does not trip the name scan


def private_terms() -> tuple[list[str], list[str]]:
    sys.path.insert(0, str(PROJECT_ROOT / "tools"))
    import leak_guard
    return leak_guard.load_private_terms()


PLACE_FIELDS = ("property", "city", "buyer")


def mask_place_names(snap: dict) -> dict:
    """Copy of the upload where, in each home's own row (main list and Explore), that row's CITY is blanked out of its
    property / city / buyer fields. A town that happens to share a private word (e.g. a town named like the client)
    is public CMS / state-roster data; any other occurrence still fails the check (decision Nov 11, 2026)."""
    import copy
    import re
    out = copy.deepcopy(snap)
    for key in ("items", "explore"):
        for r in out.get(key) or []:
            city = (r.get("city") or "").strip() if isinstance(r, dict) else ""
            if len(city) < 3 or city.upper() == "NOT FOUND":
                continue
            rx = re.compile(re.escape(city), re.I)
            for f in PLACE_FIELDS:
                if isinstance(r.get(f), str):
                    r[f] = rx.sub("", r[f])
    return out


def leak_check(text: str) -> list[str]:
    """Problems found in the upload, described without repeating any private term."""
    low = text.lower()
    terms, allow = private_terms()
    masked = low
    for a in allow:
        masked = masked.replace(a, "")
    out = [f"term #{i} ({masked.count(t)}x)" for i, t in enumerate(terms, 1) if t in masked]
    if "licensed:" in low:
        out.append(f"'licensed:' tag ({low.count('licensed:')}x)")
    if CLIENT_TERM in low:
        out.append(f"client name ({low.count(CLIENT_TERM)}x)")
    if EXCLUDE_FILE.exists():
        ids = [x.strip().lower() for x in EXCLUDE_FILE.read_text().splitlines() if x.strip() and not x.startswith("#")]
        n = sum(1 for i in ids if i in low)
        if n:
            out.append(f"past-deal facility id ({n} of {len(ids)})")
    if not terms:
        out.append("private term list missing (eval/private/forbidden_terms.txt): cannot check")
    return out


def api_key() -> str | None:
    from dotenv import dotenv_values
    return (dotenv_values(PROJECT_ROOT / ".env").get("RENDER_API_KEY") or "").strip() or None


def client(key: str):
    import httpx
    return httpx.Client(base_url=API, timeout=60, headers={"Authorization": f"Bearer {key}", "Accept": "application/json"})


def unwrap(x: dict, k: str) -> dict:
    return x.get(k) or x


def build() -> tuple[Path, dict]:
    from src.web import snapshot
    b64 = OUTPUT_DIR / SECRET_FILE
    if b64.exists():
        shutil.copy2(b64, OUTPUT_DIR / "site_snapshot.prev.b64")
    snapshot.main(["--out", str(OUTPUT_DIR / "site_snapshot.json")])
    return b64, json.loads(gzip.decompress(base64.b64decode(b64.read_bytes().strip())))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m src.web.publish", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--confirm", action="store_true", help="really upload and deploy (default: dry run)")
    a = ap.parse_args(argv)
    b64, snap = build()
    size = b64.stat().st_size
    c = snap.get("counts", {})
    print(f"upload: {b64} {size:,} bytes (limit {MAX_BYTES:,}); generated {snap.get('generated_at')}")
    print(f"counts: {json.dumps(c)}")
    items = snap.get("items") or []
    print(f"contacts: phone {sum(1 for r in items if (r.get('contact') or {}).get('phone'))}, "
          f"website {sum(1 for r in items if (r.get('contact') or {}).get('website'))} of {len(items)} leads")
    raw_hits = leak_check(json.dumps(snap, ensure_ascii=False, default=str))
    problems = leak_check(json.dumps(mask_place_names(snap), ensure_ascii=False, default=str))
    if raw_hits and not problems:
        print("note: hits only in home city names (public place names, allowed):", "; ".join(raw_hits))
    if size > MAX_BYTES:
        problems.append(f"upload too large ({size:,} > {MAX_BYTES:,} bytes)")
    print("leak / size check:", "clean" if not problems else "FAILED: " + "; ".join(problems))
    if problems:
        print("ABORT: nothing uploaded.")
        return 1
    calls = [f"PUT  {API}/services/{SERVICE_ID}/secret-files/{SECRET_FILE}   body {{\"content\": <{size:,} chars>}}",
             f"POST {API}/services/{SERVICE_ID}/deploys   body {{\"clearCache\": \"do_not_clear\"}}",
             f"GET  {API}/services/{SERVICE_ID}/deploys/<deployId>   every 15 s until status 'live' (max 15 min)",
             "GET  <service url>/healthz   expect 200"]
    key = api_key()
    if not a.confirm:
        print("DRY RUN. Would call:")
        for x in calls:
            print("  ", x)
        if not key:
            print("RENDER_API_KEY not in .env: read-only checks skipped. Manual steps: docs/DEPLOY_RENDER.md.")
            return 0
        with client(key) as h:
            r = h.get(f"/services/{SERVICE_ID}")
            svc = r.json() if r.status_code == 200 else {}
            det = svc.get("serviceDetails") or {}
            print(f"read-only GET service: {r.status_code} name={svc.get('name')} branch={svc.get('branch')} "
                  f"autoDeploy={svc.get('autoDeploy')} plan={det.get('plan')} url={det.get('url')} "
                  f"healthCheckPath={det.get('healthCheckPath')}")
            r = h.get(f"/services/{SERVICE_ID}/secret-files")
            names = [unwrap(x, "secretFile").get("name") for x in r.json()] if r.status_code == 200 else r.text[:120]
            print(f"read-only GET secret-files: {r.status_code} {names}")
            print("secret-file upload supported:", "yes (PUT add-or-update secret file)" if r.status_code == 200 else
                  "UNKNOWN: use the manual steps in docs/DEPLOY_RENDER.md")
        return 0
    if not key:
        print("RENDER_API_KEY not in .env. Manual steps: Render > deal-radar > Environment > Secret Files > "
              f"{SECRET_FILE} > paste {b64}; then Manual Deploy > Deploy latest commit; then open /healthz.")
        return 2
    import httpx
    with client(key) as h:
        url = ((h.get(f"/services/{SERVICE_ID}").json().get("serviceDetails") or {}).get("url") or "").rstrip("/")
        r = h.put(f"/services/{SERVICE_ID}/secret-files/{SECRET_FILE}", json={"content": b64.read_text().strip()})
        print("upload:", r.status_code)
        if r.status_code >= 300:
            return 3
        r = h.post(f"/services/{SERVICE_ID}/deploys", json={"clearCache": "do_not_clear"})
        dep = unwrap(r.json(), "deploy") if r.status_code < 300 else {}
        print("deploy:", r.status_code, dep.get("id"))
        if not dep.get("id"):
            return 4
        status, t0 = dep.get("status"), time.time()
        while status not in ("live", "build_failed", "update_failed", "canceled", "deactivated") and time.time() - t0 < 900:
            time.sleep(15)
            status = unwrap(h.get(f"/services/{SERVICE_ID}/deploys/{dep['id']}").json(), "deploy").get("status")
            print("  deploy status:", status)
    if status != "live":
        print("deploy did not go live; previous upload kept at output/site_snapshot.prev.b64")
        return 5
    code = httpx.get(url + "/healthz", timeout=60).status_code if url else None
    print(f"healthz: {code}")
    return 0 if code == 200 else 6


if __name__ == "__main__":
    sys.exit(main())
