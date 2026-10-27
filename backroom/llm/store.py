"""One SQLite file holds the response cache and the daily usage counters.

Both must outlive the process: the cache is what makes a rerun free and a crashed run resumable,
and the counters are what stop a restart from forgetting how much of today's free quota is spent.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from array import array
from dataclasses import dataclass
from pathlib import Path

from .. import CACHE_DB

DEFAULT_DB = CACHE_DB


@dataclass(frozen=True)
class Completion:
    text: str
    prompt_tokens: int
    completion_tokens: int
    model: str
    cached: bool = False
    latency_ms: int = (
        0  # how long the provider took, kept with the answer so a replay can report it
    )


def cache_key(provider: str, model: str, payload: dict) -> str:
    blob = json.dumps({"p": provider, "m": model, "x": payload}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


class Store:
    def __init__(self, path: Path | str = DEFAULT_DB):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path))
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS calls (
              key TEXT PRIMARY KEY, model TEXT NOT NULL, text TEXT NOT NULL,
              prompt_tokens INTEGER NOT NULL, completion_tokens INTEGER NOT NULL,
              created REAL NOT NULL, latency_ms INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS embeddings (
              key TEXT PRIMARY KEY, vector BLOB NOT NULL
            );
            CREATE TABLE IF NOT EXISTS daily (
              name TEXT NOT NULL, day TEXT NOT NULL,
              requests INTEGER NOT NULL DEFAULT 0, tokens INTEGER NOT NULL DEFAULT 0,
              PRIMARY KEY (name, day)
            );
            """
        )

    def get(self, key: str) -> Completion | None:
        row = self.db.execute(
            "SELECT model, text, prompt_tokens, completion_tokens, latency_ms FROM calls "
            "WHERE key = ?",
            (key,),
        ).fetchone()
        if row is None:
            return None
        return Completion(
            text=row[1],
            prompt_tokens=row[2],
            completion_tokens=row[3],
            model=row[0],
            cached=True,
            latency_ms=row[4],
        )

    def put(self, key: str, c: Completion) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO calls VALUES (?, ?, ?, ?, ?, ?, ?)",
            (key, c.model, c.text, c.prompt_tokens, c.completion_tokens, time.time(), c.latency_ms),
        )
        self.db.commit()

    def get_vectors(self, keys: list[str]) -> dict[str, array]:
        out: dict[str, array] = {}
        for i in range(0, len(keys), 500):
            chunk = keys[i : i + 500]
            marks = ",".join("?" * len(chunk))
            for key, blob in self.db.execute(
                f"SELECT key, vector FROM embeddings WHERE key IN ({marks})",  # noqa: S608 (only ? marks)
                chunk,
            ):
                v = array("f")
                v.frombytes(blob)
                out[key] = v
        return out

    def put_vectors(self, items: dict[str, list[float]]) -> None:
        self.db.executemany(
            "INSERT OR REPLACE INTO embeddings VALUES (?, ?)",
            [(k, array("f", v).tobytes()) for k, v in items.items()],
        )
        self.db.commit()

    def daily(self, name: str, day: str) -> tuple[int, int]:
        row = self.db.execute(
            "SELECT requests, tokens FROM daily WHERE name = ? AND day = ?", (name, day)
        ).fetchone()
        return (row[0], row[1]) if row else (0, 0)

    def add_daily(self, name: str, day: str, requests: int, tokens: int) -> None:
        self.db.execute(
            "INSERT INTO daily (name, day, requests, tokens) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(name, day) DO UPDATE SET requests = requests + ?, tokens = tokens + ?",
            (name, day, requests, tokens, requests, tokens),
        )
        self.db.commit()

    def count_calls(self) -> int:
        return self.db.execute("SELECT COUNT(*) FROM calls").fetchone()[0]
