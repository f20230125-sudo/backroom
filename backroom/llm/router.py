"""The one entry point for model calls: cache, then budget, then provider, with failover."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from .budget import Budget, CallTooLarge, DailyLimitReached
from .providers import Messages, Provider, ProviderError, RateLimited
from .store import Completion, Store, cache_key


@dataclass
class Endpoint:
    provider: Provider
    model: str
    budget: Budget


class AllEndpointsExhausted(Exception):
    pass


def estimate_tokens(messages: Messages, max_tokens: int) -> int:
    """A deliberately high estimate (3 characters a token; filings are full of digits), replaced by
    the provider's own count once the call returns."""
    return sum(len(m["content"]) for m in messages) // 3 + max_tokens


class LLM:
    def __init__(
        self,
        endpoints: list[Endpoint],
        store: Store,
        *,
        sleep: Callable[[float], None] = time.sleep,
        retries: int = 4,
    ):
        self.endpoints, self.store, self.sleep, self.retries = endpoints, store, sleep, retries
        self.calls_made = 0  # provider calls this process has spent (cache hits are free)

    def _payload(self, messages, max_tokens, temperature, json_mode, schema=None) -> dict:
        payload = {
            "messages": messages,
            "max_tokens": max_tokens,
            "t": temperature,
            "json": json_mode,
        }
        if schema is not None:
            payload["schema"] = schema  # a changed schema is a different request, so a cache miss
        return payload

    def complete(
        self,
        messages: Messages,
        *,
        max_tokens: int = 512,
        temperature: float = 0.0,
        json_mode: bool = False,
        schema: dict | None = None,
    ) -> Completion:
        payload = self._payload(messages, max_tokens, temperature, json_mode, schema)
        keys = [cache_key(e.provider.name, e.model, payload) for e in self.endpoints]
        for key in keys:
            hit = self.store.get(key)
            if hit:
                return hit

        why: list[str] = []
        for ep, key in zip(self.endpoints, keys, strict=True):
            try:
                booking = ep.budget.reserve(estimate_tokens(messages, max_tokens))
            except (DailyLimitReached, CallTooLarge) as e:
                why.append(str(e))
                continue
            done = self._call(
                ep, booking, messages, max_tokens, temperature, json_mode, why, schema
            )
            if done:
                self.store.put(key, done)
                return done
        raise AllEndpointsExhausted("; ".join(why) or "no endpoints configured")

    def _call(
        self, ep, booking, messages, max_tokens, temperature, json_mode, why, schema=None
    ) -> Completion | None:
        for attempt in range(self.retries + 1):
            try:
                self.calls_made += 1
                c = ep.provider.chat(
                    ep.model,
                    messages,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    json_mode=json_mode,
                    schema=schema,
                )
                ep.budget.settle(booking, c.prompt_tokens + c.completion_tokens)
                return c
            except RateLimited as e:
                ep.budget.release(booking)
                why.append(f"{ep.model} rate limited")
                if attempt == self.retries:
                    return None
                self.sleep(min(e.retry_after or 2.0 * 2**attempt, 60.0))
            except ProviderError as e:
                ep.budget.release(booking)
                if e.status < 500:
                    raise
                why.append(f"{ep.model} server error {e.status}")
                if attempt == self.retries:
                    return None
                self.sleep(min(2.0 * 2**attempt, 60.0))
            try:
                booking = ep.budget.reserve(estimate_tokens(messages, max_tokens))
            except (DailyLimitReached, CallTooLarge) as e:
                why.append(str(e))
                return None
        return None
