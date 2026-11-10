"""Claude calls for extraction: one-off (sync) and Message Batches (50% cheaper), with a local result cache.

Cache: data/raw/cache/extract/<model>/<prompt_version>/<content_hash>.json (gitignored). Any document whose
content_hash was already extracted with the same model + prompt version costs nothing on a re-run.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

import anthropic

from src.common.config import CACHE_DIR, env

from .prompt import PROMPT_VERSION, SYSTEM_PROMPT, user_message
from .schema import JSON_SCHEMA

DEFAULT_MODEL = "claude-sonnet-5-5"   # Gate 3 decision (Nov 10, 2026): Sonnet for all articles (holdout: buyer 12/12, 0 hallucinations)
MAX_TOKENS = 4000

# USD per million tokens (Claude API list prices, Nov 2026). Batch = 50% off; cache read = 0.1x, cache write = 1.25x.
PRICES = {
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-opus-5-5": (4.00, 20.00),
}


def client() -> anthropic.Anthropic:
    return anthropic.Anthropic(api_key=env("ANTHROPIC_API_KEY"), max_retries=4)


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


def extract_one(api: anthropic.Anthropic, model: str, title, body, spend: Spend | None = None) -> dict:
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
