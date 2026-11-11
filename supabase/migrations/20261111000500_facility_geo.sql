-- Map coordinates per building (Nov 11, 2026). Additive; no existing data changed.
-- coord_source: 'cms' = CMS Provider Information latitude / longitude (by CCN), 'zip_centroid' = US Census ZCTA
-- Gazetteer internal point of the building's ZIP (public domain). Every row keeps its public source_url.
-- Locked down like the rest of core: RLS on, service role only (the snapshot reads it).
create table core.facility_geo (
  facility_id   uuid primary key references core.facility(facility_id) on delete cascade,
  lat           double precision not null check (lat between -90 and 90),
  lon           double precision not null check (lon between -180 and 180),
  coord_source  text not null check (coord_source in ('cms', 'zip_centroid')),
  source_url    text not null check (source_url ~ '^https?://'),
  updated_at    timestamptz not null default now()
);
alter table core.facility_geo enable row level security;
revoke all on core.facility_geo from anon, authenticated;
grant all on core.facility_geo to service_role;
