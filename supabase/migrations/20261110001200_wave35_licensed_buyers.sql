-- Wave 3.5 (Nov 10, 2026): buyer facts from licensed sources (shared privately), private only.
create table if not exists private.licensed_buyer (
  hint_key      text primary key,
  family_id     integer,                 -- core.case_family (bankruptcy family) the buyer applies to
  facility_id   uuid references core.facility(facility_id) on delete cascade,  -- or one home
  buyer_name    text not null,
  status        text not null check (status in ('named','candidate')),
  event_date    date,
  evidence_text text,
  source_label  text not null,           -- free-text label of the licensed source
  loaded_at     timestamptz default now(),
  check (family_id is not null or facility_id is not null)
);
alter table private.licensed_buyer enable row level security;
revoke all on private.licensed_buyer from anon, authenticated, public;

-- hard rule 1 (Wave 3.5 exception): a source is a web link OR a licensed-source tag ('licensed:<source>').
-- Leads resting on a licensed tag are flagged 'licensed_private' and hidden from app.v_lead_list.
alter table core.signal drop constraint signal_source_url_check,
  add constraint signal_source_url_check check (source_url ~ '^(https?://|licensed:)');
alter table app.lead drop constraint lead_buyer_source_url_check,
  add constraint lead_buyer_source_url_check check (buyer_source_url ~ '^(https?://|licensed:)');
