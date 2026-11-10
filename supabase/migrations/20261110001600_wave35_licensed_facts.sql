-- Wave 3.5 (Nov 10, 2026): row-level distress / buyer facts from licensed bankruptcy research, schema private only.
-- One row per fact row of the private file; links to the facility hub / organization / bankruptcy family are added
-- by the loader (NOT FOUND = NULL). Signals and buyer hints built from it carry source 'licensed:<source>' and are
-- hidden from the public view like the other licensed facts.
create table if not exists private.licensed_fact (
  fact_key        text primary key,
  article_date    date,
  debtor          text,
  chapter         text,
  filed_date      date,
  court_case      text,                  -- court + case number; NULL = NOT FOUND (no court proof)
  property_name   text,
  city            text,
  state           text,
  units           integer,
  care_type       text,
  distress_types  text[],                -- as written in the file
  stage_text      text,
  buyer_name      text,
  buyer_status    text,
  buyer_type      text,
  lender          text,
  key_amounts     text,
  key_dates       text,
  fit_note        text,
  list_change     text,                  -- NEW DISTRESS / NEW BUYER / BUYER INTEL ...
  flags           text[],
  facility_id     uuid references core.facility(facility_id) on delete set null,
  family_id       integer,               -- core.case_family
  debtor_org_id   uuid,
  buyer_org_id    uuid,
  link_method     text,                  -- name_city_state / family_member / new / none
  link_score      numeric,
  source_label    text not null,
  loaded_at       timestamptz default now()
);
alter table private.licensed_fact enable row level security;
revoke all on private.licensed_fact from anon, authenticated, public;
