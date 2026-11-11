"""Model calls for extraction: one-off (sync) and Message Batches (Claude only, 50% cheaper), with a local result cache.

Providers (LLM_PROVIDER in .env: anthropic | gemini, default anthropic). Keys are read only from .env
(ANTHROPIC_API_KEY / GEMINI_API_KEY) through src.common.config.env, sent only in request headers, never logged,
printed or cached. The model id picks the provider ("gemini-..." -> Gemini, else Claude).
PRODUCTION_MODEL is what the pipeline writes and scores. A provider becomes production only after it matches the
current model on the holdout + dev sets (decision Nov 10, 2026: Gemini must reach buyer 12/12 and 0 hallucinations
on holdout v1 first); until then LLM_PROVIDER=gemini changes the default model for evaluations only.

Cache: data/raw/cache/extract/<model>/<prompt_version>/<content_hash>.json (gitignored). Any document whose
content_hash was already extracted with the same model + prompt version costs nothing on a re-run.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

import os
from types import SimpleNamespace

import anthropic
import httpx

from src.common.config import CACHE_DIR, env

from .prompt import PROMPT_VERSION, SYSTEM_PROMPT, user_message
from .schema import JSON_SCHEMA

DEFAULT_MODEL = "claude-sonnet-5-5"   # Gate 3 decision (Nov 10, 2026): Sonnet for all articles (holdout: buyer 12/12, 0 hallucinations)
PRODUCTION_MODEL = DEFAULT_MODEL      # the model whose answers the pipeline stores and scores (see module doc)
MAX_TOKENS = 4000
PROVIDERS = ("anthropic", "gemini")
GEMINI_DEFAULT_MODEL = "gemini-2.5-pro"   # ASSUMPTION: override with GEMINI_MODEL in .env, no code change needed
GEMINI_API = "https://generativelanguage.googleapis.com/v1beta/models"


def provider() -> str:
    p = os.environ.get("LLM_PROVIDER", "anthropic").strip().lower() or "anthropic"
    if p not in PROVIDERS:
        raise ValueError(f"LLM_PROVIDER must be one of {PROVIDERS}, not {p!r}")
    return p


def default_model() -> str:
    """Default model for evaluations: the provider's model (production writes always use PRODUCTION_MODEL)."""
    if provider() == "gemini":
        return os.environ.get("GEMINI_MODEL", "").strip() or GEMINI_DEFAULT_MODEL
    return DEFAULT_MODEL


def is_gemini(model: str) -> bool:
    return model.startswith("gemini")

# USD per million tokens (Claude API list prices, Nov 2026). Batch = 50% off; cache read = 0.1x, cache write = 1.25x.
PRICES = {
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-opus-5-5": (4.00, 20.00),
    # ASSUMPTION: Google list prices as last known (<=200k-token prompts); check before any large Gemini run
    "gemini-2.5-pro": (1.25, 10.00),
    "gemini-2.5-flash": (0.30, 2.50),
}


class Clients:
    """Lazily opens the provider a model needs: .anthropic (Claude, incl. Message Batches) and .gemini (httpx)."""

    def __init__(self):
        self._a = self._g = None

    @property
    def anthropic(self) -> anthropic.Anthropic:
        if self._a is None:
            self._a = anthropic.Anthropic(api_key=env("ANTHROPIC_API_KEY"), max_retries=4)
        return self._a

    @property
    def gemini(self) -> httpx.Client:
        if self._g is None:
            # key in a header (never in the URL, so it cannot end up in a logged URL)
            self._g = httpx.Client(headers={"x-goog-api-key": env("GEMINI_API_KEY")}, timeout=120)
        return self._g

    @property
    def messages(self):           # backward compatible: code that used client().messages gets Claude
        return self.anthropic.messages


def client() -> Clients:
    return Clients()


def _anthropic(api) -> anthropic.Anthropic:
    return api.anthropic if isinstance(api, Clients) else api


# How each model turns thinking off (the API rejects "disabled" on Sonnet 5.5 and asks for "between_tools").
THINKING_OFF = {"claude-sonnet-5-5": {"type": "between_tools"}}


def request_params(model: str, title: str | None, body: str | None) -> dict:
    """Fixed instructions first (cached), the document last. Thinking off + low effort: pure copy task."""
    return {
        "model": model,
        "max_tokens": MAX_TOKENS,
        "system": [{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
        "messages": [{"role": "user", "content": user_message(title, body)}],
        "thinking": THINKING_OFF.get(model, {"type": "disabled"}),
        "output_config": {"format": {"type": "json_schema", "schema": JSON_SCHEMA}, "effort": "low"},
    }


def cache_path(model: str, content_hash: str, prompt_version: str = PROMPT_VERSION) -> Path:
    return CACHE_DIR / "extract" / model / prompt_version / f"{content_hash}.json"


def cache_get(model: str, content_hash: str) -> dict | None:
    p = cache_path(model, content_hash)
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return None


def cache_put(model: str, content_hash: str, raw: dict) -> None:
    p = cache_path(model, content_hash)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(raw, ensure_ascii=False, indent=1), encoding="utf-8")


@dataclass
class Spend:
    model: str
    batch: bool = False
    input_tokens: int = 0
    cache_read: int = 0
    cache_write: int = 0
    output_tokens: int = 0
    calls: int = 0

    def add(self, usage) -> None:
        self.calls += 1
        self.input_tokens += usage.input_tokens or 0
        self.cache_read += getattr(usage, "cache_read_input_tokens", 0) or 0
        self.cache_write += getattr(usage, "cache_creation_input_tokens", 0) or 0
        self.output_tokens += usage.output_tokens or 0

    @property
    def usd(self) -> float:
        pin, pout = PRICES.get(self.model, PRICES["claude-sonnet-5-5"])
        cost = (self.input_tokens * pin + self.cache_read * pin * 0.1 + self.cache_write * pin * 1.25
                + self.output_tokens * pout) / 1e6
        return cost * (0.5 if self.batch else 1.0)


def _parse(message) -> dict:
    if message.stop_reason == "refusal":
        raise RuntimeError("model refused")
    if message.stop_reason == "max_tokens":
        raise RuntimeError("output hit max_tokens")
    text = next((b.text for b in message.content if b.type == "text"), "")
    return json.loads(text)


def _gemini_one(http: httpx.Client, model: str, title, body, spend: Spend | None) -> dict:
    """Same prompt, same JSON schema, deterministic. Retries 429 / 5xx a few times."""
    payload = {"systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
               "contents": [{"role": "user", "parts": [{"text": user_message(title, body)}]}],
               "generationConfig": {"temperature": 0, "maxOutputTokens": MAX_TOKENS,
                                    "responseMimeType": "application/json", "responseJsonSchema": JSON_SCHEMA}}
    for attempt in range(5):
        r = http.post(f"{GEMINI_API}/{model}:generateContent", json=payload)
        if r.status_code in (429, 500, 502, 503, 504) and attempt < 4:
            time.sleep(2 ** attempt * 5)
            continue
        if r.status_code != 200:
            # error text only (the key is in a header and never in the response)
            raise RuntimeError(f"Gemini HTTP {r.status_code}: {r.text[:300]}")
        break
    data = r.json()
    um = data.get("usageMetadata") or {}
    if spend is not None:
        spend.add(SimpleNamespace(input_tokens=um.get("promptTokenCount", 0) - um.get("cachedContentTokenCount", 0),
                                  cache_read_input_tokens=um.get("cachedContentTokenCount", 0),
                                  cache_creation_input_tokens=0,
                                  output_tokens=um.get("candidatesTokenCount", 0) + um.get("thoughtsTokenCount", 0)))
    cand = (data.get("candidates") or [{}])[0]
    if cand.get("finishReason") == "MAX_TOKENS":
        raise RuntimeError("output hit max_tokens")
    if cand.get("finishReason") not in (None, "STOP"):
        raise RuntimeError(f"model stopped: {cand.get('finishReason')}")
    text = "".join(p.get("text", "") for p in (cand.get("content") or {}).get("parts", []) if not p.get("thought"))
    return json.loads(text)


def extract_one(api, model: str, title, body, spend: Spend | None = None) -> dict:
    if is_gemini(model):
        return _gemini_one(api.gemini, model, title, body, spend)
    api = _anthropic(api)
    params = request_params(model, title, body)
    try:
        msg = api.messages.create(**params)
    except anthropic.BadRequestError as e:
        # Some models turn thinking off with {"type": "between_tools"} instead of "disabled" (the API says which).
        if "between_tools" not in str(e):
            raise
        msg = api.messages.create(**{**params, "thinking": {"type": "between_tools"}})
    if spend is not None:
        spend.add(msg.usage)
    return _parse(msg)


@dataclass
class BatchOutcome:
    results: dict = field(default_factory=dict)   # custom_id -> raw dict
    errors: dict = field(default_factory=dict)    # custom_id -> error text


def extract_batch(api: anthropic.Anthropic, model: str, docs: list[dict], spend: Spend,
                  poll_seconds: int = 30, log=print) -> BatchOutcome:
    """docs: [{custom_id, title, body}]. Submits one Message Batch (chunks of 5,000), waits, returns parsed results."""
    from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
    from anthropic.types.messages.batch_create_params import Request

    if is_gemini(model):
        raise ValueError("Message Batches are Claude-only; run Gemini with --sync")
    api = _anthropic(api)
    out = BatchOutcome()
    for start in range(0, len(docs), 5000):
        chunk = docs[start:start + 5000]
        reqs = [Request(custom_id=d["custom_id"],
                        params=MessageCreateParamsNonStreaming(**request_params(model, d["title"], d["body"])))
                for d in chunk]
        batch = api.messages.batches.create(requests=reqs)
        log(f"  submitted batch {batch.id} with {len(reqs)} requests")
        while True:
            batch = api.messages.batches.retrieve(batch.id)
            if batch.processing_status == "ended":
                break
            rc = batch.request_counts
            log(f"  batch {batch.id}: processing={rc.processing} succeeded={rc.succeeded} errored={rc.errored}")
            time.sleep(poll_seconds)
        for res in api.messages.batches.results(batch.id):
            if res.result.type == "succeeded":
                spend.add(res.result.message.usage)
                try:
                    out.results[res.custom_id] = _parse(res.result.message)
                except Exception as exc:  # bad JSON / refusal / truncated
                    out.errors[res.custom_id] = f"{type(exc).__name__}: {exc}"[:500]
            else:
                err = getattr(res.result, "error", None)
                out.errors[res.custom_id] = f"{res.result.type}: {err}"[:500]
    return out
