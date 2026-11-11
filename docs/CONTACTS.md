# Property contact details

Each lead on the website shows how to reach the building: main phone line, public website and street address, each
with the public page it came from and the date it was checked. When nothing is known the site says NOT FOUND.

## What is stored
- Table `core.facility_contact` (migration `20261111000300_facility_contact.sql`): one row per phone / website of a
  building, with `source_url` (required, a public web link) and `verified_at`.
- View `app.v_facility_contact`: one row per building (address from `core.facility`, best phone, best website). Not
  readable with the public key; only the snapshot script reads it.
- **Business contact points only.** No personal names, no personal e-mail addresses, no mobile numbers. Nothing from the
  privately shared (licensed) files. Manual rows with a kind other than phone / website, or without a public link, are refused.

## How to fill it (on a team laptop with the pipeline set up)
1. Apply the migration in Supabase (SQL editor, or your usual migration step).
2. **Phone numbers for every nursing home (CMS):** download the Nursing Home *Provider Information* csv from
   https://data.cms.gov/provider-data/topics/nursing-homes and run
   `.venv/bin/python -m src.enrich.contacts cms --csv path/to/ProviderInfo.csv --dry-run` (check), then without `--dry-run`.
   Buildings are matched by CMS certification number (CCN).
3. **Websites and assisted-living phones (hand-entered):** copy `config/contacts_manual.example.csv`, add one row per
   contact with the page you found it on, then `.venv/bin/python -m src.enrich.contacts manual --csv <file> [--dry-run]`.
   Use the leads list to decide which buildings matter first (List A, then List B).
4. Make a new snapshot (`python -m src.web.snapshot`) and upload it as described in `DEPLOY_RENDER.md`.

Re-running a load is safe. If the migration has not been applied yet the snapshot still builds (contacts show NOT FOUND).
