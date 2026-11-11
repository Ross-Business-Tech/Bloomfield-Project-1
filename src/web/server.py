"""Website for Render: serves a STATIC SNAPSHOT of the public lead list behind a password.

    SITE_PASSWORD=... SNAPSHOT_PATH=output/site_snapshot.json python -m src.web.server

- No database and no Supabase key at runtime: the only data is the snapshot file made by `python -m src.web.snapshot`
  (public view only, past-deal homes left out), uploaded to Render as a Secret File (/etc/secrets/...).
- HTTP basic auth on every page and download (any user name, password = SITE_PASSWORD); the service refuses to start
  without SITE_PASSWORD. /healthz (Render's health check: returns "ok", no data) and /robots.txt (disallow all) are
  the only unauthenticated paths.
- noindex: a robots meta tag on the page and an X-Robots-Tag header on every response.
Pages: / (List A, B, C with source links), /api/<leads|list_a|list_b|list_c|top20>.json, /exports/list_<a|b|c>.csv.
"""
from __future__ import annotations

import base64
import csv
import hmac
import html
import io
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

DEFAULT_SNAPSHOT = "/etc/secrets/site_snapshot.json"
NF = "NOT FOUND"
LEAD_COLS = ["list", "rank", "property", "city", "state", "care_type", "units_or_beds", "signals", "stage", "buyer",
             "buyer_status", "score", "flags", "public_source_link", "not_found"]
C_COLS = ["list", "rank", "buyer", "homes_in_list", "example_homes", "public_source_link"]
ROBOTS = "User-agent: *\nDisallow: /\n"
OPEN_PATHS = {"/healthz", "/robots.txt"}


class ConfigError(RuntimeError):
    pass


def site_password() -> str:
    pw = os.environ.get("SITE_PASSWORD", "")
    if len(pw) < 8:
        raise ConfigError("SITE_PASSWORD must be set (8+ characters)")
    return pw


def load_snapshot(path: str | None = None) -> dict:
    p = Path(path or os.environ.get("SNAPSHOT_PATH") or DEFAULT_SNAPSHOT)
    if not p.exists():
        raise ConfigError(f"snapshot file not found: {p}")
    return json.loads(p.read_text(encoding="utf-8"))


def authorized(header: str | None, password: str) -> bool:
    if not header or not header.startswith("Basic "):
        return False
    try:
        _, _, pw = base64.b64decode(header[6:]).decode("utf-8").partition(":")
    except ValueError:
        return False
    return hmac.compare_digest(pw.encode(), password.encode())


def to_csv(rows: list[dict], cols: list[str]) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
    w.writeheader()
    w.writerows(rows)
    return buf.getvalue()


def _link(u) -> str:
    return (f'<a href="{html.escape(u, quote=True)}" rel="noopener noreferrer">link</a>'
            if isinstance(u, str) and u.startswith(("http://", "https://")) else NF)


def page(snap: dict) -> str:
    def table(rows, cols, n=50):
        head = "".join(f"<th>{html.escape(c)}</th>" for c, _ in cols) + "<th>Source</th>"
        body = "".join("<tr>" + "".join(f"<td>{html.escape(str(r.get(k, '')))}</td>" for _, k in cols)
                       + f"<td>{_link(r.get('public_source_link'))}</td></tr>" for r in rows[:n])
        return f'<div class="wrap"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'
    lead_cols = [("#", "rank"), ("Property", "property"), ("City", "city"), ("State", "state"),
                 ("Signals", "signals"), ("Buyer", "buyer")]
    c = snap.get("counts", {})
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><meta name="robots" content="noindex, nofollow">
<title>Distressed senior housing leads</title>
<style>:root{{--bg:#fff;--fg:#1b1b1b;--line:#ddd;--link:#0b57d0}}
@media (prefers-color-scheme:dark){{:root{{--bg:#121212;--fg:#eee;--line:#333;--link:#8ab4f8}}}}
body{{font-family:system-ui,sans-serif;margin:16px;color:var(--fg);background:var(--bg)}}a{{color:var(--link)}}
table{{border-collapse:collapse;width:100%;font-size:14px}}th,td{{border-bottom:1px solid var(--line);padding:6px;
text-align:left;vertical-align:top}}.wrap{{overflow-x:auto}}</style></head><body>
<h1>Distressed senior housing leads</h1>
<p>Public sources only. Snapshot {html.escape(str(snap.get('generated_at', '')))}. Leads {c.get('leads', 0)} ·
List A {c.get('list_a', 0)} · List B {c.get('list_b', 0)} · List C {c.get('list_c', 0)}.
Downloads: <a href="/exports/list_a.csv">A</a> · <a href="/exports/list_b.csv">B</a> · <a href="/exports/list_c.csv">C</a>
· <a href="/api/leads.json">all leads (JSON)</a></p>
<h2>List A: live deals with a named buyer (top 50)</h2>{table(snap.get('list_a', []), lead_cols)}
<h2>List B: pre-sale distress, no buyer yet (top 50)</h2>{table(snap.get('list_b', []), lead_cols)}
<h2>List C: buyers named on 2+ homes in this list</h2>{table(snap.get('list_c', []), [("#", "rank"), ("Buyer", "buyer"),
                                                         ("Homes", "homes_in_list"), ("Examples", "example_homes")])}
</body></html>"""


def make_handler(snap: dict, password: str):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, code, body: str, ctype: str, extra=None):
            b = body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(b)))
            self.send_header("X-Robots-Tag", "noindex, nofollow")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Cache-Control", "no-store")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(b)

        def do_GET(self):  # noqa: N802
            path = urlsplit(self.path).path
            if path == "/healthz":
                return self._send(200, "ok", "text/plain")
            if path == "/robots.txt":
                return self._send(200, ROBOTS, "text/plain")
            if not authorized(self.headers.get("Authorization"), password):
                return self._send(401, "password required", "text/plain",
                                  {"WWW-Authenticate": 'Basic realm="leads", charset="UTF-8"'})
            if path == "/":
                return self._send(200, page(snap), "text/html; charset=utf-8")
            if path.startswith("/api/") and path.endswith(".json"):
                key = path[5:-5]
                if key in ("leads", "list_a", "list_b", "list_c", "top20"):
                    return self._send(200, json.dumps(snap.get(key, []), default=str), "application/json")
            if path in ("/exports/list_a.csv", "/exports/list_b.csv", "/exports/list_c.csv"):
                key = path.split("/")[-1][:-4]
                return self._send(200, to_csv(snap.get(key, []), C_COLS if key == "list_c" else LEAD_COLS),
                                  "text/csv; charset=utf-8")
            return self._send(404, "not found", "text/plain")

        def log_message(self, fmt, *args):     # no headers / query strings in logs
            sys.stderr.write("%s %s\n" % (self.command, urlsplit(self.path).path))
    return Handler


def main() -> int:
    password = site_password()
    snap = load_snapshot()
    port = int(os.environ.get("PORT", "10000"))
    ThreadingHTTPServer(("0.0.0.0", port), make_handler(snap, password)).serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
