"""Rate limits as a budget. Waits for the per-minute limits, refuses when the day's limit is spent.

Per-minute limits are waited out (sleeping is free). Daily limits cannot be waited out in a run, so
they raise `DailyLimitReached` and the router moves to the next model, or the run stops and can be
resumed tomorrow from the cache.
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from .store import Store


class DailyLimitReached(Exception):
    pass


class CallTooLarge(Exception):
    """A single call that could never fit the per-minute token limit, or the guardrail cap."""


@dataclass(frozen=True)
class Limits:
    rpm: int
    tpm: int
    rpd: int
    tpd: int


def utc_day(now: float) -> str:
    return datetime.fromtimestamp(now, UTC).strftime("%Y-%m-%d")


class Budget:
    def __init__(
        self,
        name: str,
        limits: Limits,
        store: Store,
        *,
        max_call_tokens: int | None = None,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
        day_of: Callable[[float], str] = utc_day,
    ):
        self.name, self.limits, self.store = name, limits, store
        self.max_call_tokens = max_call_tokens
        self.clock, self.sleep, self.day_of = clock, sleep, day_of
        self._window: deque[list] = deque()  # [time, tokens], entries edited in place on settle

    def _purge(self, now: float) -> None:
        while self._window and self._window[0][0] <= now - 60:
            self._window.popleft()

    def reserve(self, tokens: int) -> list:
        """Book `tokens` (an upper estimate) and return the booking; waits if the minute is full."""
        cap = min(self.limits.tpm, self.max_call_tokens or self.limits.tpm)
        if tokens > cap:
            raise CallTooLarge(f"{self.name}: {tokens} tokens exceeds the per-call cap of {cap}")
        while True:
            now = self.clock()
            requests, used = self.store.daily(self.name, self.day_of(now))
            if requests + 1 > self.limits.rpd or used + tokens > self.limits.tpd:
                raise DailyLimitReached(
                    f"{self.name}: day's limit reached ({requests} requests, {used} tokens used)"
                )
            self._purge(now)
            in_window = sum(e[1] for e in self._window)
            if len(self._window) + 1 <= self.limits.rpm and in_window + tokens <= self.limits.tpm:
                booking = [now, tokens]
                self._window.append(booking)
                self.store.add_daily(self.name, self.day_of(now), 1, tokens)
                return booking
            wait = (self._window[0][0] + 60 - now) if self._window else 0.05
            self.sleep(max(wait, 0.05))

    def settle(self, booking: list, actual: int) -> None:
        """Replace the estimate with what the provider reported."""
        self.store.add_daily(self.name, self.day_of(booking[0]), 0, actual - booking[1])
        booking[1] = actual

    def release(self, booking: list) -> None:
        """A call that failed before the model ran costs no tokens (it still counts as a call)."""
        self.settle(booking, 0)
