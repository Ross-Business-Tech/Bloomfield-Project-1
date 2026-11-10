-- Gate 3 decisions (Nov 10, 2026):
-- 2d: early-stage-only leads need >= 3 different signals for the main list (config scoring.early_main_min_signals);
--     the rest go to a watch list instead of being deleted. app.v_lead_list = main list (anon may read it, as before);
--     app.v_watch_list = watch list, service role only.
alter table app.lead add column if not exists tier text not null default 'main' check (tier in ('main','watch'));

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
  l.buyer_chain,
  l.tier
from app.lead l
join core.facility f on f.facility_id = l.facility_id
left join core.organization o on o.org_id = l.buyer_org_id
join lateral (
  select array_agg(distinct s.source_url order by s.source_url) as source_urls
  from app.lead_evidence e
  join core.signal s on s.signal_id = e.signal_id
  where e.lead_id = l.lead_id and s.source_url ~ '^https?://'
) ev on ev.source_urls is not null
where l.tier = 'main'
order by l.score desc nulls last;

create or replace view app.v_watch_list as
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
  l.buyer_chain,
  l.tier
from app.lead l
join core.facility f on f.facility_id = l.facility_id
left join core.organization o on o.org_id = l.buyer_org_id
join lateral (
  select array_agg(distinct s.source_url order by s.source_url) as source_urls
  from app.lead_evidence e
  join core.signal s on s.signal_id = e.signal_id
  where e.lead_id = l.lead_id and s.source_url ~ '^https?://'
) ev on ev.source_urls is not null
where l.tier = 'watch'
order by l.score desc nulls last;

revoke all on app.v_watch_list from anon, authenticated;
grant select on app.v_watch_list to service_role;
