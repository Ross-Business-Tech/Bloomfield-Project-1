-- Wave 0 · migration 2 of 5: core layer, one row per real-world thing.
-- Two hubs: core.facility (the building) and core.organization (buyer / owner / operator).
-- Satellites never join each other directly; always through a hub.
-- source_key columns let builders upsert idempotently (re-runs insert 0 duplicates).

-- Hub 1
create table core.facility (
  facility_id    uuid primary key default gen_random_uuid(),
  canonical_name text not null,
  street         text,
  city           text,
  state          text,
  zip5           text,
  phone          text,
  care_types     text[] not null default '{}',   -- IL / AL / MC / SNF / CCRC
  beds           integer,
  units          integer,
  ccn            text unique,                    -- null for non-CMS facilities
  created_at     timestamptz not null default now()
);
create index on core.facility (state, zip5);
create index on core.facility (state, city);

create table core.facility_xref (
  facility_id   uuid not null references core.facility(facility_id) on delete cascade,
  source_id     text not null references raw.source(source_id),
  source_key    text not null,
  match_method  text not null check (match_method in ('exact_ccn','exact_license','fuzzy','manual')),
  match_score   numeric,
  review_status text not null default 'auto' check (review_status in ('auto','needs_review','confirmed','rejected')),
  unique (source_id, source_key)
);
create index on core.facility_xref (facility_id);

-- Hub 2
create table core.organization (
  org_id           uuid primary key default gen_random_uuid(),
  name             text not null,
  normalized_name  text not null,
  cms_associate_id text unique,
  chain_id         text,
  org_kind         text
);
create index on core.organization (normalized_name);

-- Satellites (all attach through a hub, all carry source_url)
create table core.facility_role (
  id          bigserial primary key,
  facility_id uuid not null references core.facility(facility_id) on delete cascade,
  org_id      uuid not null references core.organization(org_id) on delete cascade,
  role        text not null check (role in ('owner','operator','licensee','propco','opco','receiver','lender')),
  start_date  date,
  end_date    date,
  source_url  text not null,
  source_key  text not null unique
);
create index on core.facility_role (facility_id);
create index on core.facility_role (org_id);

create table core.ownership_event (
  event_id       uuid primary key default gen_random_uuid(),
  facility_id    uuid not null references core.facility(facility_id) on delete cascade,
  event_type     text not null check (event_type in ('chow','license_change','court_sale','deed','news_reported_sale')),
  effective_date date,
  seller_org_id  uuid references core.organization(org_id),
  buyer_org_id   uuid references core.organization(org_id),
  price          numeric,
  source_url     text not null,
  source_key     text not null unique
);
create index on core.ownership_event (facility_id, effective_date desc);

create table core.loan (
  loan_id          uuid primary key default gen_random_uuid(),
  facility_id      uuid not null references core.facility(facility_id) on delete cascade,
  loan_source      text not null check (loan_source in ('hud','cmbs','bridge_news')),
  lender_org_id    uuid references core.organization(org_id),
  amount           numeric,
  origination_date date,
  maturity_date    date,
  dscr             numeric,
  occupancy        numeric,
  status           text,
  source_url       text not null,
  source_key       text not null unique
);
create index on core.loan (facility_id);

create table core.legal_case (
  case_id          uuid primary key default gen_random_uuid(),
  court            text not null,
  docket_no        text not null,
  chapter          text,
  filed_date       date,
  debtor_name      text,
  status           text,
  docket_url       text not null,
  claims_agent_url text,
  unique (court, docket_no)
);

create table core.case_facility (
  case_id     uuid not null references core.legal_case(case_id) on delete cascade,
  facility_id uuid not null references core.facility(facility_id) on delete cascade,
  primary key (case_id, facility_id)
);

create table core.metric (
  facility_id uuid not null references core.facility(facility_id) on delete cascade,
  metric      text not null,   -- occupancy_proxy, nurse_hprd, net_income, operating_margin, overall_rating, fines_total
  period_end  date not null,
  value       numeric,
  source_id   text not null references raw.source(source_id),
  source_url  text not null,
  primary key (facility_id, metric, period_end, source_id)
);

-- Hard rule: no source_url → no signal.
create table core.signal (
  signal_id     uuid primary key default gen_random_uuid(),
  facility_id   uuid not null references core.facility(facility_id) on delete cascade,
  category      text not null check (category in ('debt_clock','ownership','operating','legal','news')),
  signal_type   text not null check (signal_type in (
                  'dscr_below_1','maturity_12m_low_dscr','delayed_opening','mechanics_lien','chapter_11',
                  'receivership','deed_in_lieu','foreclosure','operator_turnover','low_occupancy','negative_noi',
                  'price_collapse','sff_or_candidate','staffing_drop','hud_loan_terminated','warn_closure',
                  'bridge_on_bridge')),
  stage         text not null check (stage in ('early','middle','late','sale')),
  event_date    date,
  value         numeric,
  evidence_text text,
  source_url    text not null check (source_url ~ '^https?://'),
  detected_by   text not null check (detected_by in ('code','ai')),
  confidence    numeric,
  source_key    text not null unique,
  created_at    timestamptz not null default now()
);
create index on core.signal (facility_id);
