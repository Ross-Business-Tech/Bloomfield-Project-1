# Deal Radar: hand-over (final state, Nov 11, 2026)

## Where things stand

- **The site is live** on Render (service `deal-radar-umich`, branch `development`, free plan, password on every
  page). It serves a static snapshot of the public lead list; it has no database connection and no key at runtime.
  `/healthz` returns "ok" without a password; everything else needs `SITE_PASSWORD`.
- **The UI is map-first:** US map → state → city bubble → property pin → lead card (rank, score, stage, buyer status,
  signals with source links, public phone / website). Top bar: Map, Leads, Buyers, The numbers, Watch list, and How
  it works behind `?`.
- **Final numbers** (published snapshot, Nov 11, 2026, after the human review): 485 public leads = List A 206 +
  List B 228 + 51 other; List C 159 repeat buyers linked to 35 leads; 44 states; named buyer 43%; public contact 45%;
  watch list 7,255. The full table is in [README → Final state](README.md#final-state-nov-11-2026).
- **Nov 11 human review of the top 20:** new stages "Sale approved" / "Closed / sold" with dates (closed sales rank
  below live ones), buyers corrected from court filings (`config/manual_overrides.yaml`), the former-operator rule
  (533 → 485 leads), source links best page first. Open: roadmap items 11 (roundup rule for buyers, audit only) and
  12 (closing notices for approved sales). Hand-over files: `python -m src.web.final_files`.
- **Tests:** `pytest -q` → 295 passed. CI runs the generic leak guard on every pull request.
- The contacts import (`config/contacts_manual.csv` → `core.facility_contact`) and its migration are **done**;
  contacts show on the lead card and in CSV downloads.

## Read these first

1. `CLAUDE.md`: how to work in this repo (pull requests into `development`, never `main`).
2. `README.md` → *Final state*: what the site shows, how to publish, how to roll back.
3. `docs/DEPLOY_RENDER.md`: Render design, free-plan limits, troubleshooting.
4. `docs/PIPELINE_RULES.md` and `config/thresholds.yaml`: lead, list and scoring rules (assumptions are labeled).
5. `docs/CONTACT_RESEARCH.md`: how contacts were sourced and what is still missing.
6. `docs/ROADMAP.md`: what to build next.

## Publish (team laptop with `.env`)

```sh
.venv/bin/pytest -q
python -m src.web.publish            # dry run: rebuild, pack, leak + size + funnel checks, prints the Render calls
python -m src.web.publish --confirm  # keeps output/site_snapshot.prev.b64, uploads, deploys, waits, checks /healthz
```

Render `autoDeploy` is off: merging a pull request does not change the site until someone deploys. If the publish
command stops, read its message; do not retry it blindly.

## Roll back

- **Data:** Render dashboard → service → *Environment* → *Secret Files* → replace `site_snapshot.b64` with the
  contents of `output/site_snapshot.prev.b64` → *Manual Deploy → Deploy latest commit* → check `/healthz` = 200 and
  the snapshot date on the site. Then copy `prev.b64` over `output/site_snapshot.uploaded.b64`.
- **Code:** revert the pull request on `development` and deploy, or use *Rollback* on the previous deploy in the
  Render dashboard (restores the code build only; the Secret File is unchanged).
- An encrypted backup of the final snapshot and submission exports is kept off-repo; its password is in the macOS
  Keychain (service `deal-radar-backup`) on the publishing laptop.

## Known limits (honest list)

- **Map positions are ZIP-code centroids** (Census ZCTA Gazetteer, public domain). The CMS provider file blocks
  automated downloads, so exact building coordinates are 0%; see the roadmap item on a manual CMS file.
- **Contacts cover 45% of leads.** Missing values read `NOT FOUND`; candidate records for three buildings in
  `docs/contacts_needs_review.csv` need a manual check before import.
- **Assumptions to confirm with the client:** the +10 care-type fit for AL / IL / MC / CCRC, and the 100+ homes
  "mega-platform" cut-off (one platform's size is inferred from public reporting, flagged `inferred`).
- **Candidate buyers are not final** (stalking-horse bids). Some transferees are shown as an unnamed placeholder until
  a public sale order names them; placeholders never count as a named buyer.
- **Hand-entered public events** (`config/court_facts.yaml`) carry flags such as `borrower_name_mismatch` and
  `address_unconfirmed` until someone confirms the building by hand.
- The top-10 hand-check sheet is regenerated into `output/handcheck_top10.csv` (gitignored) after every rescore.

## Never

- Never commit `.env`, keys, the snapshot, anything under `output/` or `eval/private/`, licensed facts, or details of
  the client's past deals. The repo is public, including commit messages and PR text.
- Never put a licensed-data provider's name or a forbidden term in code, docs or the site; the private term list
  (`eval/private/forbidden_terms.txt`) and `tools/leak_guard.py` check this.
- Never push to `main`, and don't change `scripts/`, `.github/`, `.claude/`, `admin/` or `CLAUDE.md`.
- No PACER and no paid sources without the team's sign-off; never work around a site's bot check.

## UI work (preview with made-up data)

```sh
npm ci && npx playwright install chromium
npm run build:radar && npm run smoke:radar    # desktop + phone screenshots in smoke-results/
npm run preview:radar                         # publishes a 24-hour preview link with made-up data only
```

`npm run build` alone builds the older Vite starter page, not Deal Radar.
