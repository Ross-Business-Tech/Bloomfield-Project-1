"""Deal Radar website for Render: serves a STATIC SNAPSHOT of the public lead list behind a password.

    SITE_PASSWORD=... SNAPSHOT_PATH=output/site_snapshot.json python -m src.web.server

- No database and no Supabase key at runtime: the only data is the snapshot file made by `python -m src.web.snapshot`
  (public view only, past-deal homes left out), uploaded to Render as a Secret File (/etc/secrets/...).
- HTTP basic auth on every page and download (any user name, password = SITE_PASSWORD); the service refuses to start
  without SITE_PASSWORD. /healthz (Render's health check: returns "ok", no data) and /robots.txt (disallow all) are
  the only unauthenticated paths.
- noindex: a robots meta tag on the page and an X-Robots-Tag header on every response.
Pages: / (the dashboard in src/web/static: KPIs, map, Lists A / B / C, detail panel), /api/snapshot.json (what the
dashboard reads), /api/<leads|list_a|list_b|list_c|top20>.json, /exports/list_<a|b|c>.csv.
"""
from __future__ import annotations

import base64
import csv
import hmac
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
STATIC_DIR = Path(__file__).resolve().parent / "static"
STATIC_FILES = {"app.js": "application/javascript; charset=utf-8", "app.css": "text/css; charset=utf-8",
                "states-albers-10m.json": "application/json"}
CSP = ("default-src 'self'; script-src 'self' https://cdn.jsdelivr.net; style-src 'self' https://fonts.googleapis.com; "
       "font-src https://fonts.gstatic.com; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; "
       "base-uri 'none'; form-action 'none'")
SNAPSHOT_KEYS = ("generated_at", "counts", "items", "list_c")
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


def static_file(name: str) -> str:
    return (STATIC_DIR / name).read_text(encoding="utf-8")


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
            self.send_header("Content-Security-Policy", CSP)
            self.send_header("Referrer-Policy", "no-referrer")
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
                return self._send(200, static_file("index.html"), "text/html; charset=utf-8")
            if path.startswith("/static/") and path[8:] in STATIC_FILES:
                return self._send(200, static_file(path[8:]), STATIC_FILES[path[8:]])
            if path == "/api/snapshot.json":
                return self._send(200, json.dumps({k: snap.get(k) for k in SNAPSHOT_KEYS}, default=str),
                                  "application/json")
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
