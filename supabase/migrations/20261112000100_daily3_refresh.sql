-- Nightly refresh (daily3, Nov 12, 2026): per-source cursors and a run log. Additive only; safe to re-run.
-- NOT APPLIED YET: written for review. `python -m src.pipeline.daily3 --dry-run` works without it (JSON files).

-- Where each source left off. cursor_key: 'feed' (RSS), 'search' (CourtListener new dockets),
-- 'docket:<courtlistener id>' (follow-through of one docket). queue: work that did not fit the run's budget.
create table if not exists raw.source_cursors (
  source_id      text not null references raw.source(source_id),
  cursor_key     text not null default 'feed',
  cursor         jsonb not null default '{}',     -- e.g. {"published_after": "..."} / {"last_entry": 812}
  queue          jsonb not null default '[]',
  updated_at     timestamptz not null default now(),
  updated_by_run uuid,
  primary key (source_id, cursor_key)
);

-- One row per run: what changed, which gates passed, what the budgets were, whether it published.
create table if not exists raw.refresh_runs (
  run_id        uuid primary key default gen_random_uuid(),
  job           text not null default 'daily3',
  mode          text not null check (mode in ('dry_run', 'live')),
  status        text not null check (status in ('running', 'dry_run', 'ready', 'no_change', 'published', 'blocked', 'failed')),
  started_at    timestamptz not null default now(),
  finished_at   timestamptz,
  new_leads     integer,
  stage_changed integer,
  dropped       integer,
  lead_count    integer,
  gates         jsonb not null default '{}',     -- {gate: {"pass": bool, "detail": text}}; never holds private terms
  budget        jsonb not null default '{}',     -- CourtListener requests used / cap, LLM USD spent / estimated / cap
  changes       jsonb not null default '[]',     -- follow-through findings (applied, kept override, undated)
  queued        jsonb not null default '[]',     -- overflow carried to the next run
  reviewed      boolean not null default true,   -- false: a new top-20 lead or a stage change went out unreviewed
  published     boolean not null default false,
  state         jsonb not null default '{}',     -- {lead_count, stages: {lead_id: stage}, top20: [...]} for the next diff
  note          text,
  changelog     jsonb                            -- daily checks of the last 30 days with their public change rows
);                                               -- (src.pipeline.changelog); a removed lead is a count, never a name
create index if not exists refresh_runs_started on raw.refresh_runs (job, started_at desc);

-- Every CourtListener HTTP call (retries included) made by daily3 and the case-list downloader. daily3 enforces
-- <= 45 per rolling hour and <= 100 per rolling 24 h over this table plus core.sale_check (sale-check job).
-- No request headers are stored, so never the API token.
create table if not exists raw.courtlistener_calls (
  call_id   bigint generated always as identity primary key,
  called_at timestamptz not null default now(),
  job       text not null,                     -- 'daily3' / 'case_list'
  run_id    text,                              -- raw.refresh_runs.run_id or raw.ingest_run.run_id
  method    text not null default 'GET',
  endpoint  text not null,                     -- URL path, e.g. /api/rest/v4/docket-entries/
  query     text,
  status    smallint                           -- NULL = no response (network error or stopped by a cap)
);
create index if not exists courtlistener_calls_at on raw.courtlistener_calls (called_at desc);

-- Service role only, like every other pipeline table (20261109000400_security.sql).
alter table raw.source_cursors enable row level security;
alter table raw.refresh_runs enable row level security;
alter table raw.courtlistener_calls enable row level security;
revoke all on raw.source_cursors, raw.refresh_runs, raw.courtlistener_calls from anon, authenticated;
grant all on raw.source_cursors, raw.refresh_runs, raw.courtlistener_calls to service_role;
