-- Leads table for the distressed deal sourcing tool.
-- Run once in Supabase: SQL Editor > New query > paste > Run.
-- A NULL value means the fact was not found in the source (shown as NOT FOUND in the app).

create table if not exists public.leads (
  id text primary key,
  property text not null,
  city text not null,
  state text not null,
  asset_type text not null,
  units integer,
  engine text not null default 'A' check (engine in ('A', 'B')),
  distress_type text,
  stage text check (stage in ('Sale', 'Late', 'Middle', 'Early')),
  event_date date,
  buyer text,
  buyer_track_record text,
  receiver text,
  signals text[] not null default '{}',
  prior_loan_per_unit integer,
  sale_price_per_unit integer,
  exit_path text,
  -- [{ "label": "...", "url": "https://..." }]. The app hides any lead without one.
  sources jsonb not null default '[]',
  is_sample boolean not null default false,
  created_at timestamptz not null default now()
);

-- The website may only read. Only the collector (using the secret service key) can write.
alter table public.leads enable row level security;

drop policy if exists "Anyone can read leads" on public.leads;
create policy "Anyone can read leads" on public.leads for select to anon, authenticated using (true);
