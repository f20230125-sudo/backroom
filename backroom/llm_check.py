"""One small structured-output call to prove the key and model work.

Prints status only, never the key.

    python -m backroom.llm_check
"""

from __future__ import annotations

import json
import sys

from .llm import Store
from .settings import MissingKey, build_llm, model_name

SCHEMA = {
    "type": "object",
    "properties": {"ok": {"type": "boolean"}, "word": {"type": "string"}},
    "required": ["ok", "word"],
}


def main() -> int:
    try:
        # A check must really call the model, not the cache.
        llm = build_llm(store=Store(":memory:"))
    except MissingKey as e:
        print(f"no key: {e}")
        return 2
    msgs = [
        {"role": "system", "content": "Return JSON only."},
        {"role": "user", "content": 'Set ok to true and word to "backroom".'},
    ]
    try:
        c = llm.complete(msgs, max_tokens=64, schema=SCHEMA)
    except Exception as e:  # report the kind of failure without echoing request headers
        print(f"call failed: {type(e).__name__}: {str(e)[:300]}")
        return 1
    data = json.loads(c.text)
    print(
        f"model={model_name()} ok={data.get('ok')} word={data.get('word')} "
        f"latency_ms={c.latency_ms} tokens={c.prompt_tokens}+{c.completion_tokens}"
    )
    return 0 if data.get("ok") is True and data.get("word") == "backroom" else 1


if __name__ == "__main__":
    sys.exit(main())
