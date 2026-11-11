# Status: pipeline build (branch `aiden/wave4-fixes`)

_Last updated: Nov 10, 2026 (Wave 4 freeze). Wave 4 is not pushed yet._

| Wave / gate | State |
|---|---|
| Wave 0: contract (schema, shared code, config) | ✅ Gate 0 approved |
| Wave 1: loaders (CMS, HUD, courts, news, Michigan licenses) | ✅ Gate 1 approved |
| Wave 2: facility / company hubs + AI extraction | ✅ Gate 2 approved |
| Wave 3: signals, buyers, scoring, lead list | ⏳ **At Gate 3**: decisions 1b / 2d and the top-5 hand check applied |
| Wave 3.5: AL sources, licensed loan facts, takeovers | ✅ done (Nov 10) |
| Wave 4: QA + backtest + fixes | ✅ backtest run (results kept private); fixes below; data frozen Nov 10 |

## Requirement #1 (AI extraction accuracy)
**Req #1 not fully met; stopped by decision. Holdout v1 (16 scored): Sonnet buyer 12/12, 0 buyer hallucinations, 9/16 correct ignoring dates/amounts. Accepted for MVP.**
- Extraction now runs on Sonnet (`claude-sonnet-5-5`, prompt v2) for all 1,984 articles. It cost $6.45.
- 4 holdout articles could not be fetched: H07 is blocked by robots.txt, H11 is a dead link, H15 was blocked by Business Wire, and N20's site would not connect.
- Report: `eval/reports/holdout_extraction.md`.

## Current numbers (Nov 10, 2026, after cleanup, parent-buyer deals and the recall audit)
- **Main lead list:** 486 homes, 50.6% with a buyer named. Buyer status: 246 named, 28 stale, 3 candidate, 3 shell, 206 NOT FOUND, 0 undated.
  - Sale stage: 123 homes, 88.6% named.
  - Late stage: 66, 15.2% named.
  - Middle stage: 49, 8.2% named.
  - Early stage: 248, 49.6% named.
- **Care type (main list):** SNF 455 (93.6%), AL / IL / CCRC / MC 25 (5.1%), mixed 2, unknown 4. It was 5.2% before the new main-list rule, so the rule does not change the share.
- **Buyer type source (main list):** 7 confirmed (a source or CMS flag), 273 inferred from the name.
- **Deals:** 221 (bankruptcy families, same-buyer groups, single homes). **Repeat distressed buyers:** top 10 in `output/repeat_buyers_<date>.csv`.
- **Watch list:** 7,132 homes, **CSV only** (`output/watch_<date>.csv`, written by the score run; no longer in the database).
- **Database:** 152 MB of the 500 MB plan (was 395.5 MB).
- **Tests:** 205 passing.

## Wave 4 (Nov 10): fixes and data freeze
**Public repo: no licensed facts and no past-deal details here; backtest results stay in `eval/private/`.**
- **Backtest top line:** property hits 0/3, buyer hits 3/3, median buyer lead time 538 days. The roadmap for the property misses (court-to-home links, a local-press receivership source, one state roster) is in the private backtest file.
- **Lists now (public view):** main list 535 homes (598 internal), List A 253 live deals with a named buyer, List B 218 pre-sale distress, List C 30 repeat-buyer / takeover firms. Assisted-living share: 11.4% public, 20.4% internal.
- **Public sources for hidden leads:** public court filings can be entered by hand (`config/court_facts.yaml` `cases`); a debtor trust ("... DST") now links to the home its master tenant ("... MT, LLC") is licensed under; homes named in public sale notices are linked. 16 of 17 hidden top-50 bankruptcy leads became public. Hand-entered homes whose state is deduced from the city carry the flag `state_inferred`.
- **Buyers:** an OpCo / PropCo buyer is matched to its parent operator by brand (`brand_match`, inferred); a debtor or its affiliate is never its own home's buyer; a buyer with the debtor's brand is flagged `buyer_possible_insider`; stalking-horse bidders are `candidate` until a public order names the final buyer.
- **Roundup articles** ("dealbooks") are split into one item per deal before extraction (976 items, $1.44). A "building" that is only a city name is never matched, and a news operator change or buyer needs a confirmed building link.
- **Public ranking:** public score without licensed points; name-only court matches are kept out of the public top 50 too.
- **Numbers:** database 202 MB; tests 244 passing.
- **Safety:** licensed unit counts stay in the private tables; the web client stops instead of sleeping when a server asks to wait more than 5 minutes.
- **AI provider:** `LLM_PROVIDER` (anthropic | gemini, default anthropic). Production stays on Sonnet until another model matches it on the holdout (`python -m eval.extraction.compare_models`).
- **Not done:** receivership / deed-in-lieu news query (GDELT rate-limited and returned nothing; reverted). Google News stays manual (robots.txt).

## Wave 3.5, part 3 (Nov 10)
**Public repo: no licensed facts here.**
- **History:** the two Wave 3.5 commits were squashed before any push. A full-history scan found no licensed facts and no Bloomfield-confidential facts: the Bloomfield mentions are rules ("past deals never go in the repo"), and no private file was ever tracked.
- **Deliverable rule (decision):** Bloomfield-facing exports (`leads_`, `watch_`, `deals_`, `repeat_buyers_`, `al_takeovers_`, `al_top_buyers_` CSVs and `app.v_lead_list`) never show licensed facts.
  - `licensed_only` leads (their public signals alone would not make the main list) are excluded.
  - `licensed_private` leads are masked: public distress types and stage, buyer NOT FOUND when the buyer is licensed, a cut-down score_breakdown, web-link sources only.
  - Internal files are named `INTERNAL_*` and keep everything for ranking and the backtest.
  - The public score comes from scoring the lead without licensed signals, buyers or sizes (Wave 4); it no longer includes licensed points.
- **Licensed bankruptcy-sale buyers:** per-community buyers load from a private CSV into `private.licensed_buyer` (`python -m src.discover.licensed.buyers`). Settings, dates and hand overrides sit in a private yaml.
  - 24 rows: 18 communities linked (2 matched, 1 hand-checked override, 15 new facilities); 6 without a state are not linked.
  - Each linked community gets a `court_sale_process` signal and a named buyer, tagged `licensed:provider`.
  - Name matching no longer trusts word-subset matches ("The Willows" vs "The Gardens at Old Mill Willows").
  - A sale needing review is never attached; per-community buyers never spread to a whole bankruptcy family.
- **Buyer types (decision):**
  - `config/buyer_orgs.yaml` now matches brand words. Brookdale and Sonida = public company; Morada, Discovery (Arvum) and Trilogy = large operator (new type, 40% like public); Welltower and National Healthcare Properties = REIT.
  - The Texas "Trilogy Senior Living Management" may not be Trilogy Health Services (not checked).
- **Backtest (`eval/backtest.py`):**
  - Reads `eval/private/past_deals.csv`, which has not been provided yet. Columns: name, street, city, state, deal_date (+ optional zip, buyer). Nothing is written to the database.
  - Main list = caught; watch list = partial (reported, not a pass).
  - Replay at 6 and 12 months; pass = main list 6 months before the deal; gap analysis for every miss.
  - Tested on a made-up 3-row sample.
- **Numbers:** internal main list 586; Bloomfield-facing 508. Database 200 MB. Tests: 223 passing.

## Wave 3.5, part 2: licensed loan facts, takeover list, news (Nov 10)
**Public repo: no licensed facts here.** Property names, loan statuses, groups and buyers from licensed files exist
only in `eval/private/` (gitignored), schema `private` and the CSVs in `output/` (gitignored).
- **Decisions applied:**
  - A licence / operator change is a **buyer event**. It is an operator_turnover signal only after real distress (closure, HUD delinquency, special servicing / REO, low occupancy, SFF) in the 24 months before the change; 21 such signals.
  - Texas is noted as "page blocked, file public" in `docs/SOURCE_CATALOG.md`. No weekly schedule.
- **Licensed loan facts (mechanics only):**
  - Loaders: `python -m src.discover.licensed.loans`, `python -m src.discover.licensed.buyers`.
  - Facts are stored in schema `private` (no API access). Signals carry `licensed:<source>` instead of a URL; hard rule 1 now accepts a web link or a `licensed:` tag.
  - Leads resting on them are flagged `licensed_private` and hidden from the public `app.v_lead_list`; CSV exports use `app.v_lead_list_internal`.
  - New hard-event signal types: hud_delinquency, special_servicing, maturity_default (middle) and lender_takeback (late); stages are assumptions.
  - Loan portfolios from the files are one deal row each, with owner / operator notes from a private yaml.
  - Matching uses past licence-roster names as aliases (buildings renamed after a takeover).
  - 62 loans: 21 matched to existing facilities and 41 new facilities; 1 match needs review.
- **Mocksville:** HAL-030-008 is Somerset Court (kept). HAL-030-009 (Mocksville Senior Living, 337 Hospital St) and HAL-030-010 (PS Senior Living, 191 Crestview Dr) are other buildings and now have their own facilities.
- **AL takeover list** (`python -m src.score.takeovers`, CSVs in output/):
  - 360 changes at 289 homes (TX 359, NC 1) in 24 months, 20+ beds, same-family restructurings excluded; 29 had distress before the change.
  - Top buyers from public roster data: Sagora 32 homes, Cima 17, PSL 16, Civitas 11, American Trust 11. 4 of the top 20 also run main-list homes.
- **News:** bondoro_rss (30 articles) and whatnow_rss (27) added; SHB was already read. Extraction cost $0.63 for 47 articles; 4 new facility links.
- **Numbers:** main list 509 → 569 (507 public + 62 resting on licensed facts); AL / IL / CCRC / MC 34 → 96 (16.9%); SNF 466. Database 200 MB. Tests: 218 passing.

## Wave 3.5: AL source expansion (Nov 10)
- **Step 1 (HUD 232 universe):** already done in Wave 2: every active HUD 232 loan is linked; ~1,560 AL / board & care buildings came from HUD alone. Left out: 656 AL loans terminated before 2016. 0 new buildings; none of the 6 missing holdout buildings has a HUD loan.
- **Step 2:** done in part 2 (licensed file; details private).
- **Step 3 (state AL licences):** automated NC (`nc_dhsr`), TX (`tx_hhsc` + closures), NY (`ny_doh`), IA (`ia_dial`, no licensee), PA (`pa_dhs`, no licensee). Manual: FL (one export), OH (form export), IL IDPH (portal/FOIA), KS (403), IL HFS SLF docx (not loaded yet). Homes under 13 beds are skipped (same rule as Michigan).
  - +3,007 facilities (21,239 total). Wayback backfill: NC 10 captures 2022-2025 (Apr 2023 report layout unreadable), TX 23 captures 2021-2026.
  - Licensee / management-company changes are events with both dates (`prior_seen_date` = last snapshot with the old party, `effective_date` = first with the new). Spelling variants, placeholders (N/A) and one-snapshot blips are ignored. TX: 767 licensee changes (448 in 24 months) and 452 management changes (216); NC: 6 (3). Each change in 36 months = one operator_turnover signal (decision Nov 10); the CMS ≥2-change rule is unchanged.
  - TX closures: `closure` hard event if ≤24 months old and the home has a known size of 13+ (assumption; the closures file has no beds).
- **Crawler identity:** User-Agent `UMich-Team1-research/0.1 (contact: RESEARCH_CONTACT_EMAIL)`; the address lives only in `.env`. No client name in any request.
- **After step 3:** main list 486 → 509; AL / IL / CCRC / MC 25 → 34 (6.7%); SNF 470. Database 152 → 199 MB.

## Cleanup and follow-ups (Nov 10)
- **Database cleanup:** 395.5 → 152.4 MB. The watch list moved to CSV only. The staging copies `stg.cms_snf_owner` and `stg.cms_chow_owner` were emptied (210 MB); the raw files stay in `data/raw/cache`, and CMS private-equity / REIT flags were copied to `core.organization.org_kind` first. Re-running `python -m src.enrich --step orgs` needs `python -m src.discover.cms` first to reload them. VACUUM FULL on the core and app tables.
- **Same-buyer deals:** homes whose buyer has the same parent (from a source, or the CMS owner behind a shell) are one deal row with homes nested, like a bankruptcy. A same-buyer group is sized by its best home (separate purchases); a bankruptcy by all its homes.
- **Undated buyers:** a court-sale buyer is dated from the sale order or closing in the docket summary ("approved the sale in January 2026" → Jan 31, 2026). Otherwise the date of the CMS change of ownership to that buyer is used. With neither, the status is 'named (undated)' at half the buyer points (migration `20261110000800`). Today none on the main list.
- **buyer_type_source** (confirmed / inferred) is shown next to buyer_type in the leads, watch, deals and repeat-buyer CSVs.
- **Recall audit:** `eval/reports/recall_audit.md`. All 8 holdout cases (H03-H14) were never fetched: local news, Levin and mortgage press are not sources, and 6 of 8 buildings are not in the facility hub (mostly AL/IL). The keyword prefilter missed 0 of 30. 172 extracted distress articles never linked to a facility, mostly company or portfolio stories with no property named. 0 of 30 watch-list homes should be on the main list.
- **Main-list rule:** 3+ different signals OR one hard event (`scoring.hard_events`). 0 homes left the main list and 9 joined.

## Gate 3 buyer fixes (Nov 10)
1. **Pulaski Memorial Hospital** = hospital/public: 0 buyer points, off the repeat-buyer list (`config/buyer_orgs.yaml`).
2. **Standard Bearer Healthcare REIT** merged into **The Ensign Group** (public company). Source: Ensign 10-K FY2025, "Standard Bearer Healthcare REIT, Inc. (Standard Bearer), our captive REIT" (investor-relations copy; sec.gov returned 403).
3. **Deal size:** a deal is sized by the total units (else beds) of all its homes against the 40-250 band ($3-30M). Deal score uses the deal's size points, not the best home's. Deals above 500 (2x the top of the band) never rank in the top 20: Genesis, Buckingham and Cold Spring now sit just under #20.
4. **Recency:** a sale-stage home whose ownership change is >12 months old loses 60 points and its court-sale priority (Richland, Oct 2024: #2 → #232; Friendship Village, 2023: last; Harborside, Jul 2025: #230). News buyers are now dated by the latest date the article states (Friendship Village: "Chapter 11 filing 2023" → Dec 31, 2023), not the publish date. Court-sale buyers no longer borrow the bankruptcy filing date.
5. **Shell buyers** (OpCo / PropCo LLC, or named after the facility): the CMS owner with the most facilities behind it is shown as the parent; with no such owner, buyer_status = 'shell' and 0 buyer points.
6. **buyer_type:** operator / PE/investor / public company / REIT / hospital/public / lender. Points: operator and PE/investor 100%, public company and REIT 40% (assumption), hospital/public and lender 0. Types come from `config/buyer_orgs.yaml` (sourced) or name keywords; anything else defaults to operator (stored as `buyer_type_basis`).
7. **Named = ownership change ≤24 months old;** older → 'stale' (0 buyer points). Migration `20261110000700_gate3_buyer_status` adds 'shell' and 'stale'.

## Gate 3 follow-ups (Nov 10)
- **Deals:** homes in one bankruptcy are no longer ranked against each other by score. The family is one deal; deal score = its best main-list home before the family cap. Homes inside a deal are ordered by size fit ($3-30M) → distress severity (stage, then number of signal types) → proof strength (person > petition / claims agent > CMS operator > CMS parent owner > name only). The best 3 in that order keep full family points on the per-home list.
- **Rochester:** 174 Virginia Ave stays confirmed (CMS operator = Rochester Manor OpCo LLC). The stalking-horse entities are dropped (sale withdrawn; mapping was guessed). Recorded instead: Nov 5, 2026 Settlement and Transfer Order (Dkt. 291). Buyer = "Focus-affiliated operators (unnamed)" (ElevenFlo's wording), candidate, for all three homes. Source tag: ElevenFlo docket summary, not verified against the order.
- **Buckingham:** the CCRC was already in CMS as **FFIII HOUSTON SNF TENANT LLC** (CCN 676111, 8580 Woodway Dr, Houston; operator Buckingham Senior Living Community Inc, manager Greystone). Buyer = Focus SH Acquisitions LLC (named), parent Focus Healthcare Partners, $133M total gross consideration, 495 units (Levin Associates, Jan 30, 2026). It was not lead #13 (#13 was The Living Center, Marshall MO); it is now deal #4.
- **Repeat buyers:** named buyers with 2+ separate deals in 24 months. One deal = one bankruptcy or one announcement. Parent orgs are merged only with a source (Focus SH Acquisitions → Focus Healthcare Partners). Other similarly named firms are not merged. Ensign and its REIT Standard Bearer stay separate for the same reason. Hospital-district license holders are left out (129 homes). Pulaski Memorial Hospital is still in; it may be the same county-hospital license pattern.
- **Northrise:** left as is (CMS shows a Dec 2025 change of ownership away from Genesis).

## Top-5 hand check (Gate 3, Nov 10)
- #1 Skies Albuquerque: **yes** (Genesis; CMS operator = the debtor).
- #2 Waterville Center (7 Highwood St): **no**, owner BSD ME 26 LLC / Ike Akiko, never Genesis. Genesis's Waterville home is **Oak Grove Center** (CCN 205091): CMS lists its operator as Waterville SNF Operations, LLC, the debtor. Now linked.
- #3 Las Cruces Wellness: **no**. Debtor Peak Medical Las Cruces LLC is the CMS operator of **Casa Del Sol Center** (CCN 325108). Now linked.
- #4 Rochester Residence: **yes, proven**: CMS lists the lead debtor Rochester Manor OpCo LLC as the operator at 174 Virginia Ave (CCN 395751). Joint debtors Armstrong SNF Operators LLC and Spring Hill SNF Operator LLC are linked to CCNs 395471 / 395666; Rochester Villa OpCo has no CMS CCN (personal care home on the same campus). Sale hearing **Nov 19, 2026** (Nov 5 is the Dkt. 291 settlement order). Stalking horses are buyer **candidates**: Arm Operating → Armstrong, RM Operating → Rochester Residence, SH Operating → Spring Hill (mapped from the names; RMV Operating → Rochester Villa, not on the list). Caveat: ElevenFlo now reports the sale motion withdrawn and a Nov 5 operations transfer to Focus-affiliated operators; re-check before outreach. All in `config/court_facts.yaml`.
- #5 Mission Point of Detroit: **yes**. Buyer stays NOT FOUND. **Correction:** the "$10 plus liabilities" Mission Point sale (Skilled Nursing News, Nov 2025) was the receivership facilities, not the Detroit Chapter 11, so it no longer counts as Detroit's sale evidence (`config/court_facts.yaml`); Detroit is now a late-stage lead.
- **Rule change:** 3 of 5 name-only court matches were wrong. A court link now counts only when an address / CCN source confirms it: a person, the CMS operator/licensee being the debtor, or a petition / claims-agent link in `config/court_facts.yaml`. Name-only links keep the "unconfirmed" flag, are capped at 60 points and never rank in the top 50 (`scoring.court_name_only` in `config/thresholds.yaml`).
- **Side effect to decide:** many Genesis homes are now confirmed at full weight, so the family cap (best 3 per bankruptcy family) picks among ties by ID. Skies is now family-capped (#79).

## Open items
- **Gate 3:** verify the Rochester Dkt. 291 order against the docket (PACER login needed). Most buyer types are inferred from the name (273 of 280 on the main list).
- **Recall (AL/IL):** add sources (local news search, Levin, mortgage trade press) and AL/IL facility lists (state licensing beyond Michigan); without them the AL/IL share stays near 5%.
- **CourtListener:**
  - Limit is 125 requests/day; 34 recent bankruptcy families are waiting.
  - The daily job needs repo secrets `SUPABASE_DB_URL` and `COURTLISTENER_TOKEN`, then a merge into `development`.
  - The email to Free Law Project asking for a higher limit is drafted but not sent.
- **Manual sources:** Stretto and Epiq docket lists, GDELT, McKnight's, Florida AHCA, Kroll.
- **Database space:** about 25 MB of headroom before 400 MB; each rescore leaves dead space, so compact `app.lead` / `core.signal` after big reruns. The next lever is dropping the raw CMS owner list (about 110 MB).
- **Website:** switch the site from `public.leads` to `app.v_lead_list`, and show deals (homes nested) instead of single homes.
