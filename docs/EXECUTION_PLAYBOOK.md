# EXECUTION_PLAYBOOK — how Claude Code executes the build

## 1. Principle: contract first, then fan out
Parallel agents only go fast when they can't collide. Wave 0 freezes the contract (schema, shared library, source registry). After that, each agent owns separate folders and tables, so 5 agents can build at once.

```
Wave 0  orchestrator (alone)        schema + shared lib + config        → GATE 0 (human approves SQL)
Wave 1  5 loaders in parallel       raw + stg for every source          → GATE 1 (row counts)
Wave 2  2 agents in parallel        core hubs + AI extraction           → GATE 2 (match review, 18/20 extraction)
Wave 3  signals-scorer              signals, buyer link, score, view    → GATE 3 (top 20 leads)
Wave 4  qa-verifier + human         tests, link checks, backtest        → GATE 4 (3/3 backtest, local only)
```

## 2. Agents and ownership (no two agents write the same file or table)

| Agent | Wave | Model | Writes code in | Writes tables | Category |
|---|---|---|---|---|---|
| orchestrator (main session) | 0, all | default | `supabase/migrations/`, `src/common/`, `config/`, `CLAUDE.md`, `README.md` | `raw.source` | All |
| cms-loader | 1 | sonnet | `src/discover/cms/` | `raw.record` (cms_*), `stg.cms_*` | Ownership + operating (SNF) |
| hud-loader | 1 | sonnet | `src/discover/hud/` | `raw.record` (hud_*), `stg.hud_*` | Debt clock |
| court-loader | 1 | sonnet | `src/discover/courts/` | `raw.document` (court), `stg.court_case` | Legal |
| news-loader | 1 | sonnet | `src/discover/news/` | `raw.document` (news, warn) | News |
| license-loader | 1 | sonnet | `src/discover/licenses/` | `stg.state_license` | Ownership (assisted living) |
| core-builder | 2 | opus | `src/enrich/` | all `core.*` except `core.signal` | Hubs |
| extractor | 2 | opus | `src/extract/`, `eval/extraction/` | `stg.extraction` | News + legal text |
| signals-scorer | 3 | sonnet | `src/score/` | `core.signal`, `app.*` | Output |
| qa-verifier | after each wave | sonnet | `tests/qa/`, `eval/reports/` (read-only on DB) | none | Checks |

Each agent also owns `tests/<its folder>/`. Schema change needed → append to `docs/schema_requests.md`, keep working with a TODO; orchestrator batches migrations between waves.

## 3. Wave details

### Wave 0 — orchestrator alone (est. 1–2 h, assumption)
1. Scaffold repo per README §12 + `src/common/` (http client with robots check, rate limit, cache; `raw_writer`; `ingest_run` logger; Supabase client; name/address normalizers).
2. Write migrations for raw/stg/core/app + RLS from BUILD_SPEC. Seed `raw.source` (all sources, incl. manual ones).
3. `config/thresholds.yaml`, `.env.example`, `.gitignore` (.env, data/raw, eval/private).
4. Update README.md: stack = Supabase + Claude Code subagents; data model = two hubs.
5. **GATE 0:** print migration SQL + file tree, stop, wait for the human.

### Wave 1 — 5 loaders in parallel (est. 2–4 h wall clock)
Orchestrator launches cms-loader, hud-loader, court-loader, news-loader, license-loader **in one message** so they run concurrently. Each: build loader → `--limit 200` sample → tests → full run → report row counts and blocked sources.
**GATE 1:** table of row counts per source + list of sources marked manual.

### Wave 2 — 2 agents in parallel (est. 2–3 h)
- core-builder: facilities from CMS enrollments (CCN exact), then HUD/licenses/courts/news by fuzzy match (rules in BUILD_SPEC step 3), organizations, roles, events, loans, legal cases, metrics. Matches scored 75–89 → `needs_review`.
- extractor: extraction prompt + JSON schema; eval on 20 hand-labeled articles in `eval/extraction/`; then run on all `raw.document`.
**GATE 2:** human reviews 20 `needs_review` matches + extraction score (need ≥18/20).

### Wave 3 — signals-scorer (est. 1–2 h)
Signal rules → buyer linking → scoring with `score_breakdown` → `app.v_lead_list` → CSV in `output/`.
**GATE 3:** human reviews the top 20 leads ("would Bloomfield call?").

### Wave 4 — QA + backtest (est. 1 h)
qa-verifier runs all acceptance checks in BUILD_SPEC. Backtest: the human runs `python -m eval.backtest --file eval/private/past_deals.csv` locally (file is gitignored; Claude never sees deal details in the repo).
**GATE 4:** 3/3 recall or a written gap analysis.

## 4. Speed levers
1. **Contract first.** No parallel work before Gate 0.
2. **Disjoint ownership.** Folders + tables per agent → zero merge conflicts.
3. **Sample before full.** Every loader runs `--limit 200` first; full pulls only after tests pass.
4. **Cache raw.** Never re-download during debugging; re-run stg from raw.
5. **Right model per job.** Loaders = sonnet (mechanical); matching + extraction = opus (judgment).
6. **Pre-approve safe commands** in `.claude/settings.json` so agents don't stall on prompts.
7. **Dev database.** Agents write to a dev Supabase project (or local `supabase start` with Docker); promote to the team project after Gate 3.
8. **Commit per wave** so any wave can be rolled back.

## 5. Multi-person mode (optional: 4 teammates, 4 terminals)
Same waves, but each teammate runs one Claude Code session in its own git worktree after Gate 0:
```
git worktree add ../bf-cms -b wave1/cms && cd ../bf-cms && claude
> /wave 1 cms-loader
```
Suggested split (adjust freely): Aiden = orchestrator + signals-scorer · teammate 2 = cms-loader + hud-loader · teammate 3 = court-loader + news-loader + extractor · teammate 4 = license-loader + core-builder + qa-verifier. Merge each branch to main via PR at each gate; the orchestrator resolves schema requests.
