"""The schema path added on top of the copied router: it reaches the provider and keys the cache."""

import httpx

from backroom.llm import Budget, Endpoint, Gemini, Limits, Store
from backroom.llm.router import LLM

SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}


def _gemini(seen):
    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(
            200,
            json={
                "candidates": [{"content": {"parts": [{"text": '{"ok": true}'}]}}],
                "usageMetadata": {"promptTokenCount": 5, "candidatesTokenCount": 3},
            },
        )

    return Gemini("test-key", client=httpx.Client(transport=httpx.MockTransport(handler)))


def test_schema_is_sent_as_response_json_schema():
    seen: list[httpx.Request] = []
    store = Store(":memory:")
    budget = Budget("g", Limits(60, 10**6, 100, 10**7), store)
    llm = LLM([Endpoint(_gemini(seen), "m", budget)], store)
    c = llm.complete([{"role": "user", "content": "hi"}], schema=SCHEMA)
    body = seen[0].read().decode()
    assert '"responseJsonSchema"' in body and '"application/json"' in body
    assert c.text == '{"ok": true}'


def test_schema_changes_the_cache_key():
    seen: list[httpx.Request] = []
    store = Store(":memory:")
    budget = Budget("g", Limits(60, 10**6, 100, 10**7), store)
    llm = LLM([Endpoint(_gemini(seen), "m", budget)], store)
    msgs = [{"role": "user", "content": "hi"}]
    llm.complete(msgs, schema=SCHEMA)
    llm.complete(msgs, schema=SCHEMA)  # cached
    llm.complete(msgs, schema={**SCHEMA, "required": []})  # different schema: a new call
    assert len(seen) == 2


def test_offline_router_never_calls():
    from backroom.settings import CacheMiss, build_llm

    store = Store(":memory:")
    llm = build_llm({"GEMINI_MODEL": "m"}, store, offline=True)
    msgs = [{"role": "user", "content": "x"}]
    try:
        llm.complete(msgs)
    except CacheMiss:
        pass
    else:
        raise AssertionError("offline router answered without a recording")
    assert store.daily("gemini/m", "2026-10-27") == (0, 0)  # the budget was never touched


def test_offline_router_replays_a_recording():
    from backroom.llm.store import Completion, cache_key
    from backroom.settings import build_llm

    store = Store(":memory:")
    llm = build_llm({"GEMINI_MODEL": "m"}, store, offline=True)
    msgs = [{"role": "user", "content": "x"}]
    key = cache_key("gemini", "m", llm._payload(msgs, 512, 0.0, False, None))
    store.put(key, Completion("recorded", 1, 1, "m"))
    assert llm.complete(msgs).text == "recorded"
