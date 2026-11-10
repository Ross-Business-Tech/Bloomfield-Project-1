"""Florida AHCA FloridaHealthFinder assisted living facilities (source_id fl_ahca): MANUAL.

Findings (2026-11-09):
- quality.healthfinder.fl.gov/Facility-Provider/ALF is a JavaScript search form (name / city / zip / county /
  field office). There is no statewide ALF export; the page's only "Download" links are static files for
  other things (a Miami-Dade-only all-provider list with no licensee column, and a clinical lab list).
- Paid patient-level data is ordered by check; it is not an ALF license roster.
Paging through the form per county/zip would be scraping a search UI, which the project rules forbid when
a bulk file is not offered. So this source is marked manual. See README.md for what to do instead.
"""
from __future__ import annotations

from .base import ManualSource

SOURCE_ID = "fl_ahca"
PAGE_URL = "https://quality.healthfinder.fl.gov/Facility-Provider/ALF?&type=0"

MANUAL_REASON = (
    "fl_ahca is manual: FloridaHealthFinder ALF data is only available through a JavaScript search form "
    "(no statewide bulk export; the one static facility file is Miami-Dade only and has no licensee). "
    "Request the ALF roster from AHCA (public records) or add a bulk file path later."
)


def fetch(client, limit=None, today=None):
    raise ManualSource(MANUAL_REASON)
