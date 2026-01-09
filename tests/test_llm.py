import httpx
import pytest

from backroom.llm import (
    LLM,
    AllEndpointsExhausted,
    Budget,
    CallTooLarge,
    Completion,
    DailyLimitReached,
    Endpoint,
    Gemini,
    Groq,
    Limits,
    ProviderError,
    RateLimited,
    Store,
)

MSG = [{"role": "user", "content": "hello"}]


class Clock:
    def __init__(self):
        self.t = 1_000_000.0
        self.slept: list[float] = []

    def now(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.slept.append(s)
        self.t += s


class Fake:
    """A provider that answers from a script: a string is a reply, an exception is raised."""

    def __init__(self, name: str, script: list, tokens: tuple[int, int] = (10, 5)):
        self.name, self.script, self.tokens, self.calls = name, list(script), tokens, 0

    def chat(self, model, messages, *, max_tokens, temperature, json_mode, schema=None):
        self.calls += 1
        step = self.script.pop(0) if self.script else "ok"
        if isinstance(step, Exception):
            raise step
        return Completion(step, self.tokens[0], self.tokens[1], model)


ROOMY = Limits(30, 8000, 1000, 200_000)


def make(clock, store, provider, model="m", limits=ROOMY, cap=None, name=None):
    b = Budget(
        name or f"{provider.name}/{model}",
        limits,
        store,
        max_call_tokens=cap,
        clock=clock.now,
        sleep=clock.sleep,
    )
    return Endpoint(provider, model, b)


def test_a_repeat_call_is_answered_from_the_cache_and_spends_nothing():
    clock, store = Clock(), Store(":memory:")
    p = Fake("a", ["first"])
    llm = LLM([make(clock, store, p)], store, sleep=clock.sleep)
    one = llm.complete(MSG)
    two = llm.complete(MSG)
    assert (one.text, one.cached) == ("first", False)
    assert (two.text, two.cached) == ("first", True)
    assert p.calls == 1
    assert store.daily("a/m", "1970-01-12")[0] >= 0  # the day key exists without error


def test_cache_hit_on_a_later_endpoint_is_used_before_any_call_is_made():
    clock, store = Clock(), Store(":memory:")
    a, b = Fake("a", []), Fake("b", ["from b"])
    both = [make(clock, store, a), make(clock, store, b)]
    LLM([both[1]], store, sleep=clock.sleep).complete(MSG)  # only b has answered this before
    again = LLM(both, store, sleep=clock.sleep).complete(MSG)
    assert again.cached and again.text == "from b"
    assert a.calls == 0 and b.calls == 1


def test_per_minute_request_limit_waits_instead_of_failing():
    clock, store = Clock(), Store(":memory:")
    p = Fake("a", [])
    llm = LLM(
        [make(clock, store, p, limits=Limits(2, 100_000, 1000, 10**9))], store, sleep=clock.sleep
    )
    for i in range(3):
        llm.complete([{"role": "user", "content": f"q{i}"}])
    assert p.calls == 3
    assert sum(clock.slept) >= 59  # the third call waited for the first to leave the window


def test_per_minute_token_limit_waits():
    clock, store = Clock(), Store(":memory:")
    p = Fake("a", [], tokens=(50, 50))
    lim = Limits(
        100, 150, 1000, 10**9
    )  # a call books about 100 tokens, so two cannot share a minute
    llm = LLM([make(clock, store, p, limits=lim)], store, sleep=clock.sleep)
    llm.complete([{"role": "user", "content": "one"}], max_tokens=90)
    llm.complete([{"role": "user", "content": "two"}], max_tokens=90)
    assert sum(clock.slept) >= 59


def test_daily_limit_fails_over_to_the_next_endpoint():
    clock, store = Clock(), Store(":memory:")
    a, b = Fake("a", []), Fake("b", ["from b"])
    lim = Limits(30, 8000, 1, 200_000)  # one request a day
    llm = LLM([make(clock, store, a, limits=lim), make(clock, store, b)], store, sleep=clock.sleep)
    llm.complete([{"role": "user", "content": "x1"}])  # spends a's only request
    out = llm.complete([{"role": "user", "content": "x2"}])
    assert out.text == "from b" and a.calls == 1 and b.calls == 1


def test_daily_limit_survives_a_restart_because_it_is_stored():
    clock, store = Clock(), Store(":memory:")
    lim = Limits(30, 8000, 1, 200_000)
    first = make(clock, store, Fake("a", []), limits=lim)
    first.budget.reserve(10)
    second = make(clock, store, Fake("a", []), limits=lim)  # a fresh process, same store
    with pytest.raises(DailyLimitReached):
        second.budget.reserve(10)


def test_the_day_rolls_over():
    clock, store = Clock(), Store(":memory:")
    ep = make(clock, store, Fake("a", []), limits=Limits(30, 8000, 1, 200_000))
    ep.budget.reserve(10)
    clock.t += 86_400
    ep.budget.reserve(10)  # a new day, a new allowance


def test_nothing_left_anywhere_raises_with_the_reasons():
    clock, store = Clock(), Store(":memory:")
    lim = Limits(30, 8000, 0, 200_000)
    llm = LLM([make(clock, store, Fake("a", []), limits=lim)], store, sleep=clock.sleep)
    with pytest.raises(AllEndpointsExhausted, match="day's limit"):
        llm.complete(MSG)


def test_rate_limited_responses_are_retried_after_the_stated_wait():
    clock, store = Clock(), Store(":memory:")
    p = Fake("a", [RateLimited(429, "slow down", 7.0), "worked"])
    llm = LLM([make(clock, store, p)], store, sleep=clock.sleep)
    assert llm.complete(MSG).text == "worked"
    assert 7.0 in clock.slept and p.calls == 2


def test_server_errors_retry_but_client_errors_do_not():
    clock, store = Clock(), Store(":memory:")
    p = Fake("a", [ProviderError(503, "down"), "back"])
    assert LLM([make(clock, store, p)], store, sleep=clock.sleep).complete(MSG).text == "back"
    bad = Fake("a", [ProviderError(400, "bad request")])
    with pytest.raises(ProviderError):
        LLM([make(clock, store, bad, model="m2")], store, sleep=clock.sleep).complete(MSG)
    assert bad.calls == 1


def test_a_call_over_the_cap_is_refused_before_it_is_sent():
    clock, store = Clock(), Store(":memory:")
    p = Fake("a", [])
    llm = LLM([make(clock, store, p, cap=100)], store, sleep=clock.sleep)
    with pytest.raises(AllEndpointsExhausted, match="per-call cap"):
        llm.complete([{"role": "user", "content": "x" * 3000}], max_tokens=50)
    assert p.calls == 0


def test_a_failed_call_does_not_keep_its_token_booking():
    clock, store = Clock(), Store(":memory:")
    p = Fake("a", [ProviderError(400, "no")])
    ep = make(clock, store, p)
    with pytest.raises(ProviderError):
        LLM([ep], store, sleep=clock.sleep).complete(MSG)
    assert store.daily("a/m", ep.budget.day_of(clock.now()))[1] == 0


def test_actual_usage_replaces_the_estimate_in_the_daily_count():
    clock, store = Clock(), Store(":memory:")
    ep = make(clock, store, Fake("a", [], tokens=(10, 5)))
    LLM([ep], store, sleep=clock.sleep).complete(MSG, max_tokens=500)
    assert store.daily("a/m", ep.budget.day_of(clock.now())) == (1, 15)


def test_calling_with_too_many_tokens_for_the_minute_can_never_succeed():
    clock, store = Clock(), Store(":memory:")
    b = Budget("x", Limits(30, 100, 1000, 10**6), store, clock=clock.now, sleep=clock.sleep)
    with pytest.raises(CallTooLarge):
        b.reserve(101)


# --- the real clients, against a mocked network ---------------------------------------------


def test_groq_request_and_response_shape():
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["auth"] = req.headers["authorization"]
        seen["body"] = req.read().decode()
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "hi"}}],
                "usage": {"prompt_tokens": 7, "completion_tokens": 2},
            },
        )

    g = Groq("secret", httpx.Client(transport=httpx.MockTransport(handler)))
    c = g.chat("openai/gpt-oss-120b", MSG, max_tokens=20, temperature=0.0, json_mode=True)
    assert (c.text, c.prompt_tokens, c.completion_tokens) == ("hi", 7, 2)
    assert seen["auth"] == "Bearer secret"
    assert '"response_format"' in seen["body"] and '"max_tokens":20' in seen["body"].replace(
        " ", ""
    )


def test_gemini_request_and_response_shape():
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["key"] = req.headers["x-goog-api-key"]
        seen["url"] = str(req.url)
        seen["body"] = req.read().decode()
        return httpx.Response(
            200,
            json={
                "candidates": [{"content": {"parts": [{"text": "a"}, {"text": "b"}]}}],
                "usageMetadata": {"promptTokenCount": 9, "candidatesTokenCount": 3},
            },
        )

    g = Gemini("secret", httpx.Client(transport=httpx.MockTransport(handler)))
    msgs = [{"role": "system", "content": "be brief"}, {"role": "user", "content": "q"}]
    c = g.chat("gemini-x", msgs, max_tokens=20, temperature=0.0, json_mode=False)
    assert (c.text, c.prompt_tokens, c.completion_tokens) == ("ab", 9, 3)
    assert seen["key"] == "secret" and seen["url"].endswith("/models/gemini-x:generateContent")
    assert (
        "systemInstruction" in seen["body"] and "key=" not in seen["url"]
    )  # the key is a header, never in the URL


def test_429_becomes_a_rate_limit_error_with_its_wait():
    h = lambda req: httpx.Response(429, headers={"retry-after": "12"}, text="quota")  # noqa: E731
    g = Groq("k", httpx.Client(transport=httpx.MockTransport(h)))
    with pytest.raises(RateLimited) as e:
        g.chat("m", MSG, max_tokens=5, temperature=0, json_mode=False)
    assert e.value.retry_after == 12.0


def test_gemini_embeddings_are_batched_by_100():
    sizes = []

    def handler(req: httpx.Request) -> httpx.Response:
        import json

        n = len(json.loads(req.read())["requests"])
        sizes.append(n)
        return httpx.Response(200, json={"embeddings": [{"values": [0.1, 0.2]}] * n})

    g = Gemini("k", httpx.Client(transport=httpx.MockTransport(handler)))
    out = g.embed("gemini-embedding-001", ["t"] * 250, task="RETRIEVAL_DOCUMENT")
    assert sizes == [100, 100, 50] and len(out) == 250


def test_a_cached_reply_keeps_the_latency_of_the_call_that_made_it():
    clock, store = Clock(), Store(":memory:")

    class Slow(Fake):
        def chat(self, *a, **kw):
            c = super().chat(*a, **kw)
            return Completion(
                c.text, c.prompt_tokens, c.completion_tokens, c.model, latency_ms=1234
            )

    llm = LLM([make(clock, store, Slow("a", ["x"]))], store, sleep=clock.sleep)
    fresh, again = llm.complete(MSG), llm.complete(MSG)
    assert (fresh.cached, fresh.latency_ms) == (False, 1234)
    assert (again.cached, again.latency_ms) == (True, 1234)
