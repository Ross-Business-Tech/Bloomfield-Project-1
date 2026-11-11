# UI field requests (ui-v2)

Fields the new UI would use if the snapshot carried them. Nothing here blocks the UI: each one degrades as described.

| Field (per lead) | Why | Today the UI does |
|---|---|---|
| `signal_details[]`: `{signal, date, source_url}` | Must-have: each signal with its own source link and date. | Shows signals, then all source links for the lead together; only the latest signal date is shown. |
| `lat`, `lon` (per lead, decimal degrees) | City bubbles (L1) and property pins (L2) on the map. The UI already reads these names (also `lng`, `latitude`/`longitude`, top level or under `contact`). | L1 shows a ranked city list instead of bubbles; L2 shows the city's leads as a list; the map says "Map positions NOT FOUND". |
| `buyer_track_record`: prior acquisitions (count, states, dates, source) | Buyer research beyond this list. | Shows other leads tied to the same buyer, the List C count when the names match exactly, and buyer type / parent when present. |
| `buyer_contact` (business phone / website + source) | The borrower is usually the buyer. | Shows the property's contact only; buyer contact reads NOT FOUND. |
| `why` (one-line reason, server-built) | Same wording in UI, CSV and e-mail. | Built in the browser from the score breakdown. |
| `id` (stable across snapshots) | Lead links that survive a data refresh. | Lead URLs use the rank (`r4`), which can change between snapshots. |
| `contact.website` coverage | Only a few leads have one. | NOT FOUND in amber. |
| `list_c[].buyer_key` matching `items[].buyer` | Join repeat-buyer records to leads reliably. | Joins on the exact buyer name (few match today). |
| `breakdown` on every lead | Some leads carry only `size_fit`. | Says the point-by-point breakdown is not shown for that lead. |
