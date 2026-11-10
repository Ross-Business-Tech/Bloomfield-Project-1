# Schema requests

Subagents: append a request here instead of editing `supabase/migrations/`. The orchestrator batches them into one migration between waves.

| # | Wave | Agent | Table | Change | Why | Status |
|---|---|---|---|---|---|---|
| 1 | 1 | cms-loader | stg.cms_snf_enrollment | add provider_name, affiliation_entity_name, affiliation_entity_id | dba_name is not the facility name; needed to seed core.facility | applied (20261109000600) |
| 2 | 1 | hud-loader | stg.hud_active_loan | add amortized_balance, interest_rate | debt-clock signals | applied (20261109000600) |
| 3 | 1 | hud-loader | stg.hud_terminated_loan | add term_claim_type | claim terminations = stronger distress | applied (20261109000600) |
| 4 | 1 | court-loader | stg.court_case | make court nullable | Epiq/Stretto often give no court | declined: keep NOT NULL, loaders store 'UNKNOWN'; core-builder reconciles court names |
| 5 | 2 | extractor | stg.extraction | (a) add `status text` ('extracted' / 'skipped_prefilter' / 'error') and `content_hash text` columns + index on (model, prompt_version, status); (b) optional child table `stg.extraction_property(extraction_id fk, ordinal, property_name, street, city, state, units, beds)` | (a) today status and content_hash live inside `fields` jsonb, so "unprocessed" and cache lookups need jsonb filters; pre-filter skips are rows with model='prefilter', prompt_version='kw-v1'. (b) one row per document holds only the primary property; other named properties go to `fields.other_properties` (jsonb array) until a child table exists. Workaround in place, no blocker. | deferred to Wave 3: workaround (fields jsonb) works; revisit if the scorer needs it |
| 5 | 2 | core-builder | core.facility_role | allow role 'indirect_owner' in the role check | CMS "5% or greater indirect ownership" orgs (~18k rows) are the parent companies / PE funds behind the operating LLC; skipped until allowed | applied (20261110000300) |
| 6 | 2 | core-builder | core.loan | add interest_rate numeric, current_balance numeric, termination_date date, termination_type text | HUD rate, amortized balance and termination date/type (hud_loan_terminated event_date); src/enrich fills these automatically once the columns exist (re-run `python -m src.enrich --step hud`) | applied (20261110000300) |
| 7 | 2 | core-builder | core.case_facility | add link_method text ('facility_name','debtor_org'), match_score numeric | court cases link either by facility-name match (also in facility_xref with review_status) or because the debtor is a known CMS owner/operator org; scorer should be able to tell them apart | applied (20261110000300) |
