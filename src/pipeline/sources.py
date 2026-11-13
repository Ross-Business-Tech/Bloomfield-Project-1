"""Which pipeline source a public link came from (source attribution and the nightly refresh share this).

Pure functions, no I/O. A link is classified by host (and, for data.cms.gov, by dataset id). Links that no loader
produced (hand-found public pages such as local news or a law-firm blog, added through court_facts / overrides /
public events) are reported as 'web:<host>' so they stay visible instead of being folded into a loader.
Licensed tags ('licensed:...') are never classified: callers drop them before counting.
"""
from __future__ import annotations

from urllib.parse import urlsplit

CMS_DATASETS = {   # data.cms.gov dataset id -> source_id (raw.source base_url)
    "97ecfad1-d3f1-4d42-b774-d74661d830bc": "cms_chain_perf",
    "f557a6ed-95b3-4a22-8433-4175db2dec1c": "cms_chow",
    "a4358712-e910-4eaf-8f24-5e90ba3cf8d0": "cms_chow_owner",
    "a69d3df7-3f66-4a0d-b5b8-0d66049bd565": "cms_cost_report",
    "7e0d53ba-8f02-4c66-98a5-14a1c997c50d": "cms_pbj",
    "5f2c306f-3b1c-42cd-b037-187b2ce22126": "cms_snf_enrollment",
    "afe44b85-cc6d-40d7-b5df-00ae8910d1d2": "cms_snf_owner",
}
HOSTS = {   # host (without 'www.') -> source_id
    "courtlistener.com": "courtlistener",
    "dm.epiq11.com": "epiq", "document.epiq11.com": "epiq",
    "cases.stretto.com": "stretto", "case.stretto.com": "stretto",
    "restructuring.ra.kroll.com": "kroll",
    "elevenflo.com": "elevenflo",
    "seniorhousingnews.com": "shn_rss",
    "skillednursingnews.com": "snn_rss",
    "seniorshousingbusiness.com": "shb_rss",
    "mcknightsseniorliving.com": "mcknights_sl_rss",
    "mcknights.com": "mcknights_ltc_rss",
    "bondoro.com": "bondoro_rss",
    "whatnow.com": "whatnow_rss",
    "seniorcare.levinassociates.com": "levin_sci",
    "news.google.com": "gnews_rss",
    "warnfirehose.com": "warn",
    "web.archive.org": "wayback",
    "apps.hhs.texas.gov": "tx_hhsc",
    "hud.gov": "hud",
    "medicare.gov": "cms_care_compare",       # Care Compare page the site links for a CCN
}
# Sources the nightly refresh (daily3) would pull. The Step 0 decision rule applies to these only.
DAILY3_CANDIDATES = ("courtlistener", "shn_rss", "snn_rss", "gnews_rss", "epiq", "stretto")


def host_of(url: str) -> str:
    h = urlsplit(url).netloc.lower()
    return h[4:] if h.startswith("www.") else h


def source_of(url: str | None) -> str | None:
    """source_id for a public http(s) link; None for licensed tags or anything that is not a web link."""
    if not isinstance(url, str) or not url.startswith(("http://", "https://")):
        return None
    host = host_of(url)
    if host == "data.cms.gov":
        return next((s for ds, s in CMS_DATASETS.items() if ds in url), "cms_care_compare")
    if host == "cms.gov":
        return "cms_sff" if "/sff-" in url else "cms_other"
    if host == "hud.gov":
        return "hud_terminated" if url.endswith("-T.xlsx") else "hud_active" if url.endswith("-A.xlsx") else "hud"
    if host == "apps.hhs.texas.gov":
        return "tx_hhsc_closures" if "/Closures/" in url else "tx_hhsc"
    return HOSTS.get(host) or "web:" + host
