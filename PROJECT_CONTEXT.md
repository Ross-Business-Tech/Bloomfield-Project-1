# Project context: distressed senior-housing deal sourcing (Bloomfield Capital × UMich Ross, Team 1)

_Last updated: Nov 10, 2026. Created this day; sections 1–9 summarize what the repo already records, §10 is the decision log._

## 1. Goal
Find senior-housing and nursing-home properties in financial distress early enough for Bloomfield Capital (a lender) to call, ideally with the buyer or new operator already named.

## 2. Output
`app.v_lead_list`: one row per lead with facility, city/state, size, distress type, stage (early / middle / late / sale), buyer and buyer status (named / candidate / NOT FOUND), score with a visible breakdown, flags, and source links. A CSV export goes to `output/`, which is local only and never committed.

## 3. Data sources
- CMS: ownership changes and owners, enrollments, cost reports, staffing (PBJ), chain performance, Special Focus Facility list.
- HUD Section 232 loans: active and terminated.
- Michigan licenses.
- Bankruptcy courts: CourtListener API, plus claims-agent sites ElevenFlo, Stretto and Epiq.
- News: Senior Housing News, Skilled Nursing News, Seniors Housing Business, WARN notices.
- Manual sources: Florida AHCA, Kroll, McKnight's, GDELT, Google News, Stretto/Epiq docket lists. These are blocked or JavaScript-only, and we never bypass blocks.

## 4. Architecture
Supabase Postgres with four schemas:
- `raw`: untouched inputs.
- `stg`: typed tables, one per source.
- `core`: two hubs, `facility` and `organization`, plus roles, events, loans, cases, metrics and signals.
- `app`: leads and the view.

RLS is on everywhere; anon can read only `app.v_lead_list`. The Python pipeline lives in `src/`. All rule numbers live in `config/thresholds.yaml`. Specs are in `docs/BUILD_SPEC.md` and `docs/EXECUTION_PLAYBOOK.md`.

## 5. Build waves
0 contract → 1 loaders → 2 core hubs + AI extraction → 3 signals, buyers, scoring → 4 QA + backtest. Each wave ends at a gate the team approves.

## 6. Matching
- CMS facilities are seeded by CCN.
- Other sources fuzzy-match on name and address. A score of 90 or more is auto; 75–89 is needs_review, which makes the lead "unconfirmed".
- HUD extra rules:
  - borrower-code prefixes are stripped before matching;
  - auto-matching requires an exact ZIP;
  - care types must agree;
  - names with different directions (North/South/East/West) never match.

## 7. AI extraction
- The model reads news and court text into fixed fields, copying values only. Anything not in the text is "NOT FOUND".
- Model: Sonnet (`claude-sonnet-5-5`), prompt v2.
- Article text is never committed. The eval sets hold only labels, URLs and short quotes.

## 8. Constraints
- Supabase free plan: 500 MB, with a warning line at 400 MB.
- CourtListener: 125 requests per day.
- Polite crawling: robots.txt, at most 1 request per second, honest User-Agent.
- The repo is public: no keys, no Bloomfield deal details, no private personal data.

## 9. Open items
See `STATUS.md`.

## 10. Decision log
| Date | Gate | Decision |
|---|---|---|
| Nov 9, 2026 | 1 | CourtListener: case list only (API token, robots applies to the website, not the API). Slim raw storage for CMS (one fingerprint per downloaded page). |
| Nov 10, 2026 | 2 | HUD matching accepted at ~85% (prefix strip, exact ZIP for auto, care type, direction rule; generic borrower groups removed). 147 duplicate buildings merged; 2 Medicare numbers = operator turnover. Metric signals keep latest value only; event signals keep full history. Drop the raw CMS owner list only above 400 MB. |
| Nov 10, 2026 | 3 | Broker / market sales = `market_sale`: low weight, not distress. Court sale processes rank first. Report "% with buyer named" with every lead count. |
| Nov 10, 2026 | 3 | **Req #1 not fully met; stopped by decision. Holdout v1 (16 scored): Sonnet buyer 12/12, 0 buyer hallucinations, 9/16 correct ignoring dates/amounts. Accepted for MVP.** Extraction switched to Sonnet for all articles; extractor tuning, holdout v2 and label fixes dropped. |
| Nov 10, 2026 | 3 | Stage rules: closure = late; lender takeback (REO / sheriff's sale) = late, not sale. |
| Nov 10, 2026 | 3 | 1b: public hospital-district buyers keep the lead but earn no buyer points (flag `buyer_hospital_district`). 2d: early-stage-only leads need ≥3 different signals for the main list (`app.v_lead_list`); the rest go to the watch list (`app.v_watch_list`, not public). 3a: the top-5 court→facility matches are confirmed by hand (`eval/reports/top5_match_check.csv`, applied with `python -m src.enrich.review`). |
