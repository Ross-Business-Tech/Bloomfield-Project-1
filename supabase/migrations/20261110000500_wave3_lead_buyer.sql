-- Wave 3 (signals-scorer): buyer text + chain performance on app.lead, and a richer app.v_lead_list.
-- A buyer named in a court sale order or a news article often has no core.organization row; the literal name
-- from the source is kept in buyer_name (never invented). buyer_chain = CMS chain performance of the buyer.
alter table app.lead
  add column if not exists buyer_name       text,
  add column if not exists buyer_source_url text check (buyer_source_url ~ '^https?://'),
  add column if not exists buyer_chain      jsonb;

-- create or replace keeps the existing columns (and the anon grant from 20261109000400); new columns are appended.
-- One row per lead, best first. A lead without at least one evidence link never appears (hard rule 1).
create or replace view app.v_lead_list as
select
  l.lead_id,
  f.canonical_name                                   as property,
  f.city,
  f.state,
  f.care_types,
  f.units,
  f.beds,
  l.distress_type,
  l.stage,
  coalesce(l.buyer_name, o.name, 'NOT FOUND')        as buyer,
  l.buyer_status,
  l.score,
  l.score_breakdown,
  l.flags,
  l.review_status,
  l.first_seen,
  l.last_updated,
  ev.source_urls,
  l.facility_id,
  f.ccn,
  'unconfirmed' = any(l.flags)                       as unconfirmed,
  l.buyer_source_url,
  l.buyer_chain
from app.lead l
join core.facility f on f.facility_id = l.facility_id
left join core.organization o on o.org_id = l.buyer_org_id
join lateral (
  select array_agg(distinct s.source_url order by s.source_url) as source_urls
  from app.lead_evidence e
  join core.signal s on s.signal_id = e.signal_id
  where e.lead_id = l.lead_id and s.source_url ~ '^https?://'
) ev on ev.source_urls is not null
order by l.score desc nulls last;
