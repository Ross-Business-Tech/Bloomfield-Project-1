# Recall audit (Gate 3, Nov 10, 2026)

Script: `python -m eval.recall_audit` (read-only). Samples: `recall_keyword_skips.csv`, `recall_watch_sample.csv`.

## 1. Holdout cases traced through the funnel
Funnel: fetched → keyword prefilter → AI extraction → facility link → signal → main / watch list.

| Case | Property | Care type | Distress | Where it falls out | Building in our facility hub? |
|---|---|---|---|---|---|
| H03 | AvantGarde Senior Living, Tarzana CA | IL/AL/MC | Chapter 11 | not fetched (whatnow.com not a source) | yes, AVANTGARDE SENIOR LIVING OF TARZANA; no signals, no lead |
| H05 | American House Keene, NH | IL/AL | receivership | not fetched (Keene Sentinel) | no |
| H08 | The Courte, Citrus Heights CA | MC | loan default, receivership sought | not fetched (Citrus Heights Sentinel) | no |
| H09 | Washington Gardens Memory Care, Tigard OR | MC | loan default, receivership sought | not fetched (MPA mortgage news) | no |
| H10 | The Brant at Oswego, IL | IL/AL/MC | delayed opening | not fetched (Shaw Local) | no |
| H12 | Airy Hills at North Bend Crossing, Cincinnati OH | AL/MC | foreclosure, never opened | not fetched (Levin Associates) | no |
| H13 | The Westland House, Westland MI | AL | closure | not fetched (ClickOnDetroit) | only a different home (American House Westland Venoy) |
| H14 | Mirror Lake Village, Federal Way WA | IL/AL/MC | Chapter 11 | not fetched (whatnow.com) | no |

**All 8 fall out at step 1: the article was never fetched.** We read three trade RSS feeds (SHN, SNN, SHB) and court
sources. These stories ran in local news, Levin and mortgage-trade sites. 7 of 8 are AL/IL/MC homes, and 6 of 8 are not in the
facility hub at all: the hub is seeded from CMS (nursing homes) plus the Michigan license list, so most AL/IL
buildings do not exist for the pipeline even when a story is found.

## 2. Keyword prefilter (30 random skipped articles of 1,583)
0 of 30 are distress stories (awards, policy, interviews, new development). 3 are paywalled "In the Pipeline"
development roundups that might mention a delayed opening. **The prefilter is not the recall problem.**

## 3. Unlinked articles
1,790 articles were AI-extracted; 1,540 never linked to a facility, of which **172 carry a distress type**:
136 name no property (company / portfolio stories), 28 give no state, 8 named a property that matched nothing.
Care types in those 172: SNF 71, other senior housing 70, AL 34, MC 30, IL 22, CCRC 7.

## 4. Watch list (30 random of 7,132)
All 30 are SNFs flagged only by cost-report metrics (negative NOI, low occupancy) and/or CMS SFF status;
none has a hard event. **0 of 30 should be on the main list.**

## 5. Main-list rule "3+ signals OR one hard event"
Hard events: Chapter 11/7, receivership, foreclosure, deed in lieu, closure, WARN closure, HUD claim termination,
loan default, court sale, mechanics lien, distressed-price sale (`scoring.hard_events`, assumption).
Effect: 0 homes leave the main list, 9 join (homes in a bankruptcy family whose points were capped).

| | Main list | SNF | AL / IL / CCRC / MC | mixed | unknown |
|---|---|---|---|---|---|
| Before | 477 | 446 (93.5%) | 25 (5.2%) | 2 (0.4%) | 4 (0.8%) |
| After | 486 | 455 (93.6%) | 25 (5.1%) | 2 (0.4%) | 4 (0.8%) |

**The rule does not change the AL/IL/CCRC share.** The gap is upstream: (1) sources: local news, Levin and
mortgage trade press are not read; (2) the facility hub has few AL/IL buildings, so even a fetched AL story has
nothing to link to; (3) cost-report metrics, the main early signal, exist only for SNFs.
