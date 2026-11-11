# Deal Radar: design direction (ui-v2)

## The concept, in five lines
1. **A Monday briefing, not a dashboard.** The first screen is one sentence: how many leads, as of when, and the three to start with.
2. **The originator's workflow is the navigation.** Where should I look → what is happening at this property → who is taking it over → can I trust this → who do I contact.
3. **Think in buyers as well as buildings.** The lender's borrower is usually the buyer or new operator, so buyers get their own view.
4. **Every claim shows its source; every gap says NOT FOUND.** Amber is reserved for "no source states this". Nothing is estimated.
5. **Quiet by default.** Paper, ink, hairlines, one blue. A table exists, but you ask for it.

## Workflow map (each screen answers one question)
| Question | Screen | What is visible without scrolling or clicking |
|---|---|---|
| Where should I look? | **This week** (landing) | Lead count, as-of date, and three leads to start with (highest-ranked live deals, one per buyer), each with stage, buyer status and phone. Below: counts that link to List A, List B, Buyers and Map, and how the list was narrowed. |
| Where should I look? (by geography) | **Map** | Leads by state; choosing a state lists its top leads and carries the state filter into Leads. |
| What is happening at this property? | **Leads** | Brief rows: rank, property, city/state, size, care type, stage, buyer status, score and a one-line "why". Filters: list A/B/C, state, stage, care type (senior housing vs SNF), buyer status, search. Sortable table on request. |
| All five, for one property | **Lead detail** (drawer over the list) | Reads top to bottom in workflow order: 1 Why this lead → 2 What is happening (signals with plain-English definitions, every source link) → 3 Who is taking it over (buyer, status, track record, other leads tied to the buyer) → 4 Who to contact (tap-to-call, website, address to maps, or NOT FOUND) → 5 Score breakdown. `↑`/`↓` step through the list, `Esc` closes, the list keeps its place. "Copy summary" puts a plain-text summary on the clipboard. |
| Who is taking it over? | **Buyers** | Leads grouped by buyer: status (Named / Candidate · not final / Unverified), number of leads, states, care types, repeat-buyer record where there is one, and a link to each lead. Most leads first. |
| Can I trust this? | **The numbers** | Key ratios in one ruled line, and the screening funnel with the rule applied at each step. |
| Can I trust this? | **How it works** | Four steps, what gets filtered out (buildings → distress signal → leads → short list), the NOT FOUND policy, scoring rules, sources, and a plain-English glossary. |
| Who do I contact? | **Lead detail, step 4**; phone column in the table | Business lines only, each with its source and check date. |

The **Watch list** (buildings below the bar) stays, linked from Leads, but is out of the main navigation: it is not part of the Monday routine.

## Type
- Display: system serif stack (Iowan Old Style / Palatino / Georgia). The landing headline has an italic second line.
- Text and UI: system sans stack. No web fonts, so no font host and nothing to download.
- Scale (px): up to 64 hero · 34 page title · 22 section · 19 lead name · 16 body · 15 UI · 13 smallest (uppercase labels only). Numbers are tabular.

## Palette (tokens: the `:root` block at the top of `src/web/static/app.css`, the only place colours are defined)
- Primary ink `#0B2545`, secondary `#13315C`, **one accent** `#1D70B8` (links, focus, selection, the final funnel step).
- Neutrals: paper `#FAF9F6`, surface `#FFFFFF`, hairline `#E4E1DA`, muted text `#4F5B6E` (about 6.6:1 on paper).
- Status: amber `#8A4B00` on `#FFF3D6` = NOT FOUND only. Named buyer = solid ink chip; Candidate = dashed outline chip that says "not final"; Unverified = dotted grey.
- All text pairs meet WCAG AA.

## Layout
- One column, max 1120px, 24px gutters (16px on phones). Sections are separated by space and a hairline, not by cards.
- Lead rows are a four-track grid on desktop and stack on phones. The drawer is a 600px right sheet (full screen at 375px).
- Top bar: wordmark, six text links in workflow order, as-of date. On phones the links scroll sideways.

## Motion (explains, never decorates)
- Numbers count up once when their page opens; the narrowing bars draw in sequence to show the filtering.
- The drawer slides in over 160ms. `prefers-reduced-motion` turns all of it off.

## Deliberately left out
- KPI tile grids, gradients, decorative shadows, emoji, a second accent colour, list colour-coding (A/B/C are words now).
- Decision buttons, saved state, notes, pipeline board, e-mail drafts, onboarding tours. Nothing is stored in the browser.
- Web fonts and a separate tokens *file*: the server serves a fixed allow-list of static files, and adding one means a server change, which this pass avoids. Tokens are one `:root` block; fonts are a system stack. This also removes the Google Fonts request the page used to make.
- Per-signal source links, buyer contact details and map pins: the snapshot has none of these yet (see `docs/ui_field_requests.md`).
