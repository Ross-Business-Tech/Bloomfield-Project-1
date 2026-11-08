# How the system works

Team 1 decided on Nov 8, 2026 to run the full pipeline on our own services instead of the event's here.now hosting.

```
Public sources              Collector (runs daily)          Database            Website
news, court dockets,  ──▶   fetch new items,          ──▶   Supabase      ──▶   Vercel
CMS ownership data          Gemini extracts the fields      (leads table)       (lead list + small API)
```

| Part | Tool | What it does |
| --- | --- | --- |
| **Collect** | Vercel Cron + a collector function | Once a day, fetches new items from each source (trade press RSS, CourtListener, CMS change-of-ownership data). Plain code, not AI. |
| **Extract** | Gemini API | Reads each item and fills in: property, city/state, units, distress type, stage, receiver/debtor, buyer/new operator, key dates. Writes `NOT FOUND` when a fact is missing. Never guesses. |
| **Store** | Supabase (Postgres) | One row per lead, with its source links. De-duplicates by property name + city. |
| **Score** | Code (`src/lib/scoring.js`) | Stage, buyer identified, recency, price collapse, exit path. Every point is explainable. |
| **Show** | Vercel website | The ranked lead list the originations analyst reviews weekly. |

## Rules that never change

- **Every lead has a source link** proving the distress signal. Without a link, the lead is not shown.
- **Missing facts say `NOT FOUND`**: the score stays blank and the row is flagged for review.
- **No secret keys in the code.** This repository is public. The Gemini key and the Supabase service key live only in Vercel's environment variable settings. The website only ever uses Supabase's public read-only key.
- **No confidential Bloomfield data** (past deal details, emails) anywhere in this repository or in Supabase.

## Accounts (one owner each, teammates invited)

- [ ] **Vercel**: one teammate creates a team, invites the others, and imports this GitHub repository. Our GitHub organization may need to approve the Vercel app; ask the instructor if it's blocked.
- [ ] **Supabase**: one teammate creates a project and invites the others.
- [ ] **Gemini API key**: created in Google AI Studio by one teammate and pasted **only** into Vercel's environment variables, never into chat, code, or GitHub.
- [ ] Instructor told that we host on Vercel instead of the event's here.now dev site and reveal.

## How we work on GitHub

- `development` is the team's shared version. Every change goes there through a pull request.
- Each person works on their own branch: `name/feature` (for example `aiden/court-filings`).
- One feature = one branch = one pull request. A teammate reviews and merges.
- Nobody pushes to `main`.

## Who builds what (suggested split)

| Piece | Owner |
| --- | --- |
| Lead list website + scoring | TBD |
| Collector: trade press news | TBD |
| Collector: CourtListener bankruptcy filings | TBD |
| Collector: CMS ownership changes + Supabase table | TBD |
