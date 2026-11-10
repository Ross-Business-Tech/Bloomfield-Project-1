-- Wave 3.5 (Nov 10, 2026): licensed loan facts (licensed exports shared privately) and the signals built on them.
-- The facts live ONLY in schema `private` (no grants to anon / authenticated; loaded from a local gitignored file).
-- Signals built on them carry source_url 'licensed:<source>' (not a web link) and the lead gets the flag
-- 'licensed_private'; such leads are hidden from the public view app.v_lead_list but stay in the internal view
-- used for the CSV exports.
create schema if not exists private;
revoke all on schema private from anon, authenticated, public;

create table if not exists private.licensed_loan (
  loan_key        text primary key,          -- '<file tag>:<row key>'
  source_file     text not null,
  source_label    text not null,             -- the file's own source line
  loan_group      text,                      -- deal row label (portfolio)
  property_name   text not null,
  city            text,
  state           text,
  units           integer,
  deal_name       text,
  loan_status     text,
  orig_balance    numeric,
  orig_date       date,
  maturity_date   date,
  transfer_reason text,
  status_history  text,                      -- most recent month on the left
  months_delinquent_total numeric,
  facility_id     uuid references core.facility(facility_id) on delete set null,
  match_method    text,
  match_score     numeric,
  loaded_at       timestamptz default now()
);
alter table private.licensed_loan enable row level security;
revoke all on private.licensed_loan from anon, authenticated, public;

alter table core.signal drop constraint signal_signal_type_check,
  add constraint signal_signal_type_check check (signal_type in (
    'dscr_below_1','maturity_12m_low_dscr','delayed_opening','mechanics_lien','chapter_11',
    'receivership','deed_in_lieu','foreclosure','operator_turnover','low_occupancy','negative_noi',
    'price_collapse','sff_or_candidate','staffing_drop','hud_loan_terminated','warn_closure',
    'bridge_on_bridge',
    'chapter_7','loan_or_lease_default','closure','court_sale_process','market_sale',
    'hud_delinquency','special_servicing','lender_takeback','maturity_default'));

-- internal view (service role only): every main-list lead, licensed ones included
create or replace view app.v_lead_list_internal as
SELECT l.lead_id, f.canonical_name AS property, f.city, f.state, f.care_types, f.units, f.beds,
    l.distress_type, l.stage, COALESCE(l.buyer_name, o.name, 'NOT FOUND'::text) AS buyer, l.buyer_status, l.score,
    l.score_breakdown, l.flags, l.review_status, l.first_seen, l.last_updated, ev.source_urls, l.facility_id, f.ccn,
    'unconfirmed'::text = ANY (l.flags) AS unconfirmed, l.buyer_source_url, l.buyer_chain, l.tier
   FROM app.lead l
     JOIN core.facility f ON f.facility_id = l.facility_id
     LEFT JOIN core.organization o ON o.org_id = l.buyer_org_id
     JOIN LATERAL ( SELECT array_agg(DISTINCT s.source_url ORDER BY s.source_url) AS source_urls
           FROM app.lead_evidence e JOIN core.signal s ON s.signal_id = e.signal_id
          WHERE e.lead_id = l.lead_id AND (s.source_url ~ '^https?://'::text OR s.source_url ~ '^licensed:'::text)) ev
       ON ev.source_urls IS NOT NULL
  WHERE l.tier = 'main'::text
  ORDER BY l.score DESC NULLS LAST;
revoke all on app.v_lead_list_internal from anon, authenticated, public;

-- public view: same rows minus anything resting on licensed facts, and only web-link sources
create or replace view app.v_lead_list as
SELECT v.lead_id, v.property, v.city, v.state, v.care_types, v.units, v.beds, v.distress_type, v.stage, v.buyer,
    v.buyer_status, v.score, v.score_breakdown, v.flags, v.review_status, v.first_seen, v.last_updated,
    v.source_urls, v.facility_id, v.ccn, v.unconfirmed, v.buyer_source_url, v.buyer_chain, v.tier
   FROM app.v_lead_list_internal v
  WHERE NOT ('licensed_private'::text = ANY (coalesce(v.flags, '{}'::text[])))
    AND coalesce(v.buyer_source_url, '') !~ '^licensed:';
grant select on app.v_lead_list to anon, authenticated;

insert into raw.source (source_id, name, category, access_method, base_url, refresh_cadence, notes) values
  ('licensed_data', 'Licensed exports (shared privately)', 'debt_clock', 'manual', null, 'manual',
   'Licensed facts; local gitignored files in eval/private only; never committed; stored in schema private')
on conflict (source_id) do nothing;
