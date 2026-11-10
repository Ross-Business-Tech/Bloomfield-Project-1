# BUILD_SPEC — technical spec (schema, sources, pipeline, tests)

Source of truth for all agents. Execution order and agent roles live in docs/EXECUTION_PLAYBOOK.md.

You are building the data pipeline for UMich Team 1 × Bloomfield Capital: an agent that finds distressed senior housing properties AND the buyer/new operator taking them over, so Bloomfield can offer a bridge loan before a broker is involved. Read README.md and docs/ first.

## Hard rules
1. Every signal and lead row must carry a working `source_url`. No URL → do not insert.
2. Unknown field → store `NOT FOUND` (text) or NULL plus a flag; never guess or impute.
3. The LLM step is extraction only (news/court text → JSON fields). Dates, stages, scoring and matching are done in code/SQL.
4. Be a polite crawler: descriptive User-Agent `UMich-Team1-research/0.1 (contact: <RESEARCH_CONTACT_EMAIL from .env>)`; never put the client's name in any request, header or email, obey robots.txt, ≤1 request/sec per host, cache responses. If a site returns 401/403 or blocks bots, do NOT work around it — mark the source `access_method='manual'` and move on.
5. Confidential: Bloomfield's past deals and emails never go into the repo, seed files, or migrations. Backtest data is loaded only from a local gitignored file into a private table.
6. Secrets in `.env` (SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, ANTHROPIC_API_KEY, COURTLISTENER_TOKEN); `.env` is gitignored.

## Stack
Python 3.11, `supabase-py` or `psycopg`, `httpx`, `pandas`, `openpyxl`, `pdfplumber`, `feedparser`, `rapidfuzz`. Supabase CLI migrations in `supabase/migrations/`. Code in `src/discover`, `src/extract`, `src/enrich`, `src/score` (per README §12).

## Database: 4 schemas (expose only `app` views through the Supabase API)

### raw — untouched inputs
- `raw.source(source_id text pk, name, category text check in ('debt_clock','ownership','operating','legal','news'), access_method text check in ('api','file','rss','html','manual'), base_url, refresh_cadence, robots_ok bool, last_pulled_at)`
- `raw.ingest_run(run_id uuid pk, source_id fk, started_at, finished_at, rows_in int, status, error)`
- `raw.record(id bigserial pk, source_id fk, run_id fk, external_id, source_url text not null, fetched_at, payload jsonb, content_hash text unique)` — structured rows (CMS, HUD)
- `raw.document(id bigserial pk, source_id fk, source_url text unique not null, title, published_at, body_text, fetched_at, content_hash)` — news, court pages, PDFs as text

### stg — one typed table per source (rebuilt from raw)
`stg.cms_chow`, `stg.cms_chow_owner`, `stg.cms_snf_owner`, `stg.cms_snf_enrollment`, `stg.cms_cost_report`, `stg.cms_pbj_quarter` (aggregate daily PBJ to one row per CCN per quarter: avg census, total nurse HPRD, RN HPRD — do not store 1.3M daily rows), `stg.cms_chain_perf`, `stg.cms_sff` (report_month, ccn, status sff/candidate, months_on_list), `stg.hud_active_loan`, `stg.hud_terminated_loan`, `stg.court_case`, `stg.state_license` (state, license_no, facility_name, address, licensee, license_type, status, as_of), `stg.extraction` (document_id fk, model, fields jsonb, not_found_fields text[], created_at).

### core — one row per real-world thing
- `core.facility(facility_id uuid pk, canonical_name, street, city, state, zip5, care_types text[], beds int, units int, ccn text unique null, created_at)`
- `core.facility_xref(facility_id fk, source_id fk, source_key text, match_method text check in ('exact_ccn','exact_license','fuzzy','manual'), match_score numeric, review_status text default 'auto', unique(source_id, source_key))`
- `core.organization(org_id uuid pk, name, normalized_name, cms_associate_id, chain_id, org_kind text)`
- `core.facility_role(facility_id, org_id, role text check in ('owner','operator','licensee','propco','opco','receiver','lender'), start_date, end_date, source_url not null)`
- `core.ownership_event(event_id uuid pk, facility_id, event_type text check in ('chow','license_change','court_sale','deed','news_reported_sale'), effective_date, seller_org_id, buyer_org_id, price numeric, source_url not null)`
- `core.loan(loan_id uuid pk, facility_id, loan_source text check in ('hud','cmbs','bridge_news'), lender_org_id, amount, origination_date, maturity_date, dscr, occupancy, status, source_url not null)`
- `core.legal_case(case_id uuid pk, court, docket_no, chapter, filed_date, debtor_name, status, docket_url, claims_agent_url)` + `core.case_facility(case_id, facility_id)`
- `core.metric(facility_id, metric text, period_end date, value numeric, source_id, source_url not null, primary key(facility_id, metric, period_end, source_id))` — occupancy_proxy, nurse_hprd, net_income, operating_margin, overall_rating, fines_total
- `core.signal(signal_id uuid pk, facility_id fk, category (same 5 values as raw.source), signal_type text, stage text check in ('early','middle','late','sale'), event_date date, value numeric, evidence_text, source_url text not null, detected_by text check in ('code','ai'), confidence numeric, created_at)`
  signal_type values: dscr_below_1, maturity_12m_low_dscr, delayed_opening, mechanics_lien, chapter_11, receivership, deed_in_lieu, foreclosure, operator_turnover, low_occupancy, negative_noi, price_collapse, sff_or_candidate, staffing_drop, hud_loan_terminated, warn_closure, bridge_on_bridge

### app — what Bloomfield sees
- `app.lead(lead_id uuid pk, facility_id fk unique, distress_type, stage, buyer_org_id null, buyer_status text check in ('named','candidate','NOT FOUND'), score numeric, score_breakdown jsonb, flags text[], first_seen, last_updated, review_status text default 'new' check in ('new','call','pass','watch'), reviewer_note)`
- `app.lead_evidence(lead_id, signal_id)`
- view `app.v_lead_list`: one row per lead with facility, city/state, units/beds, distress type, stage, buyer, score, and an array of source URLs, ordered by score desc.
Enable RLS on all tables; service role writes, anon reads only `app.v_lead_list`.

## Sources to ingest (verified reachable Nov 9, 2026)

Phase 1 — structured, free (build first):
| source_id | Endpoint | Notes |
|---|---|---|
| cms_chow | https://data.cms.gov/data-api/v1/dataset/f557a6ed-95b3-4a22-8433-4175db2dec1c/data | 5,340 rows; page with `?size=5000&offset=` |
| cms_chow_owner | https://data.cms.gov/data-api/v1/dataset/a4358712-e910-4eaf-8f24-5e90ba3cf8d0/data | 122k rows |
| cms_snf_owner | https://data.cms.gov/data-api/v1/dataset/afe44b85-cc6d-40d7-b5df-00ae8910d1d2/data | 301k rows; keep owner name/type/role/% + PE/REIT flags |
| cms_snf_enrollment | https://data.cms.gov/data-api/v1/dataset/5f2c306f-3b1c-42cd-b037-187b2ce22126/data | 14,404 rows; address + CCN → seed core.facility |
| cms_cost_report | https://data.cms.gov/data-api/v1/dataset/a69d3df7-3f66-4a0d-b5b8-0d66049bd565/data | FY2024 (lagged); occupancy = total days / bed days available; net income |
| cms_pbj | https://data.cms.gov/data-api/v1/dataset/7e0d53ba-8f02-4c66-98a5-14a1c997c50d/data | aggregate to quarter before insert |
| cms_chain_perf | https://data.cms.gov/data-api/v1/dataset/97ecfad1-d3f1-4d42-b774-d74661d830bc/data | 612 chains; buyer track record |
| cms_sff | https://www.cms.gov/files/document/sff-posting-candidate-list-october-2026.pdf | monthly PDF; parse tables with pdfplumber; URL pattern changes monthly |
| hud_active | https://www.hud.gov/sites/default/files/Housing/documents/FHA-BF90-RM-A.xlsx | header on row 2; keep SOA category containing 232/Nursing/Assisted/Board (~4,073 loans) |
| hud_terminated | https://www.hud.gov/sites/default/files/Housing/documents/FHA-BF90-RM-T.xlsx | inspect columns; termination type → hud_loan_terminated signal |
For CMS dataset IDs, re-resolve the latest version from https://data.cms.gov/data.json by title at run time (IDs change each release).

Phase 2 — text + courts:
| source_id | Endpoint | Notes |
|---|---|---|
| shn_rss | https://seniorhousingnews.com/feed/ | daily |
| snn_rss | https://skillednursingnews.com/feed/ | daily |
| gnews_rss | https://news.google.com/rss/search?q= + queries: "assisted living" receivership; "senior living" "chapter 11"; "nursing home" "new operator"; "memory care" foreclosure; "assisted living" "deed in lieu"; "senior living" "delayed opening" | daily |
| courtlistener | https://www.courtlistener.com/api/rest/v4/ (search + dockets, token required) | bankruptcy dockets mentioning assisted living / senior living / nursing / healthcare |
| stretto, epiq | https://cases.stretto.com · https://dm.epiq11.com | case pages only; sale motions → buyer |
| elevenflo | https://elevenflo.com | case pages only (robots blocks /search, /api/search/open) |
| mi_lara, fl_ahca | Michigan statewide HFA/AFC text file (README §7) · https://quality.healthfinder.fl.gov | assisted living licensee changes = operator turnover |
| warn | https://www.warnfirehose.com/layoffs | filter to senior living / nursing |

Manual only (log in raw.source, no scraper): cmbs.news (robots blocks /api/), MSRB EMMA, Kroll portal (403), CMS Care Compare provider-data (403), county recorders, Freddie Mac loan-level (registration).

## Pipeline steps
1. Migrations for the 4 schemas + seed `raw.source`.
2. Phase 1 loaders → raw → stg (idempotent; content_hash dedupe; one ingest_run row per run).
3. Build `core.facility` from CMS enrollments (ccn as exact key). Add HUD, licenses and court/news facilities via fuzzy match: normalize name (drop LLC/Inc/propco/opco, expand Ctr/Rehab), normalize street (St/Ave/Rd), block on state+zip5 (fallback state+city), score = 0.35·name token_set_ratio + 0.45·street token_sort_ratio + 20 if street numbers equal − 25 if they differ + 10 if phone equal. ≥90 auto-match, 75–89 `review_status='needs_review'`, <75 new facility. Address outweighs name because facilities are renamed after a sale.
4. Extraction (Claude): for each new raw.document, return strict JSON: property_name, street, city, state, units, beds, care_type, distress_type, stage_text, debtor_or_owner, receiver_or_trustee, buyer_or_new_operator, lender, event_dates, amounts. Any field not literally in the text = "NOT FOUND". Store in stg.extraction with the model name. No scoring in the prompt.
5. Signal rules in SQL/Python (examples): cost-report occupancy < 0.70 → low_occupancy; net income < 0 → negative_noi; PBJ census down >15% over 4 quarters → staffing_drop; on SFF/candidate list → sff_or_candidate; ≥2 CHOW/licensee changes in 36 months → operator_turnover; HUD termination → hud_loan_terminated; extraction distress_type → chapter_11/receivership/foreclosure/deed_in_lieu. Thresholds live in one config file and are flagged as assumptions.
6. Buyer linking: buyer = CHOW buyer org, court sale-motion buyer, license new licensee, or extracted buyer_or_new_operator; else 'NOT FOUND'. Attach chain_perf metrics to the buyer.
7. Scoring in code: stage weight (sale 40, late 30, middle 20, early 10) + recency + buyer named (+20) + buyer chain quality + size fit (units/beds as $3–30M proxy) + HUD exit path (+10 if the buyer has prior Sec. 232 loans). Write score_breakdown jsonb.
8. `app.v_lead_list` + CSV export to output/.

## Acceptance checks (write as tests in eval/)
- 100% of core.signal and app.lead rows have a non-null source_url that returns HTTP 200 (sample 20).
- Every missing extracted field = "NOT FOUND" on the 20 test articles; ≥18/20 extracted correctly.
- Re-running any loader inserts 0 duplicate rows.
- Phase 1 alone produces ≥100 leads with stage, score and buyer_status.
- Backtest hook: a script reads a local gitignored file of past-deal addresses and reports whether each appears in app.lead (never commit that file).
