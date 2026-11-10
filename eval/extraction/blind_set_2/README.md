# Blind label set 2 (Gate 3 requirement)

20 articles the AI was **not** tuned on: 15 picked because their titles mention distress
(bankruptcy, receivership, foreclosure, default, closure…) and 5 random others. Picked by title words only, never by AI output.

## For the teammate labeling
1. Copy `labels_template.csv` to `labels_<your initials>.csv`.
2. Open each `source_url`, read the article, and fill one row per article. **Do not look at the AI's output** (stg.extraction or any results file) before you finish.
3. Rules:
   - Copy names, addresses and numbers **exactly as written** in the article. If something is not literally in the text, write `NOT FOUND`. Don't infer.
   - Several values in one cell: separate with `; ` (e.g. `Omega Healthcare Investors; CIBC`).
   - `care_type` — only these values: skilled_nursing, assisted_living, memory_care, independent_living, ccrc, senior_housing_other, hospital, other_health_care.
   - `distress_type` — only: chapter_11, chapter_7, bankruptcy_other, receivership, foreclosure, deed_in_lieu, loan_or_lease_default, closure, sale, operator_change.
   - `stage_text` — a short exact quote that shows the distress stage (e.g. "filed for Chapter 11"), or NOT FOUND.
   - `buyer_or_new_operator` — only a **named** buyer/new operator. "An affiliate of X" → `X`. "A Texas investor" → NOT FOUND. Proposed (stalking horse) buyers count; write `(proposed)` in notes.
   - `lender` — a lender, not a landlord. Landlords/REITs that own the building go in notes.
   - `event_dates` / `amounts` — every date / dollar amount tied to the property or deal, as written.
4. Save and send the file back (or commit it into this folder).

## Scoring
`python -m eval.extraction.score_blind eval/extraction/blind_set_2/labels_<initials>.csv`
prints per-field accuracy, **buyer accuracy separately**, buyer names the AI gave that are not in the article text, and the number of fully correct articles (need ≥18/20).
