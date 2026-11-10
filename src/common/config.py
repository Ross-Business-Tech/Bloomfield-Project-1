"""Settings shared by every pipeline step: paths, secrets from .env, and config/thresholds.yaml."""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import yaml
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"
CACHE_DIR = DATA_DIR / "raw" / "cache"   # gitignored
OUTPUT_DIR = PROJECT_ROOT / "output"

load_dotenv(PROJECT_ROOT / ".env")

# Decision (Nov 10, 2026): no client name in any request. The contact address lives only in .env
# (RESEARCH_CONTACT_EMAIL, gitignored), never in the repo.
_CONTACT = os.environ.get("RESEARCH_CONTACT_EMAIL", "").strip()
USER_AGENT = "UMich-Team1-research/0.1" + (f" (contact: {_CONTACT})" if _CONTACT else "")


class MissingSetting(RuntimeError):
    pass


def env(name: str) -> str:
    """Return a secret from .env. Never print or log the value."""
    value = os.environ.get(name, "").strip()
    if not value:
        raise MissingSetting(f"{name} is not set. Add it to .env (see .env.example).")
    return value


@lru_cache(maxsize=1)
def thresholds() -> dict:
    with open(PROJECT_ROOT / "config" / "thresholds.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)
