import json

import httpx
import pytest

from src.extract import client as cl


def test_provider_default_and_validation(monkeypatch):
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    assert cl.provider() == "anthropic" and cl.default_model() == cl.PRODUCTION_MODEL
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_MODEL", "gemini-test")
    assert cl.default_model() == "gemini-test"
    monkeypatch.setenv("LLM_PROVIDER", "other")
    with pytest.raises(ValueError):
        cl.provider()


def test_production_run_refuses_unapproved_model(monkeypatch):
    from src.extract.__main__ import main
    with pytest.raises(SystemExit):
        main(["--model", "gemini-test", "--dry-run"])


def test_gemini_call_parses_json_and_keeps_key_in_header():
    seen = {}

    def handler(req: httpx.Request):
        seen["url"], seen["key"] = str(req.url), req.headers.get("x-goog-api-key")
        body = json.loads(req.content)
        assert body["generationConfig"]["responseMimeType"] == "application/json"
        return httpx.Response(200, json={"candidates": [{"finishReason": "STOP", "content": {"parts": [
            {"text": '{"property_name": "X"}'}]}}], "usageMetadata": {"promptTokenCount": 100, "candidatesTokenCount": 10}})
    http = httpx.Client(transport=httpx.MockTransport(handler), headers={"x-goog-api-key": "k"})
    spend = cl.Spend("gemini-2.5-pro")
    out = cl._gemini_one(http, "gemini-2.5-pro", "t", "b", spend)
    assert out == {"property_name": "X"} and spend.input_tokens == 100 and spend.output_tokens == 10
    assert "k" not in seen["url"].split("models/")[1] and seen["key"] == "k"
