"""Reads `.env` and builds the model endpoint. Keys are never printed or logged."""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

from . import ROOT
from .llm import Budget, Endpoint, Gemini, Limits, Store
from .llm.router import LLM
from .llm.store import cache_key

# Conservative defaults below the free tier (about 15 requests a minute and 1,000 a day for
# flash-lite, from AI Studio); `GEMINI_LIMITS=rpm,tpm,rpd,tpd` replaces them.
GEMINI_LIMITS = Limits(rpm=10, tpm=200_000, rpd=600, tpd=3_000_000)
DEFAULT_MODEL = "gemini-3.5-flash-lite"


class MissingKey(RuntimeError):
    pass


class CacheMiss(RuntimeError):
    """An offline replay asked for a response that was never recorded."""


class OfflineLLM(LLM):
    """Cache-only: answers from recorded responses and never touches the network or the budget."""

    def complete(self, messages, *, max_tokens=512, temperature=0.0, json_mode=False, schema=None):
        payload = self._payload(messages, max_tokens, temperature, json_mode, schema)
        for ep in self.endpoints:
            hit = self.store.get(cache_key(ep.provider.name, ep.model, payload))
            if hit:
                return hit
        raise CacheMiss("no recorded response for this request (run once with a key to record it)")


def load_env(path: Path = ROOT / ".env") -> dict[str, str]:
    out: dict[str, str] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip().strip('"').strip("'")
    for k in ("GEMINI_API_KEY", "GEMINI_MODEL", "GEMINI_LIMITS"):  # a real env var wins
        if k in os.environ:
            out[k] = os.environ[k]
    return out


def _limits(raw: str | None) -> Limits:
    if not raw:
        return GEMINI_LIMITS
    parts = [int(x) for x in raw.split(",")]
    if len(parts) != 4:
        raise ValueError("GEMINI_LIMITS must be rpm,tpm,rpd,tpd")
    return Limits(*parts)


def model_name(env: Mapping[str, str] | None = None) -> str:
    e = load_env() if env is None else env
    return e.get("GEMINI_MODEL") or DEFAULT_MODEL


def build_llm(
    env: Mapping[str, str] | None = None, store: Store | None = None, *, offline: bool = False
) -> LLM:
    """The model router. `offline=True` builds a cache-only router: any cache miss raises, so a
    replay (CI, the hosted pipeline, the evaluation) can never spend quota or need a key."""
    # Offline replay must not even open a secrets file. The model name may still be
    # selected through the process environment so custom recordings can be replayed.
    e = (
        ({"GEMINI_MODEL": os.environ.get("GEMINI_MODEL", DEFAULT_MODEL)} if offline else load_env())
        if env is None
        else dict(env)
    )
    store = store or Store()
    model = e.get("GEMINI_MODEL") or DEFAULT_MODEL
    if offline:
        ep = Endpoint(Gemini(""), model, Budget(f"gemini/{model}", Limits(0, 0, 0, 0), store))
        return OfflineLLM([ep], store)
    key = e.get("GEMINI_API_KEY", "")
    if not key:
        raise MissingKey("put GEMINI_API_KEY in .env (see .env.example)")
    budget = Budget(f"gemini/{model}", _limits(e.get("GEMINI_LIMITS")), store)
    return LLM([Endpoint(Gemini(key), model, budget)], store)
