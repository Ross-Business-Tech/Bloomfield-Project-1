# Contact research — 11 November 2026 (UTC)

The import file is `config/contacts_manual.csv`: **256 records covering 240 facilities: 240 phones and 16 official websites**. Every row has a public source. These are real facility business contacts, not test fixtures. Neither the preview generator nor the website reads this CSV directly.

`contact_sources.csv` is the review ledger: CMS certification number (CCN), official facility name, city/state, value, source, source date where available, check date and match notes. It contains no lead scores, transaction signals, buyers, patient data or personal contacts. Street addresses on the website still come from the existing public facility view; this batch does not overwrite addresses.

## Coverage and matching

The authenticated Render page exposed 252 List A and 218 List B rows in its snapshot generated at `2026-11-11T01:41:27+00:00`. The CSV download did not complete in this environment, so the same site's authenticated snapshot page was used to read only facility names, cities and states. No password or session credential was extracted. The snapshot and working roster are not committed.

All **470 facilities** were compared with the two official CMS directories below. We accepted only a unique CCN/phone with an exact facility name, city and state after normalizing case, punctuation and `&`/`AND`. We did not accept fuzzy matches, infer truncated names, or fill missing identifiers. This produced 122 matches from the SFF file and 118 additional matches from the qualifying-home directory.

**230 facilities have no import-ready phone in this batch.** This is a partial contact enrichment, not a completed individual website search for all 470 facilities. A missing entry can reflect a clipped PDF name, a renamed institution, an assisted-living property outside CMS, conflicting data or an unverified identity. It does not establish that the facility has no public phone or website. Unfilled website fields also remain NOT FOUND. Continue with the full CMS Provider Information CSV and individual official pages on the team computer.

The 16 websites were inspected individually. Facility name, address and phone were compared with CMS, and renamed properties have notes in the ledger. Group hotlines, fax numbers, personal names, email addresses and individual mobile numbers were excluded. Axiom Healthcare and Axiom Gardens are separate facilities even within the same city.

`contacts_needs_review.csv` holds four source-backed candidate records for three properties. It is **not an import file** and deliberately has no `ccn` or `facility_id` column. Fountains of Hope has an explicit official rename; Toney Rest Home needs the internal UUID; AdviniaCare Orchard has a city discrepancy. Do not promote them until the database identity is checked. Its phone source for Toney is older (January 2026), so refresh it first.

## Primary sources

| Source | Edition | Use |
| --- | --- | --- |
| [CMS SFF directory](https://www.cms.gov/files/document/sff-posting-candidate-list-october-2026.pdf) | Updated 28 October 2026 | Facility CCN, name, location and phone only |
| [CMS qualifying nursing homes](https://www.cms.gov/files/document/list-qualifying-nursing-homes.pdf) | PDF metadata: 22 October 2026; title: Nursing for Nursing Homes Oct-2026 | Additional exact matches only; spreadsheet text can be clipped |
| [CMS Provider Information](https://data.cms.gov/provider-data/dataset/4pq5-n9py) | Download the current edition on the team computer | The CSV/API download returned Access Denied here; no values were taken from that failed download |

PDF links in the import file include `#page=N`, where N is the **one-based file page**, not the document's printed page label. The directory URL may be replaced by CMS over time. Downloaded editions had these SHA-256 values:

```text
SFF October 2026: b2857bf3cda8ac86da7e97f3fd9b2f3c5bad8fcadb229f6dc8cbf923c0059370
Qualifying homes: ff791400df2a19982bf2096c9e3650d9aa3bf62ecc40664f75a856e664148848
```

The check date records an online source check, not a test phone call. The importer currently stamps `verified_at` with the import date, so retain this ledger to distinguish research date from load date. Recheck stale or conflicting entries before a later import. Exact database membership cannot be checked on this machine.

## Local validation

```sh
python -m src.enrich.contacts manual --csv config/contacts_manual.csv --dry-run
```

Expected: `256 usable rows, 0 skipped`. Dry-run checks syntax without connecting to the database. It does **not** verify that a CCN exists in `core.facility`, or that the source is still current. The CLI currently exits successfully even when rows are skipped; read its counts and messages.
