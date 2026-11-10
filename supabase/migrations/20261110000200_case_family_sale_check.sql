-- Bankruptcy families + sale-process check.
-- A "family" is the set of jointly administered (or otherwise linked) stg.court_case rows that make up one bankruptcy.
-- A family running a sale process (sale motion, bidding procedures, stalking horse, auction, sale order) is a top lead.
-- Built by src/discover/courts/families.py and src/discover/courts/sale_check.py. Small tables (~100s of rows).

create table core.case_family (
  family_id          bigint generated always as identity primary key,
  family_name        text not null,
  court              text,                 -- CourtListener-style court code (e.g. 'txnb'); NULL = unknown
  lead_docket_no     text,                 -- normalized 'YY-NNNNN'
  filed_date         date,                 -- NULL = not found (never guessed)
  filed_date_source  text,                 -- URL the date came from
  grouping_method    text not null check (grouping_method in
                       ('lead_case','jointly_administered_text','same_debtor_name','two_day_rule','single_case','manual')),
  senior_housing     text not null check (senior_housing in ('yes','no','maybe')),
  within_36m         boolean,              -- NULL = unknown
  claims_agent_url   text,
  notes              text,
  updated_at         timestamptz not null default now()
);

create table core.case_family_member (
  family_id          bigint not null references core.case_family(family_id) on delete cascade,
  stg_court_case_id  bigint not null unique references stg.court_case(id) on delete cascade,
  join_method        text not null check (join_method in
                       ('lead_case','jointly_administered_text','same_debtor_name','two_day_rule','single_case','manual')),
  senior_housing     text not null check (senior_housing in ('yes','no','maybe')),
  label_source       text not null check (label_source in ('hand','keyword')),
  primary key (family_id, stg_court_case_id)
);

-- One row per family per channel: the best (latest-stage) evidence seen so far, plus when it was last checked.
create table core.sale_check (
  family_id           bigint not null references core.case_family(family_id) on delete cascade,
  channel             text not null check (channel in ('claims_agent','courtlistener','news')),
  checked_at          timestamptz not null default now(),
  sale_process_found  boolean not null,
  stage_text          text check (stage_text in
                        ('sale motion','bidding procedures','stalking horse','auction','sale order','sale closed')),
  buyer_name          text,                -- only when literally present in the evidence
  evidence_text       text check (length(evidence_text) <= 500),
  source_url          text not null,
  items_scanned       integer,
  requests_used       smallint not null default 0,
  note                text,
  primary key (family_id, channel)
);
create index sale_check_channel_checked on core.sale_check (channel, checked_at);

-- Same lock-down as 20261109000400_security.sql: RLS on, no policies, service role only.
alter table core.case_family enable row level security;
alter table core.case_family_member enable row level security;
alter table core.sale_check enable row level security;
revoke all on core.case_family, core.case_family_member, core.sale_check from anon, authenticated;
grant all on core.case_family, core.case_family_member, core.sale_check to service_role;
grant all on all sequences in schema core to service_role;
