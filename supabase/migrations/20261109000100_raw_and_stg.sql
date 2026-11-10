-- Wave 0 · migration 1 of 5: schemas, raw layer (untouched inputs) and stg layer (typed copy per source).
-- Source of truth: docs/BUILD_SPEC.md. Only the orchestrator edits this folder.

create extension if not exists pgcrypto;  -- gen_random_uuid()

create schema if not exists raw;
create schema if not exists stg;
create schema if not exists core;
create schema if not exists app;

-- ───────────────────────────── raw ─────────────────────────────

create table raw.source (
  source_id       text primary key,
  name            text not null,
  category        text not null check (category in ('debt_clock','ownership','operating','legal','news')),
  access_method   text not null check (access_method in ('api','file','rss','html','manual')),
  base_url        text,
  refresh_cadence text,
  robots_ok       boolean,
  last_pulled_at  timestamptz,
  notes           text            -- e.g. why a source is manual
);

create table raw.ingest_run (
  run_id      uuid primary key default gen_random_uuid(),
  source_id   text not null references raw.source(source_id),
  started_at  timestamptz not null default now(),
  finished_at timestamptz,
  rows_in     integer not null default 0,
  status      text not null default 'running' check (status in ('running','success','failed','blocked')),
  error       text
);
create index on raw.ingest_run (source_id, started_at desc);

-- Structured rows (CMS, HUD). content_hash makes every loader idempotent.
create table raw.record (
  id           bigserial primary key,
  source_id    text not null references raw.source(source_id),
  run_id       uuid references raw.ingest_run(run_id),
  external_id  text,
  source_url   text not null,
  fetched_at   timestamptz not null default now(),
  payload      jsonb not null,
  content_hash text not null unique
);
create index on raw.record (source_id, external_id);

-- Text documents (news, court pages, PDFs as text).
create table raw.document (
  id             bigserial primary key,
  source_id      text not null references raw.source(source_id),
  run_id         uuid references raw.ingest_run(run_id),
  source_url     text not null unique,
  title          text,
  published_at   timestamptz,
  body_text      text,
  fetched_at     timestamptz not null default now(),
  content_hash   text not null,
  buyer_evidence boolean not null default false   -- court-loader: mentions stalking horse / purchaser / APA
);
create index on raw.document (source_id, published_at desc);

-- ───────────────────────────── stg ─────────────────────────────
-- One typed table per source, rebuilt from raw. Every row keeps raw_id + source_url for traceability.
-- IDs such as CCN, ZIP and license numbers are text so leading zeros survive.

create table stg.cms_chow (
  id                    bigserial primary key,
  raw_id                bigint references raw.record(id),
  source_url            text not null,
  effective_date        date,
  chow_type             text,
  state                 text,
  buyer_enrollment_id   text,
  buyer_ccn             text,
  buyer_associate_id    text,
  buyer_org_name        text,
  buyer_dba_name        text,
  seller_enrollment_id  text,
  seller_ccn            text,
  seller_associate_id   text,
  seller_org_name       text,
  seller_dba_name       text,
  unique (raw_id)
);
create index on stg.cms_chow (buyer_ccn);
create index on stg.cms_chow (seller_ccn);

create table stg.cms_chow_owner (
  id                  bigserial primary key,
  raw_id              bigint references raw.record(id),
  source_url          text not null,
  chow_side           text check (chow_side in ('buyer','seller')),
  enrollment_id       text,
  associate_id        text,
  organization_name   text,
  owner_associate_id  text,
  owner_type          text,           -- individual / organization as given
  owner_name          text,
  role_text           text,
  ownership_pct       numeric,
  association_date    date,
  unique (raw_id)
);

create table stg.cms_snf_owner (
  id                  bigserial primary key,
  raw_id              bigint references raw.record(id),
  source_url          text not null,
  enrollment_id       text,
  associate_id        text,
  organization_name   text,
  owner_associate_id  text,
  owner_type          text,
  owner_name          text,
  role_text           text,
  ownership_pct       numeric,
  association_date    date,
  is_private_equity   boolean,
  is_reit             boolean,
  is_holding_company  boolean,
  unique (raw_id)
);
create index on stg.cms_snf_owner (enrollment_id);

create table stg.cms_snf_enrollment (
  id                 bigserial primary key,
  raw_id             bigint references raw.record(id),
  source_url         text not null,
  enrollment_id      text not null unique,
  ccn                text,
  npi                text,
  associate_id       text,
  organization_name  text,
  dba_name           text,
  street             text,
  city               text,
  state              text,
  zip5               text,
  phone              text,
  organization_type  text,
  proprietary_nonprofit text
);
create index on stg.cms_snf_enrollment (ccn);

create table stg.cms_cost_report (
  id                       bigserial primary key,
  raw_id                   bigint references raw.record(id),
  source_url               text not null,
  ccn                      text not null,
  fiscal_year_end          date not null,
  facility_name            text,
  street                   text,
  city                     text,
  state                    text,
  zip5                     text,
  beds                     integer,
  total_days               numeric,
  total_bed_days_available numeric,
  occupancy_proxy          numeric,   -- total_days / total_bed_days_available
  net_income               numeric,
  total_revenue            numeric,
  unique (ccn, fiscal_year_end)
);

-- PBJ is aggregated to one row per CCN per quarter before insert (never store daily rows).
create table stg.cms_pbj_quarter (
  id               bigserial primary key,
  source_url       text not null,
  ccn              text not null,
  quarter          text not null,       -- e.g. 2026Q2
  quarter_end      date not null,
  days_reported    integer,
  avg_census       numeric,
  total_nurse_hprd numeric,
  rn_hprd          numeric,
  unique (ccn, quarter)
);

create table stg.cms_chain_perf (
  id              bigserial primary key,
  raw_id          bigint references raw.record(id),
  source_url      text not null,
  chain_id        text not null,
  chain_name      text,
  report_date     date,
  num_facilities  integer,
  num_states      integer,
  avg_overall_rating numeric,
  pct_sff         numeric,
  total_fines     numeric,
  measures        jsonb not null default '{}',   -- remaining measures as given
  unique (chain_id, report_date)
);

create table stg.cms_sff (
  id             bigserial primary key,
  source_url     text not null,
  report_month   date not null,
  ccn            text not null,
  facility_name  text,
  state          text,
  status         text not null check (status in ('sff','candidate')),
  months_on_list integer,
  unique (report_month, ccn)
);

create table stg.hud_active_loan (
  id              bigserial primary key,
  raw_id          bigint references raw.record(id),
  source_url      text not null,
  as_of           date not null,
  fha_number      text not null,
  property_name   text,
  street          text,
  city            text,
  state           text,
  zip5            text,
  units           integer,
  soa_code        text,
  soa_category    text,
  endorsement_date date,
  maturity_date   date,
  original_amount numeric,
  holder_name     text,
  servicer_name   text,
  unique (fha_number, as_of)
);

create table stg.hud_terminated_loan (
  id               bigserial primary key,
  raw_id           bigint references raw.record(id),
  source_url       text not null,
  as_of            date not null,
  fha_number       text not null,
  property_name    text,
  city             text,
  state            text,
  zip5             text,
  units            integer,
  soa_category     text,
  original_amount  numeric,
  termination_date date,
  termination_type text,            -- exactly as given by HUD
  unique (fha_number, as_of)
);

create table stg.court_case (
  id                bigserial primary key,
  source_url        text not null,   -- docket page
  court             text not null,
  docket_no         text not null,
  case_name         text,
  chapter           text,
  filed_date        date,
  terminated_date   date,
  debtor_name       text,
  claims_agent_url  text,
  courtlistener_id  text,
  unique (court, docket_no)
);

create table stg.state_license (
  id             bigserial primary key,
  source_url     text not null,
  as_of          date not null,      -- every snapshot is kept to detect licensee changes
  state          text not null,
  license_no     text not null,
  facility_name  text,
  street         text,
  city           text,
  zip5           text,
  licensee       text,
  license_type   text,
  capacity       integer,
  status         text,
  unique (state, license_no, as_of)
);

create table stg.extraction (
  id               bigserial primary key,
  document_id      bigint not null references raw.document(id),
  model            text not null,
  prompt_version   text not null,
  fields           jsonb not null,
  not_found_fields text[] not null default '{}',
  created_at       timestamptz not null default now(),
  unique (document_id, model, prompt_version)
);
