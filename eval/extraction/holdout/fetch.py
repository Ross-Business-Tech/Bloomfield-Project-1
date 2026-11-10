"""Fetch holdout article text politely (robots.txt, 1 req/s, honest UA). Text stays in the local cache
(data/raw/cache/holdout_eval, gitignored) - never committed. Tries url, then alt_url."""
from __future__ import annotations

import json
import re
from pathlib import Path

from src.common.http import PoliteClient, SourceBlocked
from src.discover.news.text import html_to_text

HERE = Path(__file__).parent
# Pages a person saved from a browser (File > Save Page As, or copy the article text into a .txt file) when a site
# blocks programs or a link is dead. Gitignored (eval/private/), never committed.
LOCAL = HERE.parents[2] / "eval" / "private" / "holdout_text"


def fetch_all(log=print) -> dict[str, dict]:
    cases = json.loads((HERE / "holdout_labels.json").read_text())["cases"]
    out = {}
    with PoliteClient("holdout_eval", timeout=30) as http:
        for c in cases:
            local = next((p for p in (LOCAL / f"{c['id']}.html", LOCAL / f"{c['id']}.txt") if p.exists()), None)
            if local:
                raw = local.read_text(errors="replace")
                text = html_to_text(raw) if local.suffix == ".html" else raw.strip()
                m = re.search(r"<title[^>]*>(.*?)</title>", raw, re.S | re.I) if local.suffix == ".html" else None
                out[c["id"]] = {"url": c["url"], "title": (m.group(1).strip() if m else ""), "body": text}
                log(f"{c['id']}: local copy {local.name} ({len(text)} chars)")
                continue
            for url in [c["url"], c.get("alt_url")]:
                if not url:
                    continue
                try:
                    r = http.get(url)
                except SourceBlocked as e:
                    log(f"{c['id']}: BLOCKED {url[:70]} ({e})"); continue
                except Exception as e:  # network error: try the next URL
                    log(f"{c['id']}: ERROR {url[:70]} ({type(e).__name__})"); continue
                if r.status_code != 200:
                    log(f"{c['id']}: HTTP {r.status_code} {url[:70]}"); continue
                html = r.text
                m = re.search(r"<title[^>]*>(.*?)</title>", html, re.S | re.I)
                text = html_to_text(html)
                if len(text) < 400:
                    log(f"{c['id']}: too little text ({len(text)} chars) at {url[:70]}"); continue
                out[c["id"]] = {"url": url, "title": (m.group(1).strip() if m else ""), "body": text}
                log(f"{c['id']}: ok {len(text):>6} chars  {url[:70]}")
                break
            else:
                log(f"{c['id']}: NOT AVAILABLE")
    return out


if __name__ == "__main__":
    fetch_all()
