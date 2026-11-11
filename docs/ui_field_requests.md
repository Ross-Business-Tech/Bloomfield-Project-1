# UI field requests (ui-v2)

Fields the new UI would use if the snapshot carried them. Nothing here blocks the UI: each one degrades as described.

| Field (per lead) | Why | Today the UI does |
|---|---|---|
| `signal_details[]`: `{signal, date, source_url}` | Must-have: each signal with its own source link and date. | Shows signals, then all source links for the lead together; only the latest signal date is shown. |
| `buyer_track_record`: prior acquisitions (count, states, dates, source) | Buyer research beyond this list. | Shows other leads tied to the same buyer, the List C count when the names match exactly, and buyer type / parent when present. |
| `buyer_contact` (business phone / website + source) | The borrower is usually the buyer. | Shows the property's contact only; buyer contact reads NOT FOUND. |
| `why` (one-line reason, server-built) | Same wording in UI, CSV and e-mail. | Built in the browser from the score breakdown. |
| `contact.website` coverage | Only a few leads have one. | NOT FOUND in amber. |
| `breakdown` on every lead | Some leads carry only `size_fit`. | Says the point-by-point breakdown is not shown for that lead. |
| `items[].list_c` (true/false) or `items[].list_c_buyer` | Per-lead List C membership, so List C can be a map layer like A and B. The summary block says 43 leads are linked, but no lead carries the link. | The map shows "C · 159 buyers" as a link to the Buyers view, where List C is its own section. Buyer blocks show the List C record only when the buyer name matches exactly. |
| `coord_source` other than `zip_centroid` (e.g. `address`) | Street-level pins. | Positions are ZIP-code centres: the legend says "approximate", the map never zooms closer than about a 60-mile window, and leads sharing a ZIP are fanned out around one dashed circle. 27 leads have no coordinates and appear in lists only. |

Delivered since the first request (now used by the UI): `lead_id` (lead links and URL state), `lat` / `lon` / `coord_source` (city bubbles and pins), `summary` block (all headline counts), `rank_a` / `rank_b` (list positions).
