-- Property contact information (Nov 11, 2026): so a client can reach the building behind a lead.
--
-- Rules (same spirit as the rest of the schema):
--   * BUSINESS contact points only: the main phone line and the public website of the building. No personal names,
--     no personal e-mail addresses, no mobile numbers (the state-licence loaders already drop those).
--   * Every row carries the public web page it came from (source_url), like every other evidence table.
--   * Only public sources feed this table: the CMS Provider Information file, or a hand-entered row with a public link.
--     Nothing from the privately shared (licensed) files.
--   * Locked down like everything else: RLS on, no anon access. Only the service role (the pipeline / snapshot) reads it.

create table core.facility_contact (
  contact_id   uuid primary key default gen_random_uuid(),
  facility_id  uuid not null references core.facility(facility_id) on delete cascade,
  kind         text not null check (kind in ('phone', 'website')),
  value        text not null,                       -- phone: 10 digits, no punctuation; website: https://... address
  label        text not null default 'Main line',   -- what the contact is, e.g. 'Main line', 'Admissions'
  source_id    text not null,                       -- 'cms_provider_info' or 'contact_manual'
  source_url   text not null check (source_url ~ '^https?://'),
  verified_at  date not null default current_date,  -- when the source was read
  created_at   timestamptz not null default now(),
  unique (facility_id, kind, value)
);
create index on core.facility_contact (facility_id, kind);

alter table core.facility_contact enable row level security;
revoke all on core.facility_contact from anon, authenticated;
grant all on core.facility_contact to service_role;

-- One row per building with what the website needs: address (from core.facility), the best phone and website.
-- Falls back to core.facility.phone when no sourced phone row exists. Not granted to anon.
create or replace view app.v_facility_contact as
select f.facility_id, f.street, f.city, f.state, f.zip5,
       coalesce(p.value, f.phone)       as phone,
       p.label                          as phone_label,
       p.source_url                     as phone_source_url,
       w.value                          as website,
       w.source_url                     as website_source_url,
       greatest(p.verified_at, w.verified_at) as contact_checked
  from core.facility f
  left join lateral (select c.value, c.label, c.source_url, c.verified_at from core.facility_contact c
                      where c.facility_id = f.facility_id and c.kind = 'phone'
                      order by c.verified_at desc, c.created_at desc limit 1) p on true
  left join lateral (select c.value, c.source_url, c.verified_at from core.facility_contact c
                      where c.facility_id = f.facility_id and c.kind = 'website'
                      order by c.verified_at desc, c.created_at desc limit 1) w on true
 where coalesce(p.value, f.phone) is not null or w.value is not null or f.street is not null;
revoke all on app.v_facility_contact from anon, authenticated;
grant select on app.v_facility_contact to service_role;
