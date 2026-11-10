"""Resolve the latest dataset id for each CMS title from https://data.cms.gov/data.json (ids change every release)."""
from __future__ import annotations

import re
from dataclasses import dataclass

from src.common.http import PoliteClient, SourceBlocked

DATA_JSON = "https://data.cms.gov/data.json"
API_ROOT = "https://data.cms.gov/data-api/v1/dataset"

# source_id -> (exact base title in data.json, fallback dataset id from docs/BUILD_SPEC.md, landing page)
TITLES = {
    "cms_chow": ("Skilled Nursing Facility Change of Ownership", "f557a6ed-95b3-4a22-8433-4175db2dec1c"),
    "cms_chow_owner": ("Skilled Nursing Facility Change of Ownership - Owner Information", "a4358712-e910-4eaf-8f24-5e90ba3cf8d0"),
    "cms_snf_owner": ("Skilled Nursing Facility All Owners", "afe44b85-cc6d-40d7-b5df-00ae8910d1d2"),
    "cms_snf_enrollment": ("Skilled Nursing Facility Enrollments", "5f2c306f-3b1c-42cd-b037-187b2ce22126"),
    "cms_cost_report": ("Skilled Nursing Facility Cost Report", "a69d3df7-3f66-4a0d-b5b8-0d66049bd565"),
    "cms_pbj": ("Payroll Based Journal Daily Nurse Staffing", "7e0d53ba-8f02-4c66-98a5-14a1c997c50d"),
    "cms_chain_perf": ("Nursing Home Chain Performance Measures", "97ecfad1-d3f1-4d42-b774-d74661d830bc"),
}


@dataclass
class Dataset:
    source_id: str
    title: str
    api_url: str            # .../dataset/<id>/data
    landing_url: str        # human page, used as source_url when a row has no single page
    version_date: str | None  # YYYY-MM-DD from the title suffix, None if unknown
    resolved: bool          # False = fallback id from BUILD_SPEC was used


def landing_for(ds: dict) -> str:
    lp = ds.get("landingPage")
    if isinstance(lp, dict):
        lp = lp.get("accessURL")
    return lp or ds.get("identifier") or "https://data.cms.gov/"


def pick_latest(catalog: dict, base_title: str) -> dict | None:
    """Newest dataset whose title is exactly `base_title` or `base_title : YYYY-MM-DD`."""
    pat = re.compile(re.escape(base_title) + r"(?: : (\d{4}-\d{2}-\d{2}))?")
    best, best_key = None, None
    for ds in catalog.get("dataset", []):
        m = pat.fullmatch(ds.get("title", ""))
        if not m:
            continue
        key = (m.group(1) or "", ds.get("modified") or "")
        if best_key is None or key > best_key:
            best, best_key = ds, key
    return best


def api_url_of(ds: dict) -> str | None:
    for dist in ds.get("distribution", []):
        url = dist.get("accessURL") or ""
        if dist.get("format") == "API" and url.startswith(API_ROOT) and url.endswith("/data"):
            return url
    return None


def resolve(client: PoliteClient, source_id: str, catalog: dict | None) -> Dataset:
    base, fallback_id = TITLES[source_id]
    ds = pick_latest(catalog, base) if catalog else None
    url = api_url_of(ds) if ds else None
    if ds and url:
        m = re.search(r" : (\d{4}-\d{2}-\d{2})$", ds["title"])
        return Dataset(source_id, ds["title"], url, landing_for(ds), m.group(1) if m else None, True)
    return Dataset(source_id, base, f"{API_ROOT}/{fallback_id}/data", f"{API_ROOT}/{fallback_id}/data", None, False)


def load_catalog(client: PoliteClient) -> dict | None:
    """Fetch data.json fresh (never from cache: the point is to see the newest release). None on non-block failures."""
    try:
        r = client.get(DATA_JSON, use_cache=False)
    except SourceBlocked:
        raise
    except Exception:
        return None
    if r.status_code != 200:
        return None
    try:
        return r.json()
    except ValueError:
        return None
