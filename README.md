# Deal Radar

**Our goal: Found distressed senior housing before brokers through the signals we find from public data.**

🔗 **Live site: [bloomfield-project-1.onrender.com](https://bloomfield-project-1.onrender.com/)**

Notes: Password-protected. Ask the handoff owner for access; the password is never stored in this repo.

**Powered by:** Ross Business+Tech AI Sprint, the Weiser Center for Real Estate, Bloomfield Capital

**Team members:** Aiden Lee, Qifeng Yuan, Rui Li, Di Wu

**Last updated:** Nov 15, 2026

---

## Contents

1. [The problem](#1-the-problem)
2. [The insight](#2-the-insight)
3. [What the site shows](#3-what-the-site-shows)
4. [Results](#4-results)
5. [How it works](#5-how-it-works)
6. [Daily automatic update](#6-daily-automatic-update)
7. [Keeping it running](#7-keeping-it-running)
8. [Developer setup](#8-developer-setup)
9. [Repo structure](#9-repo-structure)
10. [Testing and evaluation](#10-testing-and-evaluation)
11. [Known limits](#11-known-limits)
12. [What's next](#12-whats-next)
13. [Rules that never change](#13-rules-that-never-change)
14. [Glossary](#14-glossary)

---

## 1. The problem

Bloomfield Capital is a commercial real estate lender (~20 people, ~$500M fund). Its main product is the **bridge loan**: a short-term (~2 year) loan on a property that banks won't lend on yet, typically $3–30M, with a focus on senior housing.

- About **100 deal emails a week** arrive, almost all from brokers. They turn into ~20 conversations, ~5 term sheets and ~**1 close**.
- Once a broker is involved, the deal goes to many lenders and Bloomfield competes on price.
- **Goal:** grow originations from **~$200M to ~$300M a year with no new hires**. The growth has to come from sourcing deals directly, before a broker does.

**Scope (confirmed with Bloomfield, Nov 4, 2026):** sourcing only (not screening or underwriting), bridge loan originations, senior housing nationwide, public data only. Target: 100 leads is good, 1,000 is amazing.

## 2. The insight

In all three of Bloomfield's recent senior housing bridge loans (details are confidential and not in this repo):

1. **The property failed**: receivership, Chapter 11 or a lender takeback.
2. **A new operator bought it cheap**: an operator with a track record stepped in.
3. **Bloomfield lent to the buyer** to fund the turnaround.

**Bloomfield's borrower is the buyer, not the distressed owner.** So Deal Radar finds two things: the distressed property **and** who is taking it over.

---

## 3. What the site shows

**Map first.** The home page is a US map with the headline numbers and List A / B / C toggles. USA → state → city → property → call: four clicks to a phone number.

- Darker states have more leads. Click a state for city bubbles and a ranked list.
- Click a city for pins sized by beds and colored by stage, with 5, 15 and 30 mile rings. Pins sit at ZIP-code centres.
- Search and the breadcrumb are always visible; Esc or Back goes up one level.

**The three lists**

| List | What it is |
|---|---|
| **A · Live deals** | A public source names the buyer property by property, and the change is recent. |
| **B · Pre-sale** | Distress is clear but no public source names a buyer yet. That gap is the opening for a bridge lender. |
| **C · Repeat buyers** | Companies (not leads) that keep buying distressed homes, linked to the leads they touch. |

**The lead card** shows rank, score, stage, buyer status, every signal with its source link, and the building's public phone and website.

**Other views in the top bar:** Leads (ranked list), Buyers (Named · Candidate · Inferred · Unverified · List C), The numbers (funnel and reconciliation), Watch list (buildings with signals that are not on the main list yet), How it works (`?`), and a **change log** of every daily check. Lists A, B and C download as CSV.

**Buyer status labels**

| Label | Meaning |
|---|---|
| Named | A public source names the buyer of this property. |
| Candidate · not final | A stalking-horse bidder; final only when a public sale order names the buyer. |
| Inferred · portfolio sale | Only a portfolio-wide sale report covers this home. Shown separately, never counted as named. |
| Unverified | Needs a person to confirm. |
| NOT FOUND | No public source names a buyer. We never guess. |

---

## 4. Results

Hand-over snapshot (Nov 11, 2026, after the human review of the top 20):

| Measure | Value |
|---|---|
| Buildings tracked | ~21,300 |
| With dated distress in the last 36 months | 7,740 |
| **Public leads on the site** | **485 in 44 states** (goal: 100) |
| List A · buyer named, recent change | 206 |
| List B · pre-sale, no public buyer yet | 228 |
| Other | 51 (stalking-horse candidates, older ownership changes, sales with no public buyer, one shell) |
| List C · repeat buyers | 159 companies, linked to 35 leads |
| Leads with a named buyer | 43% (207) |
| Leads with a public phone or website | 45% (217) |
| AL / IL / MC / CCRC share | 13% of the main list · 35% of the top 20 · 42% of the top 50 |
| Watch list | 7,255 buildings |

Pennsylvania has the most leads: 73 in 52 cities. Since the hand-over, the daily check updates these numbers; the site's change log shows each change against this baseline.

---

## 5. How it works

```
Public sources ──▶ Collect (Python) ──▶ Supabase: raw → stg → core → app ──▶ Snapshot ──▶ Render website
                     │                        │
                     │                  Claude reads text (extract only)
                     └── code decides everything else: matching, stages, scoring, lists
```

### Step 1 · Collect and save

| Source | What it gives us | Loader |
|---|---|---|
| Court dockets (CourtListener, claims-agent sites) | Bankruptcy cases, sale orders, closings | `src/discover/courts` |
| Trade press RSS (Senior Housing News, Skilled Nursing News, Seniors Housing Business, others) | Distress and sale articles | `src/discover/news` |
| Medicare (CMS) | Ownership, changes of ownership, ratings, special-focus facilities | `src/discover/cms` |
| State licensing (MI, NC, TX, NY, IA, PA; FL by hand) | Assisted-living licences and licensee changes | `src/discover/licenses` |
| HUD | Section 232 loans, defaults and terminations | `src/discover/hud` |

Collectors are polite: they identify themselves, obey robots.txt, stay under one request per second per host, cache what they download and skip repeats. A site that blocks us is marked manual, never worked around. Full terms and access notes: [`docs/SOURCE_CATALOG.md`](docs/SOURCE_CATALOG.md).

### Step 2 · The AI reads, code decides

| Step | What happens | AI? |
|---|---|---|
| Input | Article or filing, saved with its link (`raw`) | No |
| Filter | A keyword check drops off-topic stories; roundup articles are split into one item per deal | No |
| Extract | Claude fills a fixed form: property, location, units, distress type, buyer, dates. Missing facts are `NOT FOUND` (`stg`) | **Yes** |
| Match | Name, address, CMS number and licence rules link the record to a building and a company (`core`) | No |

**Two hubs.** Every source attaches to one of two hubs, never to each other: `core.facility` (the building; exact on CMS CCN or licence number, otherwise name + address) and `core.organization` (the buyer, owner or operator). Only the `app` views are exposed.

### Step 3 · Rules pick the leads

1. Last 36 months only.
2. Three or more signal types, or one hard event (bankruptcy, receivership, foreclosure, lender takeback, closure).
3. Not a former owner's bankruptcy: a parent's bankruptcy is not attached to a home it sold before the petition.
4. No public source link, no lead.
5. Rank by stage: sale, late, middle, early. Closed sales rank below live ones; deals far outside the $3–30M size band never reach the top 20.

Every threshold is in [`config/thresholds.yaml`](config/thresholds.yaml), labelled as an assumption until Bloomfield confirms it. The full rule set is in [`docs/PIPELINE_RULES.md`](docs/PIPELINE_RULES.md). Hand corrections live in [`config/manual_overrides.yaml`](config/manual_overrides.yaml), each with a public link and a date.

### The website

The site (`src/web/`) serves a **static snapshot** of the public lead list. It has **no database connection and no key at runtime**, so nobody can query the database through it. Every page needs the password; pages are `noindex` and `/robots.txt` disallows crawling. Only `/healthz` is open.

---

## 6. Daily automatic update

Nobody presses start. Three GitHub Actions workflows run every day on a temporary computer. Schedules run from the default branch; the jobs use the `development` code, which is what the site is deployed from.

| Workflow | Time (UTC) | What it does |
|---|---|---|
| `daily3` | 11:00 | The daily check: follow open sales, discover new items, read, score, check, publish |
| `news-daily` | 11:17 | Saves new trade press articles to Supabase |
| `courtlistener-daily` | 13:43 | Re-checks up to 15 bankruptcy cases for sale orders |
| `leak-guard` | on every pull request | Blocks keys, email addresses and private files |

### What `daily3` does

1. **Follow:** re-read the court dockets of every lead that is in a sale or has an approved sale. Sale orders and closing notices are detected by text rules, not AI.
2. **Discover:** pick up new bankruptcy dockets and new articles since the last run.
3. **Read and score:** Claude reads only tonight's new text; rules re-score.
4. **Five safety checks:** tests pass · privacy scan with the private term list · every lead has a public source link · lead count within ±10% · snapshot size.

Then one of three things happens:

| Result | What it means | What the job does |
|---|---|---|
| **Publish** | Every check passes and the top 20 has no material change | Uploads the snapshot; the site updates itself |
| **Hold** | A lead entered or left the top 20 with a real change, or a top-20 score moved by the threshold | Publishes nothing; opens a GitHub issue `daily3 hold YYYY-MM-DD` with counts only |
| **Fail** | Any other failed check or a crash | Publishes nothing; GitHub emails the owner of the schedule |

### Guardrails

| Limit | Value |
|---|---|
| AI cost cap per run | $1 |
| CourtListener calls | ≤ 45 per rolling hour, ≤ 100 per rolling 24 h (all jobs combined) |
| Lead-count change per run | ±10% |
| Names, addresses or private terms in public logs | 0 (logs show counts only; no data files are uploaded) |

---

## 7. Keeping it running

### Day to day: nothing

Act only on one of these:

- **A hold issue.** Open the site's change log, review the top 20 changes, and publish by hand if they are right (below). Close the issue.
- **A failure email.** Open the failed run in the *Actions* tab, read the error type, fix the cause, then re-run with *Run workflow* (choose `dry-run` first, then `live`).

### Run the daily check by hand

*Actions → daily3 → Run workflow*. `dry-run` reads and checks only and changes nothing; `live` is the real check and may publish.

### Publish by hand (team laptop with `.env`)

```bash
.venv/bin/pytest -q
python -m src.web.publish            # dry run: rebuild, pack (< 900 KB), leak + size + funnel checks, prints the Render calls
python -m src.web.publish --confirm  # keeps output/site_snapshot.prev.b64, uploads, deploys, waits, checks /healthz
```

Then open the site and click home → a state → a city → a lead card. If the command stops, read its message; do not retry blindly.

Render `autoDeploy` is off: merging a pull request does **not** change the site until someone deploys (publish command, or *Manual Deploy* in the Render dashboard).

### Roll back

- **Data:** Render dashboard → service → *Environment* → *Secret Files* → replace `site_snapshot.b64` with the contents of `output/site_snapshot.prev.b64` → *Manual Deploy* → check `/healthz` and the snapshot date on the site. Then copy `prev.b64` over `output/site_snapshot.uploaded.b64`.
- **Code:** revert the pull request on `development` and deploy, or use *Rollback* on the previous deploy in the Render dashboard (code only; the Secret File is unchanged).

### Where things live

| What | Where |
|---|---|
| Website | Render web service `deal-radar-umich`, free plan, branch `development` ([`render.yaml`](render.yaml), [`docs/DEPLOY_RENDER.md`](docs/DEPLOY_RENDER.md)) |
| Site password | Render env var `SITE_PASSWORD` |
| Site data | Render Secret File `site_snapshot.b64` (never committed) |
| Database | Supabase (Postgres); schema in [`supabase/migrations/`](supabase/migrations/) |
| Pipeline secrets | GitHub → *Settings → Secrets and variables → Actions*: `SUPABASE_DB_URL`, `COURTLISTENER_TOKEN`, `ANTHROPIC_API_KEY`, `RENDER_API_KEY`, plus packed private inputs (exclusion list, private term list, case labels, change-log baseline, watch list). Set them from the laptop with `python -m src.pipeline.cloud_inputs --pack --set`. |

**Free-plan note:** the Render site sleeps after 15 minutes without visitors, so the first visit can take about a minute.

**Database space:** each rescore leaves dead space; compact `app.lead` and `core.signal` after big reruns.

---

## 8. Developer setup

### Pipeline (Python 3.11)

```bash
python3.11 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env          # fill in; never commit it
.venv/bin/pytest -q
```

Use Supabase's *Session pooler* connection string for `SUPABASE_DB_URL` (GitHub runners have no IPv6).

**Full pipeline, in order:**

| Step | Command |
|---|---|
| Collect | `python -m src.discover.<source>` (`cms`, `hud`, `courts`, `news`, `licenses`); add `--limit 200` for a sample run |
| Hand-entered court filings | `python -m src.discover.courts --source court_manual` (from `config/court_facts.yaml`) |
| Split roundup articles | `python -m src.extract.split` |
| Read articles with AI | `python -m src.extract` (Claude Sonnet; a new model must pass `python -m eval.extraction.compare_models` first) |
| Match to buildings and companies | `python -m src.enrich` |
| Score and build lists | `python -m src.score`, then `python -m src.score.takeovers` |
| Exports | `python -m src.score.final_exports` → List A / B / C and top-20 CSVs in `output/` |
| Daily check | `python -m src.pipeline.daily3 --dry-run` (no writes) or `--live --publish` |
| Website | `python -m src.web.publish` (dry run), then `--confirm` |

**Local site preview:**

```bash
python -m src.web.snapshot
SITE_PASSWORD=<anything> SNAPSHOT_PATH=output/site_snapshot.json PORT=8765 .venv/bin/python -m src.web.server
# open http://localhost:8765 (any user name, that password)
```

### UI work with made-up data

```bash
npm ci && npx playwright install chromium
npm run build:radar && npm run smoke:radar    # desktop + phone screenshots in smoke-results/
npm run preview:radar                         # 24-hour preview link, made-up data only
```

`npm run build` alone builds the older Vite starter page (`src/App.jsx`), not Deal Radar.

### Contributing

Work on a branch named `name/feature` and open a pull request into **`development`**. Never push to `main`. See [`CONTRIBUTING.md`](CONTRIBUTING.md) and [`CLAUDE.md`](CLAUDE.md).

---

## 9. Repo structure

```
.
├── .github/workflows/     # daily3, news-daily, courtlistener-daily, leak-guard
├── config/                # thresholds, buyer types, hand-entered court facts, manual overrides, contacts
├── docs/                  # build spec, pipeline rules, source catalog, Render deploy, roadmap, contacts
├── eval/                  # extraction test sets, backtest, recall audit, reports (eval/private/ is gitignored)
├── src/
│   ├── common/            # polite HTTP client, raw writer, database, normalizers
│   ├── discover/          # collectors: cms, courts, hud, licenses, news
│   ├── extract/           # Claude prompt, schema, roundup splitter
│   ├── enrich/            # building / company matching, geography, contacts
│   ├── score/             # signals, rules, buyers, lists A/B/C, exports
│   ├── pipeline/          # daily3, pre-flight, change log, log check
│   ├── web/               # Deal Radar: snapshot builder, publish command, server, map UI
│   └── App.jsx, lib/      # older Vite starter page (not Deal Radar)
├── supabase/migrations/   # database schema
├── tests/                 # pytest
├── tools/                 # leak guard, preview and screenshot tools
├── render.yaml            # Render blueprint
├── HANDOFF.md             # hand-over checklist
└── STATUS.md              # build log, wave by wave
```

`output/` and `data/raw/` are gitignored.

---

## 10. Testing and evaluation

| Test | Result |
|---|---|
| Backtest on Bloomfield's past deals | Found the **buyer in 3 of 3**, a median of ~18 months (538 days) early. Details stay private; aggregate only. |
| Buyer extraction on a hand-labelled holdout set | **12 / 12** correct, 0 invented buyers (Claude Sonnet, prompt v2) |
| Automated tests | **295 passing** at hand-over (`pytest -q`) |
| Leak guard | Runs on every pull request; full private-term scan on every publish |

**What the human review of the top 20 caught (Nov 11):**

- **Stale stages:** sales were tracked when they started, not when approved or closed.
- **Hidden buyers:** some buyers were named only in court sale orders.
- **Wrong owner:** a parent company's bankruptcy was attached to homes it had already sold.

**The fix:** "Sale approved (date)" and "Closed / sold (date)" stages, buyers corrected from court filings, and the former-operator rule, which removed 48 misattributed leads (533 → 485). The distress signals were right; the follow-through lagged. The daily check now does that follow-through automatically.

Reports: [`eval/reports/`](eval/reports/).

---

## 11. Known limits

- **Map positions are ZIP-code centres.** CMS blocks automated downloads of exact coordinates.
- **Contacts cover 45% of leads.** The rest read `NOT FOUND`.
- **"Sale approved" does not mean closed.** Closing is unknown for many approved sales.
- **Candidate buyers are not final** until a public sale order names them.
- **The backtest found the buyers, not the specific properties.** Linking court cases to individual homes is the main recall gap.
- **Assisted living is under-covered** compared with nursing homes, because CMS covers nursing homes only and state licence data is uneven.
- **Scoring assumptions** (care-type fit bonus, the 100+ homes "mega-platform" cut-off, the top-20 hold threshold) still need Bloomfield's confirmation.

## 12. What's next

From [`docs/ROADMAP.md`](docs/ROADMAP.md), roughly in order of value:

1. Exact building coordinates from a manually downloaded CMS file
2. A source link and date for each individual signal on the lead card
3. Buyer track-record profiles (homes acquired, current ratings, exits)
4. "This buyer is on List C" on each lead card
5. More state assisted-living licences, starting with Kansas
6. A receivership news feed
7. Reading claims-agent sale orders to upgrade candidate buyers to named
8. Closing notices for approved sales
9. A fully automatic roundup-article buyer check (today it only counts suspects)

---

## 13. Rules that never change

- **No source link, no lead.** Every buyer and signal rests on a public link.
- **Never guess.** Unknown facts read `NOT FOUND`.
- **The AI only reads.** It fills a form from text; dates, stages, matching and scoring are code.
- **Public sources only.** No PACER, no paid sources without sign-off, never work around a site's bot check.
- **Nothing private in this repo.** It is public, including commit messages, pull requests and Actions logs. Never commit `.env`, keys, the snapshot, anything in `output/` or `eval/private/`, licensed data, or details of Bloomfield's past deals.

---

## 14. Glossary

| Term | Meaning |
|---|---|
| Bridge loan | Short-term loan that carries a property from unstable to stable, then is replaced by a cheaper bank or HUD loan |
| Receivership | A court-appointed manager runs the property |
| Chapter 11 | Bankruptcy reorganization; homes are often sold through a court-supervised sale |
| Stalking horse | The first bidder in a court sale; not final until the sale order |
| Deed-in-lieu / lender takeback | The owner hands the property back to the lender |
| CHOW | Change of ownership (CMS) |
| CCN | CMS certification number, a unique ID for a nursing home |
| IL / AL / MC / SNF | Independent living / assisted living / memory care / skilled nursing |
| CCRC | A campus with all levels of senior care |

