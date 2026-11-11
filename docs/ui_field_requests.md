# UI field requests (ui-v2)

Fields the new UI would use if the snapshot carried them. Nothing here blocks the UI: each one degrades as described.

| Field (per lead) | Why | Today the UI does |
|---|---|---|
| `id` (stable across snapshots) | Decisions, notes and follow-ups are keyed to a lead. | Builds a key from property + city + state (unique in the current snapshot; would break if a property is renamed). |
| `signal_details[]`: `{signal, date, source_url}` | Must-have: each signal with its own source link and date. | Shows signals, then all source links for the lead together; only the latest signal date is shown. |
| `lat`, `lon` | Pins on the map. | Map shades states only. |
| `buyer_track_record`: prior acquisitions (count, states, dates, source) | Buyer research beyond this list. | Shows other leads in the list tied to the same buyer, plus buyer type / parent when present. |
| `buyer_contact` (business phone / website + source) | The borrower is usually the buyer. | Shows the property's contact only; buyer contact reads NOT FOUND. |
| `why` (one-line reason, server-built) | Same wording in UI, CSV and e-mail. | Built in the browser from the score breakdown. |
| `first_seen` / `last_updated` as timestamps | "New since your last visit" on same-day refreshes. | Dates are present and used; compared by day against the previous snapshot the browser saw. |
| `contact.website` coverage | Only a few leads have one. | NOT FOUND in amber. |
