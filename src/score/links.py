"""Source links in reading order (Nov 11 hand check): the page a person can read about THIS property first, court
pages next, the facility's Medicare Care Compare page instead of raw CMS API links (kept last as "CMS dataset (raw)")."""
from __future__ import annotations

import re
from urllib.parse import urlparse

RAW_LABEL = "CMS dataset (raw)"
_HOST_LABEL = {
    "seniorhousingnews.com": "Senior Housing News", "skillednursingnews.com": "Skilled Nursing News",
    "seniorshousingbusiness.com": "Seniors Housing Business", "seniorcare.levinassociates.com": "Levin Associates",
    "elevenflo.com": "ElevenFlo case summary", "courtlistener.com": "CourtListener docket",
    "dm.epiq11.com": "Epiq claims agent", "document.epiq11.com": "Court filing (Epiq)",
    "casedocs.omniagentsolutions.com": "Court filing (Omni)", "bondoro.com": "Bondoro filing alert",
    "altswire.com": "AltsWire", "cms.gov": "CMS document", "hud.gov": "HUD loan file",
    "medicare.gov": "Medicare Care Compare (this facility)", "apps.hhs.texas.gov": "Texas HHSC licence file",
}
_COURT = ("courtlistener.com", "elevenflo.com", "epiq11.com", "stretto.com", "omniagentsolutions.com", "kccllc.net",
          "veritaglobal.net", "inforuptcy.com", "bondoro.com")


def host(url: str) -> str:
    return (urlparse(url).hostname or "").removeprefix("www.")


def is_raw_cms(url: str) -> bool:
    return host(url) == "data.cms.gov"


def care_compare(ccn) -> str | None:
    """Medicare Care Compare page of a nursing home by its 6-digit CCN (a re-enrolment letter suffix is dropped)."""
    m = re.fullmatch(r"(\d{6})[A-Z]?", str(ccn or "").strip())
    return f"https://www.medicare.gov/care-compare/details/nursing-home/{m[1]}" if m else None


def label(url: str) -> str:
    if is_raw_cms(url):
        return RAW_LABEL
    h = host(url)
    return _HOST_LABEL.get(h) or next((v for k, v in _HOST_LABEL.items() if h.endswith("." + k)), h)


def _rank(url: str) -> int:
    h = host(url)
    if is_raw_cms(url):
        return 9
    if h == "medicare.gov":
        return 3
    if any(h == c or h.endswith("." + c) for c in _COURT):
        return 1 if "document" in h or "casedocs" in h else 2          # a filing naming the property beats a case page
    if h.endswith(".gov") or h.endswith("web.archive.org"):
        return 4
    return 0                                                             # article / operator page about this property


def ordered(urls, first=(), ccn=None) -> list[dict]:
    """[{url, label}] best human-readable page first. `first`: property-specific links (buyer / sale evidence) that
    lead the list. An article link with a '#deal-N' anchor replaces the same article without it."""
    seen, out = set(), []
    urls = [u for u in [*first, *(urls or [])] if isinstance(u, str) and u.startswith(("http://", "https://"))]
    anchored = {u.split("#")[0] for u in urls if "#" in u}
    lead = [u for u in first if u in urls and not is_raw_cms(u)]
    rest = sorted((u for u in urls if u not in lead), key=lambda u: (_rank(u), urls.index(u)))
    cc = care_compare(ccn) if any(is_raw_cms(u) for u in urls) or ccn else None
    for u in [*lead, *rest, *([cc] if cc else [])]:
        if u in seen or ("#" not in u and u in anchored):
            continue
        seen.add(u)
        out.append({"url": u, "label": label(u)})
    out.sort(key=lambda x: (0 if x["url"] in lead else 1, _rank(x["url"]) if x["url"] not in lead else 0))
    return out


def stage_text(stage: str | None, sale: dict | None) -> str | None:
    """CSV / deck wording of the stage: 'sale approved (2026-10-13)', 'closed / sold (2026-05-01)', else the stage."""
    if not sale:
        return stage
    word = "closed / sold" if sale.get("status") == "closed" else "sale approved"
    return f"{word} ({sale.get('date') or 'date NOT FOUND'})"
