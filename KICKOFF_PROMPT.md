# KICKOFF — how to start the full build in Claude Code

## A. One-time setup (you, ~15 min)
1. Create the repo and copy this kit into its root: `CLAUDE.md`, `.claude/` (agents, commands, settings.json), `docs/BUILD_SPEC.md`, `docs/EXECUTION_PLAYBOOK.md`, plus your existing `README.md`.
2. Supabase: create a **dev** project (or run `supabase start` locally if you have Docker). Install the Supabase CLI and run `supabase link` to the dev project.
3. Create `.env` (never commit it):
   ```
   SUPABASE_URL=...
   SUPABASE_SERVICE_ROLE_KEY=...
   SUPABASE_DB_URL=postgresql://...
   ANTHROPIC_API_KEY=...
   COURTLISTENER_TOKEN=...   # free account at courtlistener.com
   ```
4. Put the past-deal addresses (confidential) in `eval/private/past_deals.csv` on your machine only. It is gitignored and denied to Claude in settings.json.
5. `git init && git add -A && git commit -m "kit"` then run `claude` in the repo root.

## B. Paste this into Claude Code

```
You are the orchestrator for the Bloomfield Team 1 sourcing pipeline.

Read, in order: CLAUDE.md, README.md, docs/BUILD_SPEC.md, docs/EXECUTION_PLAYBOOK.md, and list .claude/agents/. Then:

1. Restate in 5 bullets: the goal, the two-hub data model, the 5 waves, the gates, and the rules you must never break.
2. Execute Wave 0 yourself exactly as the playbook says (scaffold, src/common, migrations for raw/stg/core/app with RLS, raw.source seed, config/thresholds.yaml, .env.example, .gitignore, README update).
3. Stop at GATE 0: show me the migration SQL and the file tree. Wait for my "approved".
4. After I approve, run `/wave 1`: launch cms-loader, hud-loader, court-loader, news-loader and license-loader in ONE message so they run in parallel. Each runs a --limit 200 sample, tests, then the full load.
5. Continue wave by wave (2 → 3 → 4). Wave 2 runs core-builder and extractor in parallel. After every wave run qa-verifier, commit, and stop at the gate with a checklist.

Working rules for you as orchestrator:
- You own supabase/migrations, src/common, config, CLAUDE.md and README.md. Subagents own only their folders/tables (playbook §2).
- Give each subagent a self-contained brief: goal, owned paths/tables, BUILD_SPEC sections, done criteria, report format. Subagents don't see this conversation.
- Batch docs/schema_requests.md into one migration between waves, never mid-wave.
- If a source is blocked (401/403/robots), mark it manual and keep going. Never bypass blocks.
- If something is ambiguous and costly to redo, ask me; otherwise pick the simplest option that satisfies BUILD_SPEC and note it in the wave report.
- Keep reports compact: tables, row counts, failures. No long prose.
```

## C. Your job at each gate (5–15 min each)
| Gate | You check | Reply |
|---|---|---|
| 0 | Migration SQL matches the two-hub model; nothing confidential in repo | "approved" or fixes |
| 1 | Row counts per source look sane (CHOW ~5k, enrollments ~14k, HUD 232 ~4k); blocked sources list | "go wave 2" |
| 2 | 20 needs_review matches (mark right/wrong); extraction ≥18/20 | corrections, then "go wave 3" |
| 3 | Top 20 leads: would Bloomfield call? | feedback on scoring weights |
| 4 | Run `python -m eval.backtest --file eval/private/past_deals.csv` yourself; 3/3? | done, or gap list |

## D. Handy commands inside Claude Code
- `/wave 2` runs a whole wave · `/wave 1 hud-loader` re-runs one agent
- `/status` shows row counts, last runs, gates passed
- `/agents` lists and edits the subagents
