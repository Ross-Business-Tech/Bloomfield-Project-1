"""Polite HTTP client used by every loader.

- Descriptive User-Agent (never spoofed).
- Obeys robots.txt per host.
- At most one request per second per host.
- Caches successful responses on disk so debugging never re-downloads.
- 401/403 or a robots.txt block raises SourceBlocked: the loader stops and the source becomes manual.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlencode, urlsplit

import httpx

from .config import CACHE_DIR, USER_AGENT

_OUR_TOKEN = USER_AGENT.split("/")[0].lower()


MAX_RETRY_WAIT = 300   # seconds: a server asking us to wait longer than this ends the run for that source


class RateLimited(Exception):
    """The server's Retry-After is longer than MAX_RETRY_WAIT (a quota, not a short burst limit)."""


class SourceBlocked(Exception):
    """The site refused us (401/403) or robots.txt disallows the URL. Never work around this."""


@dataclass
class Robots:
    """robots.txt rules for the group that applies to us (our own product token beats '*').

    Python's urllib.robotparser applies the FIRST matching rule, so `Disallow: /wp-admin/` listed before
    `Allow: /wp-admin/admin-ajax.php` would wrongly block an allowed path. RFC 9309 says the longest
    match wins and Allow wins ties; that is what this implements.
    """
    rules: list[tuple[bool, str]] = field(default_factory=list)   # (is_allow, pattern)
    deny_all: bool = False
    crawl_delay: float | None = None

    @classmethod
    def parse(cls, text: str) -> "Robots":
        groups: list[tuple[list[str], list[tuple[bool, str]], list[float]]] = []
        last_was_agent = False
        for raw in text.splitlines():
            line = raw.split("#", 1)[0].strip()
            key, sep, val = line.partition(":")
            if not sep:
                continue
            key, val = key.strip().lower(), val.strip()
            if key == "user-agent":
                if not last_was_agent:
                    groups.append(([], [], []))
                groups[-1][0].append(val.lower())
                last_was_agent = True
                continue
            last_was_agent = False
            if not groups:
                continue
            if key in ("allow", "disallow") and val:
                groups[-1][1].append((key == "allow", val))
            elif key == "crawl-delay":
                try:
                    groups[-1][2].append(float(val))
                except ValueError:
                    pass
        own = [g for g in groups if _OUR_TOKEN in g[0]]
        chosen = own or [g for g in groups if "*" in g[0]]
        delays = [d for g in chosen for d in g[2]]
        return cls(rules=[r for g in chosen for r in g[1]], crawl_delay=max(delays) if delays else None)

    def allows(self, path: str) -> bool:
        if self.deny_all:
            return False
        best_len, allow = -1, True
        for is_allow, pattern in self.rules:
            if _pattern_matches(pattern, path) and (
                len(pattern) > best_len or (len(pattern) == best_len and is_allow)
            ):
                best_len, allow = len(pattern), is_allow
        return allow


def _pattern_matches(pattern: str, path: str) -> bool:
    regex = re.escape(pattern).replace(r"\*", ".*")
    if regex.endswith(r"\$"):
        regex = regex[:-2] + "$"
    return re.match(regex, path) is not None


@dataclass
class Response:
    url: str
    status_code: int
    headers: dict
    content: bytes
    from_cache: bool = False

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", errors="replace")

    def json(self):
        return json.loads(self.content)


class PoliteClient:
    def __init__(
        self,
        source_id: str,
        *,
        min_interval: float = 1.0,
        cache_dir: Path = CACHE_DIR,
        use_cache: bool = True,
        timeout: float = 60.0,
        max_retries: int = 3,
        transport: httpx.BaseTransport | None = None,
        headers: dict[str, str] | None = None,
        clock=time.monotonic,
        sleep=time.sleep,
    ):
        self.source_id = source_id
        self.min_interval = min_interval
        self.cache_dir = Path(cache_dir) / source_id
        self.use_cache = use_cache
        self.max_retries = max_retries
        self._clock = clock
        self._sleep = sleep
        self._last_request: dict[str, float] = {}
        self._host_interval: dict[str, float] = {}   # robots.txt Crawl-delay above min_interval
        self._robots: dict[str, Robots] = {}
        # Extra headers (e.g. an API token) are sent with every request but never written to the cache.
        # The User-Agent cannot be overridden.
        self._client = httpx.Client(
            headers={**(headers or {}), "User-Agent": USER_AGENT},
            timeout=timeout,
            follow_redirects=True,
            transport=transport,
        )

    # ── public ──────────────────────────────────────────────
    def get(self, url: str, params: dict | None = None, *, use_cache: bool | None = None) -> Response:
        full_url = f"{url}?{urlencode(sorted(params.items()))}" if params else url
        if not self.allowed(full_url):
            raise SourceBlocked(f"robots.txt disallows {full_url}")

        cache_on = self.use_cache if use_cache is None else use_cache
        cache_file = self._cache_path(full_url)
        if cache_on and cache_file.exists():
            meta = json.loads(cache_file.with_suffix(".json").read_text())
            return Response(full_url, meta["status_code"], meta["headers"], cache_file.read_bytes(), True)

        resp = self._fetch(full_url)
        if resp.status_code in (401, 403):
            raise SourceBlocked(f"{resp.status_code} from {full_url}")
        if cache_on and resp.status_code == 200:
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            cache_file.write_bytes(resp.content)
            cache_file.with_suffix(".json").write_text(
                json.dumps({"status_code": resp.status_code, "headers": resp.headers, "url": full_url})
            )
        return resp

    def allowed(self, url: str) -> bool:
        """RFC 9309 robots.txt check: longest matching rule wins, Allow wins ties."""
        parts = urlsplit(url)
        host = f"{parts.scheme}://{parts.netloc}"
        if host not in self._robots:
            r = self._fetch(f"{host}/robots.txt")
            if r.status_code in (401, 403):
                self._robots[host] = Robots(deny_all=True)
            elif r.status_code >= 400:
                self._robots[host] = Robots()          # no robots.txt = no restrictions
            else:
                self._robots[host] = Robots.parse(r.text)
                delay = self._robots[host].crawl_delay
                if delay and delay > self.min_interval:
                    self._host_interval[parts.netloc] = delay
        path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
        return self._robots[host].allows(path)

    def close(self):
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # ── internals ───────────────────────────────────────────
    def _cache_path(self, url: str) -> Path:
        return self.cache_dir / hashlib.sha256(url.encode()).hexdigest()

    def _wait_turn(self, host: str):
        last = self._last_request.get(host)
        if last is not None:
            wait = self._host_interval.get(host, self.min_interval) - (self._clock() - last)
            if wait > 0:
                self._sleep(wait)
        self._last_request[host] = self._clock()

    def _fetch(self, url: str) -> Response:
        host = urlsplit(url).netloc
        for attempt in range(self.max_retries + 1):
            self._wait_turn(host)
            try:
                r = self._client.get(url)
            except (httpx.TimeoutException, httpx.NetworkError):
                # Slow or flaky connection: back off and retry instead of failing the whole run.
                if attempt < self.max_retries:
                    self._sleep(2 ** (attempt + 2))
                    continue
                raise
            if r.status_code in (429, 500, 502, 503, 504) and attempt < self.max_retries:
                retry_after = r.headers.get("retry-after", "")
                wait = float(retry_after) if retry_after.isdigit() else 2 ** (attempt + 1)
                if wait > MAX_RETRY_WAIT:
                    # e.g. a daily API quota ("try again in 15296 s"): fail now instead of sleeping for hours
                    raise RateLimited(f"{host}: HTTP {r.status_code}, server asks to wait {wait:.0f}s "
                                      f"(> {MAX_RETRY_WAIT}s); try again later")
                self._sleep(wait)
                continue
            return Response(str(r.url), r.status_code, dict(r.headers), r.content)
        raise AssertionError("unreachable")
