# Holdout extraction test (extraction_holdout_v1)

Labels human-verified (Aiden, 2026-11-10). Articles fetched: 16/20.

## claude-haiku-5-5
Articles fully correct: **5/16 scored** (4 not available) — pass bar 18/20 and 0 buyer hallucinations
Fully correct ignoring event_dates/amounts/stage_text: 7/16
Buyer accuracy: 9/12 distress articles · buyer hallucinations: **3**
Hallucinations (all fields): 3 {'buyer_or_new_operator': 3}
Stage computed in code correct: 13/16

| Field | Correct | via soft |
|---|---|---|
| property_name | 11/12 | 0 |
| street | 11/12 | 0 |
| city | 12/12 | 0 |
| state | 11/12 | 0 |
| units | 10/12 | 0 |
| beds | 11/12 | 0 |
| care_type | 11/12 | 0 |
| distress_type | 14/16 | 0 |
| stage_text | 12/12 | 0 |
| debtor_or_owner | 10/12 | 0 |
| receiver_or_trustee | 12/12 | 0 |
| buyer_or_new_operator | 9/12 | 0 |
| lender | 11/12 | 0 |
| event_dates | 5/12 | 0 |
| amounts | 10/12 | 0 |

| Case | Result | Misses | Stage (code / label) | Buyer (AI / label) |
|---|---|---|---|---|
| H01 | ❌ | units, beds, care_type, distress_type, event_dates | sale / sale | Focus Healthcare Partners LLC / Focus Healthcare Partners LLC |
| H02 | ✅ |  | sale / sale | NOT FOUND / NOT FOUND |
| H03 | ❌ | property_name | late / late | NOT FOUND / NOT FOUND |
| H04 | ❌ | debtor_or_owner, expected_stage | sale / late | Fannie Mae / Fannie Mae |
| H05 | ❌ | lender, amounts | late / late | NOT FOUND / NOT FOUND |
| H06 | ❌ | event_dates | late / late | NOT FOUND / NOT FOUND |
| H07 | not available | | | |
| H08 | ❌ | street, event_dates, amounts | late / late | NOT FOUND / NOT FOUND |
| H09 | ❌ | event_dates | late / late | NOT FOUND / NOT FOUND |
| H10 | ❌ | distress_type, buyer_or_new_operator, event_dates, expected_stage | middle / early | Frontier Senior Living / NOT FOUND |
| H11 | not available | | | |
| H12 | ❌ | units, event_dates | sale / sale | Sonida Senior Living / Sonida Senior Living |
| H13 | ❌ | state, debtor_or_owner, buyer_or_new_operator, event_dates, expected_stage | middle / late | Alcore Senior / NOT FOUND |
| H14 | ❌ | buyer_or_new_operator | late / late | Brightwater Senior Living / NOT FOUND |
| H15 | not available | | | |
| N16 | ✅ |  | NOT FOUND / NOT FOUND | NOT FOUND / NOT FOUND |
| N17 | ✅ |  | NOT FOUND / NOT FOUND | NOT FOUND / NOT FOUND |
| N18 | ✅ |  | NOT FOUND / NOT FOUND | Senior Resource Group; TIAA Real Estate Account / Senior Resource Group and the TIAA Real Estate Account |
| N19 | ✅ |  | NOT FOUND / NOT FOUND | Discovery Senior Living / Discovery Senior Living |
| N20 | not available | | | |

API cost this run: $0.000

## claude-sonnet-5-5
Articles fully correct: **6/16 scored** (4 not available) — pass bar 18/20 and 0 buyer hallucinations
Fully correct ignoring event_dates/amounts/stage_text: 9/16
Buyer accuracy: 12/12 distress articles · buyer hallucinations: **0**
Hallucinations (all fields): 0 {}
Stage computed in code correct: 12/16

| Field | Correct | via soft |
|---|---|---|
| property_name | 11/12 | 0 |
| street | 10/12 | 0 |
| city | 11/12 | 0 |
| state | 10/12 | 0 |
| units | 11/12 | 0 |
| beds | 11/12 | 0 |
| care_type | 11/12 | 0 |
| distress_type | 14/16 | 0 |
| stage_text | 11/12 | 0 |
| debtor_or_owner | 11/12 | 0 |
| receiver_or_trustee | 12/12 | 0 |
| buyer_or_new_operator | 12/12 | 0 |
| lender | 12/12 | 0 |
| event_dates | 4/12 | 0 |
| amounts | 7/12 | 0 |

| Case | Result | Misses | Stage (code / label) | Buyer (AI / label) |
|---|---|---|---|---|
| H01 | ❌ | units, beds, distress_type, event_dates | sale / sale | Focus Healthcare Partners LLC / Focus Healthcare Partners LLC |
| H02 | ✅ |  | sale / sale | NOT FOUND / NOT FOUND |
| H03 | ❌ | property_name, street, city, state, amounts | late / late | NOT FOUND / NOT FOUND |
| H04 | ❌ | debtor_or_owner, expected_stage | sale / late | Fannie Mae / Fannie Mae |
| H05 | ❌ | event_dates, amounts | late / late | NOT FOUND / NOT FOUND |
| H06 | ❌ | care_type, event_dates | late / late | NOT FOUND / NOT FOUND |
| H07 | not available | | | |
| H08 | ❌ | street, event_dates, amounts, expected_stage | sale / late | NOT FOUND / NOT FOUND |
| H09 | ❌ | event_dates | late / late | NOT FOUND / NOT FOUND |
| H10 | ❌ | distress_type, stage_text, event_dates, expected_stage | NOT FOUND / early | NOT FOUND / NOT FOUND |
| H11 | not available | | | |
| H12 | ❌ | event_dates, amounts | sale / sale | Sonida Senior Living / Sonida Senior Living |
| H13 | ❌ | state, event_dates, amounts, expected_stage | middle / late | NOT FOUND / NOT FOUND |
| H14 | ✅ |  | late / late | NOT FOUND / NOT FOUND |
| H15 | not available | | | |
| N16 | ✅ |  | NOT FOUND / NOT FOUND | NOT FOUND / NOT FOUND |
| N17 | ✅ |  | NOT FOUND / NOT FOUND | NOT FOUND / NOT FOUND |
| N18 | ✅ |  | NOT FOUND / NOT FOUND | Senior Resource Group; TIAA Real Estate Account / Senior Resource Group and the TIAA Real Estate Account |
| N19 | ✅ |  | NOT FOUND / NOT FOUND | Discovery Senior Living / Discovery Senior Living |
| N20 | not available | | | |

API cost this run: $0.000
