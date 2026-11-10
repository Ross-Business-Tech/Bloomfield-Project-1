"""Page through a data.cms.gov dataset API."""
from __future__ import annotations

from typing import Iterator

from src.common.http import PoliteClient

PAGE_SIZE = 5000


def total_rows(client: PoliteClient, api_url: str) -> int | None:
    try:
        r = client.get(f"{api_url}/stats", use_cache=False)
        if r.status_code == 200:
            return int(r.json().get("total_rows"))
    except Exception:
        pass
    return None


def iter_pages(client: PoliteClient, api_url: str, limit: int | None = None) -> Iterator[tuple[str, list[dict]]]:
    """Yield (page_url, rows). Stops at the last page or after `limit` rows."""
    offset, remaining = 0, limit
    while remaining is None or remaining > 0:
        size = PAGE_SIZE if remaining is None else min(PAGE_SIZE, remaining)
        r = client.get(api_url, {"size": size, "offset": offset})
        if r.status_code != 200:
            raise RuntimeError(f"{r.url} returned HTTP {r.status_code}")
        rows = r.json()
        if not rows:
            return
        yield r.url, rows
        offset += len(rows)
        if remaining is not None:
            remaining -= len(rows)
        if len(rows) < size:
            return
