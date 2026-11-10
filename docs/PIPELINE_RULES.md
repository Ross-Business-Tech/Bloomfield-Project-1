# CLAUDE.md — Bloomfield Team 1 sourcing pipeline

Claude Code reads this file in every session and every subagent. Keep it short; details live in docs/.

## What we're building
A pipeline on Supabase that finds (a) distressed senior housing facilities and (b) the buyer or new operator taking them over, then outputs a ranked lead list (100+ leads; 1,000 stretch) where every row has a source link. Bloomfield lends to the buyer.

## Read before working
1. docs/BUILD_SPEC.md: schema, sources, pipeline steps, acceptance tests (source of truth)
2. docs/EXECUTION_PLAYBOOK.md: waves, agents, file ownership, gates
3. README.md: project background

## Data model (two hubs)
- Hub 1: `core.facility` (the building). Every source attaches through `core.facility_xref` (exact on CMS CCN or license number, fuzzy on name + address).
- Hub 2: `core.organization` (the buyer, owner or operator). Linked to facilities through `core.facility_role` and `core.ownership_event`.
- Satellites never join each other directly; always go through a hub.
- Layers: raw → stg → core → app. Only `app` views are exposed through the Supabase API.

## Non-negotiable rules
1. No `source_url` → no row in core.signal or app.lead.
2. Unknown → `NOT FOUND` (text) or NULL + flag. Never guess, impute or fill from model knowledge.
3. The LLM only extracts fields from text. Dates, stages, matching and scoring are code/SQL.
4. Polite crawling: User-Agent `BloomfieldTeam1-UMich-research/0.1`, obey robots.txt, ≤1 req/sec per host, cache raw responses. On 401/403/bot-block: stop, mark the source `manual`, report it. Never spoof user agents or bypass blocks.
5. Confidential: Bloomfield deal details and emails never enter the repo, seeds, migrations, logs or fixtures. Backtest reads a gitignored local file only.
6. Secrets only in `.env` (gitignored). Never print keys.
7. Only the orchestrator edits `supabase/migrations/`. Other agents request schema changes in `docs/schema_requests.md`.
8. Write only inside your owned folders/tables (see playbook ownership table).
9. Every loader is idempotent (content_hash dedupe) and supports `--limit N` for a quick sample run.
10. Thresholds live in `config/thresholds.yaml` and are labeled as assumptions.

## Commands
- Tests: `pytest -q`
- Sample run of a loader: `python -m src.discover.<name> --limit 200`
- Full run: `python -m src.discover.<name>`
- DB: Supabase CLI (`supabase db push` to the dev project only)

## Definition of done for any task
Tests pass, sample run works, row counts reported, no file outside your ownership changed, commit with a clear message.
