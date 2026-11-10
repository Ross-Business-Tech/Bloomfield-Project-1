# QA — Wave 1 (dry-run mode)

Date: 2026-11-09. Database credentials were not yet in `.env`, so every loader ran `--dry-run` (fetch + parse + validate, no DB writes). DB write paths are written and unit-tested with fakes but not yet run against Supabase.

| Check | Result |
|---|---|
| `pytest -q` | PASS: 85 passed |
| Each agent stayed in its owned folders | PASS: only `src/discover/<x>/`, `tests/discover/<x>/`; orchestrator changed src/common, migrations, docs |
| No secrets / confidential strings in new files | PASS |
| `.env` not tracked; `eval/private/` ignored | PASS |
| Robots compliance (RFC 9309 longest match, now in src/common) | PASS: Google News /rss/search disallowed → manual; elevenflo /search disallowed (unused); Stretto ajax endpoint allowed |
| Re-run inserts 0 duplicates | NOT RUN (needs DB) |
| 20 random source_url return HTTP 200 | NOT RUN (no core.signal / app.lead rows until Wave 3) |

## Dry-run row counts

| Source | Sample (--limit 200) | Full size available | Status |
|---|---|---|---|
| cms_chow | 200 | 5,340 | ok |
| cms_chow_owner | 200 | 122,191 | ok |
| cms_snf_owner | 200 | 300,738 | ok |
| cms_snf_enrollment | 200 | 14,404 | ok |
| cms_cost_report | 189 (11 lack FY end) | 14,587 | ok |
| cms_pbj | 3 quarter rows from 200 daily | 1,317,134 daily → quarterly | ok |
| cms_chain_perf | 199 | 612 | ok |
| cms_sff | 200 | 528 (88 SFF + 440 candidates, Oct 2026) | ok |
| hud_active | 200 | 4,073 Sec. 232 loans (full dry run) | ok |
| hud_terminated | 200 | 9,458 (1,146 CLAIM) | ok |
| mi_lara | 200 | 1,147 HFA/AL homes of 4,480 | ok |
| fl_ahca | 0 | — | manual: JS search only, no licensee export |
| shn_rss / snn_rss | 10 / 10 | feed holds 10 | ok, needs daily runs |
| gnews_rss | 0 | — | manual: robots.txt disallows /rss/search |
| warn | 1 | ~20 latest healthcare notices | ok, thin |
| courtlistener | 0 | — | waiting for COURTLISTENER_TOKEN |
| stretto / epiq / elevenflo | 10 / 71 / 108 cases | — | ok (name-based healthcare filter) |
| kroll | — | — | manual (403) |
