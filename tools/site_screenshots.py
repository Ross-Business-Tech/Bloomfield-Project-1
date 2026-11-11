"""Screenshots of the Deal Radar dashboard for review (desktop + mobile), written to output/ (gitignored).

    .venv/bin/pip install -r requirements-dev.txt
    .venv/bin/python -m src.web.snapshot            # makes output/site_snapshot.json (never committed)
    .venv/bin/python tools/site_screenshots.py

Starts the real server on a free local port with a throwaway password, drives the installed Google Chrome through
Playwright (no browser download), and checks the page loads without errors and without the password it answers 401.
"""
from __future__ import annotations

import json
import secrets
import sys
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.web import server  # noqa: E402

OUT = ROOT / "output"
VIEWS = {"desktop": {"viewport": {"width": 1440, "height": 900}},
         "mobile": {"viewport": {"width": 390, "height": 844}, "device_scale_factor": 2, "is_mobile": True,
                    "has_touch": True}}


def main() -> int:
    from playwright.sync_api import sync_playwright
    snap = server.load_snapshot(str(OUT / "site_snapshot.b64"))      # the exact file that is uploaded to Render
    pw = secrets.token_urlsafe(12)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), server.make_handler(snap, pw))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        urllib.request.urlopen(base + "/")
        print("ERROR: page opened without a password")
        return 1
    except urllib.error.HTTPError as e:
        assert e.code == 401
    shots, problems = [], []
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome")
        for name, opts in VIEWS.items():
            ctx = browser.new_context(http_credentials={"username": "review", "password": pw}, **opts)
            page = ctx.new_page()
            page.on("pageerror", lambda e: problems.append(f"{name}: {e}"))
            page.on("console", lambda m: problems.append(f"{name} console {m.type}: {m.text}") if m.type == "error" else None)
            page.goto(base + "/", wait_until="networkidle")
            page.wait_for_selector("#tbody tr")
            page.wait_for_selector("#map path.state")
            path = OUT / f"site_{name}.png"
            page.screenshot(path=str(path), full_page=True)
            shots.append(path)
            # map: click the state with the most leads, then the detail panel of the first row
            counts = {}
            for r in snap["items"]:
                counts[r.get("state")] = counts.get(r.get("state"), 0) + 1
            top_state = max((k for k in counts if k), key=counts.get)
            page.select_option("#f-state", top_state)
            page.wait_for_timeout(300)
            page.locator("#tbody tr td.prop").first.click()
            page.wait_for_selector("#panel:not(.hidden)")
            path = OUT / f"site_{name}_detail.png"
            page.screenshot(path=str(path), full_page=False)
            shots.append(path)
            page.keyboard.press("Escape")
            page.select_option("#f-state", "")
            page.click('[data-tab="C"]')
            page.wait_for_timeout(300)
            page.locator("#view-dashboard .list-card").scroll_into_view_if_needed()
            path = OUT / f"site_{name}_list_c.png"
            page.locator("#view-dashboard .list-card").screenshot(path=str(path))
            shots.append(path)
            for view in ("how", "funnel", "explore"):
                page.goto(base + "/#/" + view, wait_until="networkidle")
                page.wait_for_selector(f"#view-{view}:not(.hidden)")
                page.wait_for_timeout(300)
                path = OUT / f"site_{name}_{view}.png"
                page.screenshot(path=str(path), full_page=view != "explore")
                shots.append(path)
            ctx.close()
        browser.close()
    srv.shutdown()
    print(json.dumps({"screenshots": [str(s) for s in shots], "page_errors": problems}, indent=1))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
