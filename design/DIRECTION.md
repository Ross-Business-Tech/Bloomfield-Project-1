# Deal Radar: design direction (ui-v2)

## The concept, in five lines
1. **A Monday briefing, not a dashboard.** The first screen is one sentence: how many leads, as of when, and the three to start with.
2. **A workflow tool.** Every lead can be decided (Call / Watch / Pass) from the keyboard; the tool remembers and builds your call list.
3. **Think in buyers as well as buildings.** The lender's borrower is usually the buyer taking over, so buyers get their own view.
4. **Every claim shows its source; every gap says NOT FOUND.** Amber is reserved for "no source states this". Nothing is estimated.
5. **Quiet by default.** Paper, ink, hairlines, one blue. Tables exist, but you ask for them.

## Workflow map (each screen serves one step)
| Step | Screen | What it does |
|---|---|---|
| Triage | **Today** (landing) | Headline count + as-of date. Then, in order: New since your last visit, Follow-ups due, My call list, Where to start (top undecided leads, one per buyer). First visit shows a 3-step guide instead. |
| Triage | **Leads** | Brief rows (rank, property, place, size, care, stage, buyer status, score, one-line why) with filters; sortable table on request. `j`/`k` to move, `c`/`w`/`p` to decide. |
| Research | **Lead drawer** | Why this lead, signals with plain-English definitions, every source link, buyer + other properties tied to the same buyer, score breakdown. Opens over the list; `↑`/`↓` step through without losing your place. |
| Research | **Buyers** | Leads grouped by buyer/operator: status (Named / Candidate, candidates labelled "not final"), linked properties, states, care types. Sorted by count. |
| Research | **Map** | Leads by state; click a state to see its leads. |
| Decide | Drawer + every row | Call / Watch / Pass / Reset. |
| Outreach | Drawer | Contact (tap-to-call, website, map), **Copy summary** (for an internal email), **Copy intro** (outreach draft from public fields, `[Your name]` placeholder). |
| Follow-up | Drawer + **Today** + **My pipeline** | Note, follow-up date, "contacted" tick. Due items surface on Today. Pipeline board: To call → Contacted / Watch / Pass. Export / import as JSON. |
| Trust | **How it works**, **Overview** | Pipeline story, funnel (buildings → distress signal → leads → short list), sources, scoring rules, NOT FOUND policy, glossary. |

Workflow state lives in `localStorage` only (`dealradar:v1`), holds lead IDs + decisions/notes/dates, and is never sent anywhere. The UI says so: "Saved on this browser only." If storage is unavailable everything still works for the session.

## Type
- Display: system serif stack (Iowan Old Style / Palatino / Georgia). Headline has an italic second line.
- Text and UI: system sans stack. No web fonts, so no font host and nothing to download.
- Scale (px): 56 hero · 34 page title · 22 section · 18 lead name · 16 body · 14 UI/meta · 13 smallest (labels only). Numbers are tabular.

## Palette (tokens: the `:root` block at the top of `src/web/static/app.css`, the only place colours are defined)
- Primary ink `#0B2545`, secondary `#13315C`, **one accent** `#1D70B8` (links, focus, the Call action, map/funnel shade).
- Neutrals: paper `#FAF9F6`, surface `#FFFFFF`, hairline `#E4E1DA`, muted text `#4F5B6E` (7:1 on paper).
- Status: amber `#8A4B00` on `#FFF3D6` = NOT FOUND only. Named buyer = solid ink chip; Candidate = dashed outline chip ("not final").
- All text pairs meet WCAG AA.

## Layout
- One column, max 1120px, 24px gutters (16px on phones). Sections separated by space and a hairline, not by cards.
- Lead rows are a 5-track grid on desktop and stack on phones. The drawer is a 560px right sheet (full screen at 375px).
- Top bar: wordmark, six text links, as-of date. On phones the links scroll sideways.

## Motion (explains, never decorates)
- Numbers count up once when they enter (headline, funnel). The funnel bars narrow in sequence to show the filtering.
- Drawer slides 160ms; row decisions change state in place. `prefers-reduced-motion` turns all of it off.

## Deliberately left out
- KPI tile grids, gradients, shadows-as-decoration, emoji, a second accent colour, list colour-coding (A/B/C are words now).
- Web fonts: the server only serves an allow-list of text files and server edits are out of scope, so a system stack it is. This also removes the Google Fonts request from the page.
- A separate tokens *file*: same reason (allow-list). Tokens are one `:root` block instead.
- Any LLM call, any backend, any per-user account.
- Per-signal source links and lat/long pins: the snapshot has neither (see `docs/ui_field_requests.md`).
