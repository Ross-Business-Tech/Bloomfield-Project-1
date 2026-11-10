-- Wave 0 · migration 3 of 5: app layer, what Bloomfield sees.

create table app.lead (
  lead_id         uuid primary key default gen_random_uuid(),
  facility_id     uuid not null unique references core.facility(facility_id) on delete cascade,
  distress_type   text,
  stage           text check (stage in ('early','middle','late','sale')),
  buyer_org_id    uuid references core.organization(org_id),
  buyer_status    text not null default 'NOT FOUND' check (buyer_status in ('named','candidate','NOT FOUND')),
  score           numeric,
  score_breakdown jsonb not null default '{}',
  flags           text[] not null default '{}',
  first_seen      timestamptz not null default now(),
  last_updated    timestamptz not null default now(),
  review_status   text not null default 'new' check (review_status in ('new','call','pass','watch')),
  reviewer_note   text
);

create table app.lead_evidence (
  lead_id   uuid not null references app.lead(lead_id) on delete cascade,
  signal_id uuid not null references core.signal(signal_id) on delete cascade,
  primary key (lead_id, signal_id)
);

-- One row per lead, best first. A lead without at least one evidence link never appears (hard rule 1).
create view app.v_lead_list as
select
  l.lead_id,
  f.canonical_name                         as property,
  f.city,
  f.state,
  f.care_types,
  f.units,
  f.beds,
  l.distress_type,
  l.stage,
  coalesce(o.name, 'NOT FOUND')            as buyer,
  l.buyer_status,
  l.score,
  l.score_breakdown,
  l.flags,
  l.review_status,
  l.first_seen,
  l.last_updated,
  ev.source_urls
from app.lead l
join core.facility f on f.facility_id = l.facility_id
left join core.organization o on o.org_id = l.buyer_org_id
join lateral (
  select array_agg(distinct s.source_url order by s.source_url) as source_urls
  from app.lead_evidence e
  join core.signal s on s.signal_id = e.signal_id
  where e.lead_id = l.lead_id
) ev on ev.source_urls is not null
order by l.score desc nulls last;
