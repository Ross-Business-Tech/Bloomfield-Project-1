# State license loaders (assisted living)

Purpose: CMS has no assisted-living coverage, so state license rosters give us facilities and their
licensee (operator). Because every snapshot is kept, a changed `licensee` for the same `license_no`
across `as_of` dates signals an operator change.

Run: `python -m src.discover.licenses [--source mi_lara|fl_ahca] [--limit N] [--dry-run] [--refresh]`
(`--dry-run` downloads, parses and prints counts and 3 samples with no database; `--refresh` ignores the disk cache.)

Writes: `raw.record` (external_id = license_no, source_url = file URL, payload = every column of the
source record) and `stg.state_license`, upserted on `(state, license_no, as_of)`. Re-runs add no duplicates.
License numbers and ZIPs are text. Empty source fields become NULL.

## Michigan (`mi_lara`) - working
- Page: https://www.michigan.gov/lara/bureau-list/bchs/adult/online-lookups/statewide-text-file-of-adult-foster-care-homes-for-the-aged-facilities
- File (link found on the page, regenerated daily by LARA): https://documents.apps.lara.state.mi.us/bchs/afc_sw.txt
- Record description: https://www.michigan.gov/lara/bureau-list/bchs/adult/online-lookups/adult-foster-care-record-description
- Format: no header row, comma-separated, double-quoted text, CRLF, 31 columns (A..AE), ~1 MB, ~4,480 rows.
- `as_of` = the file's HTTP `Last-Modified` date (falls back to the download date if the header is missing).
- Mapping: license_no = C; facility_name = D; street = F (or E when F is empty); city = G; zip5 = first 5
  digits of I (file has ZIP+4 without a dash); licensee = W; license_type = B code + label; capacity = K;
  status = N (facility license status: REGULAR / TEMPORARY / 1ST PROVISIONAL / EXEMPT / NONE).
  Licensee mailing address, phone, effective/expiry dates and the Y/N service flags are preserved in
  `raw.record.payload` only.
- Types in file: AS small group 1-6 (2,487), AF family home 1-6 (421), AM medium group 7-12 (425),
  AL large group 13-20 (556), AG congregate >20 (4), AI county infirmary >20 (2), AH Home for the Aged (351),
  XH (234).
- **Kept: AH, XH, AL, AG, AI = 1,147 rows** (assisted-living-like and larger). Dropped: AS, AF, AM (small
  group homes, 12 beds or fewer, not investable assets).
- Quirks: `XH` is not in LARA's record description. Every XH row has status EXEMPT, empty license dates and
  large capacities (e.g. 36, 304), so we treat it as an exempt Home for the Aged (our inference; verify
  with LARA before relying on it). When both address columns E and F are filled, E holds the unit
  ("Suite 4") and F the street. The file lists only currently ACTIVE licensees, so closed licenses just
  disappear between snapshots. Licensee status (AE) is always ACTIVE in the file.

## Florida (`fl_ahca`) - MANUAL
- Site: https://quality.healthfinder.fl.gov (Facility-Provider/ALF). robots.txt is a 404 (no restrictions) and
  nothing was blocked, but the ALF data is only reachable through a JavaScript search form (name, city, zip,
  county or field office; the "ALL" search asks you to narrow it). There is no statewide ALF export.
- The only static download linked there is `/files/AllFacilityData_MiamiDade_<date>.xlsx`: Miami-Dade only,
  all provider types, columns File Number / Facility / Type / Street / City / Zip / Phone / Licensed Beds, and
  no licensee, so it cannot detect operator changes.
- We do not page through the search form (project rule: no scraping around a missing bulk file). Options:
  a public-records request to AHCA for the ALF roster with licensee/owner, or a one-off manual export.
  When a file exists, add `florida.py` parsing into the same `LicenseRow` shape.

## Adding a state
1. Create `<state>.py` with `SOURCE_ID`, `fetch(client, limit=None, today=None) -> ParseResult`.
2. Use `PoliteClient(source_id)`; let `SourceBlocked` propagate; raise `ManualSource` when no bulk file exists.
3. Register it in `SOURCES` in `__main__.py`, add a `raw.source` row (orchestrator migration), add a fixture test.
