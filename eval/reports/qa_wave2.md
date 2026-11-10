# QA — Wave 2 (Nov 10, 2026)

| Check | Result |
|---|---|
| Test suite (`pytest -q`) | 162 passed |
| Re-run `python -m src.enrich` | 0 duplicate rows (identical core counts on 2nd run) |
| Re-run `python -m src.extract --dry-run` | 0 pending documents (re-runs cost $0) |
| core rows with missing / non-http source_url (facility_role, ownership_event, loan, metric, sale_check) | 0 |
| Duplicate CCN in core.facility | 0 |
| anon grants on raw/stg/core | 0; RLS on every table |
| Extraction eval (claude-haiku-5-5, prompt v2) | 19/20 fully correct (target ≥18); labels pending human confirmation |
| HUD 75–89 match sample (20 random) | ~17/20 judged right after Gate 2 rules (prefix strip, exact ZIP for auto, care type, direction) |
| Database size | 361 MB of 500 MB (warning line 400 MB) |

## Core row counts
facility 18,151 · facility_xref 25,178 · organization 28,500 · facility_role 82,187 · ownership_event 5,506 · loan 8,954 · legal_case 963 · case_facility 1,694 · metric 86,712 · case_family 156 · stg.extraction 3,567 (1,984 extracted, 1,583 pre-filter skips)

## Known limits
- Stretto docket lists (reCAPTCHA) and Epiq dockets (JavaScript-only) are manual; 34 recent families wait for the daily CourtListener check (limit 125/day).
- GDELT and McKnight's news remain manual.
- needs_review links (75–89) stay provisional; Wave 3 labels leads that depend on them "unconfirmed".
- Extraction eval was tuned on the same 20 cases (no held-out set); repeat runs vary by ±1 article.
