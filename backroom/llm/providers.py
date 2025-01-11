"""Provider clients: Gemini (REST) and Groq (OpenAI-compatible). No SDKs, just httpx.

Keys are read from the environment by the caller and passed in; nothing here logs or prints them.
"""

from __future__ import annotations

import time
from typing import Protocol

import httpx

from .store import Completion

Messages = list[dict[str, str]]  # [{"role": "system"|"user"|"assistant", "content": "..."}]


class ProviderError(Exception):
    def __init__(self, status: int, body: str):
        super().__init__(f"HTTP {status}: {body[:300]}")
        self.status = status


class RateLimited(ProviderError):
    def __init__(self, status: int, body: str, retry_after: float | None):
        super().__init__(status, body)
        self.retry_after = retry_after


class Provider(Protocol):
    name: str

    def chat(
        self,
        model: str,
        messages: Messages,
        *,
        max_tokens: int,
        temperature: float,
        json_mode: bool,
        schema: dict | None = None,
    ) -> Completion: ...


def _raise_for(resp: httpx.Response) -> None:
    if resp.status_code == 429:
        ra = resp.headers.get("retry-after")
        raise RateLimited(
            429, resp.text, float(ra) if ra and ra.replace(".", "").isdigit() else None
        )
    if resp.status_code >= 400:
        raise ProviderError(resp.status_code, resp.text)


class Groq:
    name = "groq"
    url = "https://api.groq.com/openai/v1/chat/completions"

    def __init__(self, api_key: str, client: httpx.Client | None = None, extra: dict | None = None):
        self._key = api_key
        self.client = client or httpx.Client(timeout=120)
        self.extra = extra or {}

    def chat(self, model, messages, *, max_tokens, temperature, json_mode, schema=None):
        body = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            **self.extra,
        }
        if schema is not None:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "output", "schema": schema},
            }
        elif json_mode:
            body["response_format"] = {"type": "json_object"}
        t0 = time.perf_counter()
        resp = self.client.post(
            self.url, json=body, headers={"Authorization": f"Bearer {self._key}"}
        )
        ms = round((time.perf_counter() - t0) * 1000)
        _raise_for(resp)
        data = resp.json()
        usage = data.get("usage") or {}
        return Completion(
            text=(data["choices"][0]["message"].get("content") or ""),
            prompt_tokens=int(usage.get("prompt_tokens", 0)),
            completion_tokens=int(usage.get("completion_tokens", 0)),
            model=model,
            latency_ms=ms,
        )


class Gemini:
    name = "gemini"
    base = "https://generativelanguage.googleapis.com/v1beta"

    def __init__(self, api_key: str, client: httpx.Client | None = None):
        self._key = api_key
        self.client = client or httpx.Client(timeout=120)

    def _headers(self) -> dict[str, str]:
        return {"x-goog-api-key": self._key}

    def chat(self, model, messages, *, max_tokens, temperature, json_mode, schema=None):
        system ="\n\n".join(m["content"] for m in messages if m["role"] == "system")
        contents = [
            {
                "role": "model" if m["role"] == "assistant" else "user",
                "parts": [{"text": m["content"]}],
            }
            for m in messages
            if m["role"] != "system"
        ]
        gen = {"maxOutputTokens": max_tokens, "temperature": temperature}
        if json_mode or schema is not None:
            gen["responseMimeType"] = "application/json"
        if schema is not None:
            # Structured output: the model must return JSON matching this JSON Schema.
            gen["responseJsonSchema"] = schema
        body: dict = {"contents": contents, "generationConfig": gen}
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        t0 = time.perf_counter()
        resp = self.client.post(
            f"{self.base}/models/{model}:generateContent", json=body, headers=self._headers()
        )
        ms = round((time.perf_counter() - t0) * 1000)
        _raise_for(resp)
        data = resp.json()
        parts = ((data.get("candidates") or [{}])[0].get("content") or {}).get("parts") or []
        usage = data.get("usageMetadata") or {}
        return Completion(
            text="".join(p.get("text", "") for p in parts),
            prompt_tokens=int(usage.get("promptTokenCount", 0)),
            completion_tokens=int(usage.get("candidatesTokenCount", 0)),
            model=model,
            latency_ms=ms,
        )

    def embed(self, model: str, texts: list[str], *, task: str) -> list[list[float]]:
        """`task` is RETRIEVAL_DOCUMENT or RETRIEVAL_QUERY. Batched, up to 100 texts per request."""
        out: list[list[float]] = []
        for i in range(0, len(texts), 100):
            chunk = texts[i : i + 100]
            body = {
                "requests": [
                    {
                        "model": f"models/{model}",
                        "content": {"parts": [{"text": t}]},
                        "taskType": task,
                    }
                    for t in chunk
                ]
            }
            resp = self.client.post(
                f"{self.base}/models/{model}:batchEmbedContents", json=body, headers=self._headers()
            )
            _raise_for(resp)
            out.extend(e["values"] for e in resp.json()["embeddings"])
        return out
