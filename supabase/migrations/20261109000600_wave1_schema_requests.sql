-- Between waves 1 and 2: batched schema requests from docs/schema_requests.md (#1-#3). Additive only.

-- #1 cms-loader: the real facility name (seeds core.facility.canonical_name) and the affiliated chain.
alter table stg.cms_snf_enrollment
  add column if not exists provider_name           text,
  add column if not exists affiliation_entity_name text,
  add column if not exists affiliation_entity_id   text;

-- #2 hud-loader: loan balance and rate are in the file and help debt-clock signals.
alter table stg.hud_active_loan
  add column if not exists amortized_balance numeric,
  add column if not exists interest_rate     numeric;

-- #3 hud-loader: CLAIM terminations (lender filed an insurance claim) are a stronger distress signal.
alter table stg.hud_terminated_loan
  add column if not exists term_claim_type text;   -- 'CLAIM' / 'NONCLAIM' as given
