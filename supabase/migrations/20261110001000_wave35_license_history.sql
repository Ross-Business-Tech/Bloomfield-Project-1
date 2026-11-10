-- Wave 3.5 (Nov 10, 2026): licence snapshot history (weekly + Wayback backfill), management-company changes,
-- change windows (both dates) and Texas ALF closures.
alter table stg.state_license add column if not exists management_co text;
alter table stg.state_license add column if not exists snapshot_origin text default 'live';  -- 'live' | 'wayback'

alter table core.ownership_event add column if not exists prior_seen_date date;  -- last snapshot showing the old party
alter table core.ownership_event drop constraint if exists ownership_event_event_type_check,
  add constraint ownership_event_event_type_check
  check (event_type in ('chow','license_change','management_change','court_sale','deed','news_reported_sale'));

create table if not exists stg.state_closure (
  id            bigserial primary key,
  source_url    text not null,
  as_of         date not null,
  state         text not null,
  facility_key  text not null,       -- TX Facility ID (same key as stg.state_license.license_no)
  facility_name text,
  owner         text,
  street        text,
  city          text,
  zip5          text,
  closure_date  date,
  program       text,
  service_type  text,
  unique (state, facility_key, closure_date)
);
alter table stg.state_closure enable row level security;

insert into raw.source (source_id, name, category, access_method, base_url, refresh_cadence, notes) values
  ('tx_hhsc_closures', 'Texas HHSC ALF closures list', 'operating', 'file',
   'https://apps.hhs.texas.gov/providers/directories/Closures/alf_closures.xlsx', 'weekly', 'Cumulative closures; closure = hard event'),
  ('wayback', 'Internet Archive Wayback Machine (licence-file captures)', 'ownership', 'file',
   'https://web.archive.org', 'one-off', 'Backfill of NC/TX licence rosters; id_ raw captures; robots.txt 404')
on conflict (source_id) do nothing;
