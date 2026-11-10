"""Offline tests for the extraction step (no API calls, no database)."""
import json

from eval.extraction.scorer import field_correct, norm_amount, norm_date, score_case
from src.extract.client import request_params
from src.extract.evaluate import load_cases
from src.extract.prefilter import passes
from src.extract.prompt import PROMPT_VERSION, SYSTEM_PROMPT
from src.extract.schema import FIELDS, JSON_SCHEMA, NOT_FOUND, clean_party, postprocess

NF = NOT_FOUND


def _raw(**kw):
    base = {f: NF for f in FIELDS}
    base.update(care_type=[], distress_type=[], event_dates=[], amounts=[], other_properties=[])
    base.update(kw)
    return base


def test_schema_is_strict_and_covers_build_spec_fields():
    assert JSON_SCHEMA["additionalProperties"] is False
    assert set(FIELDS) <= set(JSON_SCHEMA["required"])
    assert len(FIELDS) == 15


def test_request_params_cache_fixed_instructions_and_use_structured_output():
    p = request_params("claude-haiku-5-5", "T", "B")
    assert p["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert p["output_config"]["format"]["schema"] is JSON_SCHEMA
    assert "T" in p["messages"][0]["content"] and "B" in p["messages"][0]["content"]


def test_prompt_examples_do_not_leak_eval_answers():
    for case in load_cases():
        for f in ("property_name", "debtor_or_owner", "buyer_or_new_operator", "receiver_or_trustee", "city"):
            vals = case["expected"][f] if isinstance(case["expected"][f], list) else [case["expected"][f]]
            for v in vals:
                if v != NF:
                    assert v not in SYSTEM_PROMPT, (case["doc_id"], f, v)


def test_postprocess_empty_values_become_not_found():
    fields, nf = postprocess(_raw(property_name="  ", city="Springfield"), "in Springfield today")
    assert fields["property_name"] == NF and fields["care_type"] == NF and fields["amounts"] == NF
    assert "property_name" in nf and "city" not in nf
    assert fields["unverified_fields"] == []


def test_postprocess_flags_values_not_in_text():
    fields, _ = postprocess(_raw(city="Chicago"), "a facility in Springfield")
    assert fields["unverified_fields"] == ["city"]


def test_postprocess_party_names_and_dates():
    fields, _ = postprocess(_raw(
        buyer_or_new_operator="A Texas-based investor", debtor_or_owner="an affiliate of Oakview Health LLC",
        event_dates=[{"date_text": "Monday", "event": "x"}, {"date_text": "May 2, 2025", "event": "filing"}],
        distress_type=["bankruptcy_other", "chapter_11"], units="1,200 units"), "")
    assert fields["buyer_or_new_operator"] == NF
    assert fields["debtor_or_owner"] == "Oakview Health LLC"
    assert [d["date_text"] for d in fields["event_dates"]] == ["May 2, 2025"]
    assert fields["distress_type"] == ["chapter_11"]
    assert fields["units"] == "1200"


def test_clean_party_keeps_names_starting_with_the():
    assert clean_party("The Bellevue Hospital") == "The Bellevue Hospital"


def test_prefilter():
    assert passes("elevenflo", "x", "")[0]
    assert passes("shn_rss", "Operator Files for Chapter 11", "")[0]
    assert passes("shb_rss", "Firm Acquires 120-Unit Community", "The seller ...")[0]
    assert not passes("shn_rss", "Future Leader: Jane Doe", "She leads resident engagement.")[0]
    assert not passes("shn_rss", "Adds Sales VP", "joins the sales team")[0]


def test_scorer_normalization():
    assert norm_amount("$76 million") == norm_amount("$76M") == "76000000"
    assert norm_amount("$808,084") == "808084"
    assert norm_date("Sep 30 2026") == norm_date("September 30, 2026")
    assert field_correct("state", ["N.Y.", "New York"], "new york")
    assert field_correct("beds", "126", "126")
    assert not field_correct("city", NF, "Denver")
    assert field_correct("amounts", {"required": ["$76 million"], "optional": ["$5 million"]},
                         [{"amount_text": "$76M", "what": "price"}])
    assert not field_correct("amounts", {"required": ["$76 million"], "optional": []},
                             [{"amount_text": "$76M", "what": "p"}, {"amount_text": "$1M", "what": "q"}])
    assert field_correct("stage_text", "ref", "has filed for Chapter 11", "Acme has filed for Chapter 11 today")
    assert not field_correct("stage_text", "ref", "filed Chapter 11 petition", "Acme has filed for Chapter 11")


def test_eval_set_shape():
    data = json.loads(open("eval/extraction/cases.json", encoding="utf-8").read())
    assert data["status"].startswith("labels pending human confirmation") or data["status"].startswith("confirmed")
    cases = data["cases"]
    assert len(cases) == 20
    assert sum(c["kind"] == "distress" for c in cases) == 12
    for c in cases:
        assert set(c["expected"]) == set(FIELDS)
        assert c["source_url"].startswith("http")
        assert c["text"] or c["excerpt"]
        perfect = {f: (v if not isinstance(v, (list, dict)) else (v[0] if isinstance(v, list) else
                   ([*v["required"]] or NF))) for f, v in c["expected"].items()}
        # list-field labels become plain lists of strings, which the scorer accepts
        doc = (c["text"] or "") + " " + (c["expected"]["stage_text"] if c["expected"]["stage_text"] != NF else "")
        assert score_case(c, perfect, doc)["fully_correct"], c["doc_id"]
