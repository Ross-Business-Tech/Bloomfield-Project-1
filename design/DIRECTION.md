# Deal Radar: design direction (ui-v2, map-first)

## The concept, in five lines
1. **The map is the product.** Everything else is a layer you drill into: USA, state, city, property, research.
2. **A tool, not a page.** One fixed console: a rail of numbers and layer toggles, the map, a slim list. No sentences on the main path.
3. **Four clicks to a phone number.** State, city, property, Call. Search jumps straight to any layer.
4. **Every claim shows its source; every gap says NOT FOUND.** Amber is reserved for "no source states this", including map positions and phone numbers. Nothing is estimated.
5. **Paper, ink, one blue.** The radar feel comes from behaviour (zoom, range rings, pings, breadcrumb), not from decoration.

## Layers (each click goes one level deeper; Esc, the breadcrumb or the Back button goes up)
| Layer | URL | What is on screen | Originator's question |
|---|---|---|---|
| L0 USA | `#/map` | States shaded by lead count. Three numbers: leads, states, buyer named. Layer toggles A / B / C. | Where should I look? |
| L1 State | `#/map/PA` | Animated zoom to the state. City bubbles sized by lead count (when leads carry coordinates), otherwise a ranked city list. Slim list of the state's leads: name, city, size, stage, buyer status, phone. | Where should I look? |
| L2 City | `#/map/PA/pittsburgh` | Property pins: size = units or beds, colour = stage, a slow ping on late-stage or buyer-named pins, range rings for scale. Other cities stay as quiet dots. | What is happening here? |
| L3 Property | `#/map/PA/pittsburgh/r4` | A card: property, place, size, care type, one-line why, stage and buyer chips, "fits $3-30M" chip when the size-fit rule applies, and two buttons: **Call** (or amber Phone NOT FOUND) and **Research**. | Who is taking it over? Who do I contact? |
| L4 Research | `.../r4/research` | The lead detail drawer, unchanged: 1 why, 2 what is happening + sources, 3 who is taking it over, 4 who to contact, 5 score breakdown. | Can I trust this? |

Always visible: **search** (property, buyer, city or state; Enter jumps to that layer) and the **breadcrumb** (USA > State > City > Property). The layer toggles ride along in the URL (`?l=AB`), so a link or the Back button restores the exact view.

Secondary views, from the top bar: **Leads** (sortable table, brief rows on request, all filters, CSV), **Buyers** (leads grouped by buyer), and **?** (How it works and The numbers). The watch list is linked from Leads.

## What degrades without coordinates (never invented)
- L1: no city bubbles; the side panel leads with a ranked city list instead. The map shows the state outline and "Map positions NOT FOUND".
- L2: no pins, rings or legend; the city's leads are the list, and choosing one opens the same L3 card.
- Click count is the same either way: state, city, property, Call.
- Coordinates are read from `lat` / `lon` (also `lng`, `latitude` / `longitude`, top level or under `contact`). A state can be partly covered: leads without coordinates stay in the list only.

## Word budget
- Home: a title, three numbers, three toggles, the map, a one-line legend.
- Deeper layers: labels, numbers and chips. The only running text is the one-line "why" on the property card.
- Explanations live in tooltips (hover, focus or tap) and behind "?".

## Type
- Display: system serif stack (Iowan Old Style / Palatino / Georgia) for titles and the three numbers.
- Text and UI: system sans stack. No web fonts, so no font host.
- Scale (px): 56 / 44 numbers, 30 place title, 22 card title, 15-16 body, 12.5-13 uppercase labels. Numbers are tabular.

## Palette (tokens: the `:root` block at the top of `src/web/static/app.css`, the only place colours are defined)
- Primary ink `#0B2545`, secondary `#13315C`, one accent `#1D70B8`.
- Neutrals: paper `#FAF9F6`, surface `#FFFFFF`, hairline `#E4E1DA`, muted text `#4F5B6E`.
- Map: states shade from pale blue to ink by lead count. Pins use four steps of the same blue family for stage (early to in a sale).
- Amber `#8A4B00` on `#FFF3D6` = NOT FOUND only.

## Layout
- Desktop: `rail 248px | map | list 372px` filling the window; the list column appears from L1. Nothing on the main path scrolls except the list.
- Phone (375px): title and numbers, toggles, map on top, then the card, then the list.

## Motion (explains, never decorates)
- 750ms zoom between layers so you see where you went. Marks fade in after the zoom lands.
- A slow ping on pins that are late-stage or have a named buyer: "look here first".
- `prefers-reduced-motion` turns zoom animation, pings and count-ups off.

## Deliberately left out
- A text landing page, KPI tile grids, gradients, emoji, a second accent colour.
- Map tiles, street detail, any dollar value for a property (size is units or beds only).
- Decision buttons, saved state, notes, pipeline board, e-mail drafts, onboarding. Nothing is stored in the browser.
- Web fonts and a separate tokens file: both would need a change to the server's static-file list, which this pass avoids.
