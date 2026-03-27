"""Fast M4 contract checks with synthetic rows and a fake cache miss."""

import json
import re

from backroom import ROOT
from backroom.ask.gold import PATH, build_gold
from backroom.ask.numbers import grounded
from backroom.ask.tools import REGISTRY, Tools
from backroom.brief import generate_brief
from backroom.db import connect, migrate
from backroom.db.repo import dumps
from backroom.drafts import TEMPLATE, generate_drafts
from backroom.pipeline import load_masters
from backroom.settings import CacheMiss
from backroom.synth.catalogue import ITEMS, OUTLETS, SUPPLIERS


def no_send_capability():
    for path in (ROOT / "backroom").rglob("*.py"):
        if "llm" in path.relative_to(ROOT / "backroom").parts:
            continue
        # This is a source scan, including imports and qualified attribute calls.
        require(
            not re.search(
                r"\b" + "smtp" + r"lib\b|\brequests\s*\.\s*post\b|\bhttpx\s*\.\s*post\b",
                path.read_text(encoding="utf-8"),
            ),
            "Send capability: " + str(path),
        )


def require(condition, message):
    if not condition:
        raise ValueError(message)


class Missing:
    def complete(self, *a, **kw):
        raise CacheMiss("gate fake")


def checks():
    no_send_capability()
    print("PASS m4 package scan: no send capability outside model transport")
    require(
        set(REGISTRY)
        == {"sales", "payouts", "food_cost", "exceptions", "supplier_spend", "top_items"},
        "Registry changed",
    )
    try:
        REGISTRY["sql"] = (None, None)
    except TypeError:
        pass
    else:
        raise ValueError("Registry is mutable")
    with connect(":memory:") as repo:
        migrate(repo)
        load_masters(
            repo,
            {
                "outlets": [r.model_dump(mode="json") for r in OUTLETS],
                "suppliers": [r.model_dump(mode="json") for r in SUPPLIERS],
                "items": [r.model_dump(mode="json") for r in ITEMS],
            },
        )
        tools = Tools(repo)
        args = {"outlet": "OUT-JBR", "date_from": "2026-07-01", "date_to": "2026-09-30"}
        invalid = [
            ("sales", {**args, "outlet": "DROP TABLE outlets"}),
            ("sales", {**args, "date_from": "2027-07-01"}),
            ("sales", {**args, "date_to": "2026-06-30"}),
            ("sales", {**args, "sql": "anything"}),
            ("top_items", {**args, "n": 21}),
            (
                "supplier_spend",
                {"supplier": "unknown", "date_from": args["date_from"], "date_to": args["date_to"]},
            ),
            ("payouts", {"outlet": "OUT-JBR", "platform": "D", "week": 34}),
            ("sql", {}),
        ]
        for tool, bad in invalid:
            try:
                tools.call(tool, bad)
            except ValueError:
                pass
            else:
                raise ValueError("Tool accepted invalid arguments")
        print("PASS m4 closed registry and strict argument/name rejection")
        for text, facts, valid in [
            ("1,234.50 and two orders at 22%", ["1234.50", 2, 22], True),
            ("AED 10.01", [10], True),
            ("AED 10.02", [10], False),
            ("22%", ["0.22"], False),
            ("three", [2], False),
        ]:
            require(grounded(text, facts) == valid, "Number checker failed")
        print("PASS m4 number checker: money, tolerance, percentages, counts")
        generated = build_gold(repo, output=None)
        stored = json.loads(PATH.read_text(encoding="utf-8"))
        for artifact in (generated, stored):
            questions = artifact["questions"]
            require(
                sum(q["in_scope"] for q in questions) == 30 and len(questions) == 35,
                "Gold count mismatch",
            )
            require(all("expected_numbers" in q for q in questions), "Missing SQL answers")
        # Expected values depend on the extraction snapshot; definitions must remain identical.
        require(
            [(q["question"], q["calls"]) for q in generated["questions"]]
            == [(q["question"], q["calls"]) for q in stored["questions"]],
            "Gold questions stale",
        )
        print("PASS m4 gold: 30 SQL-backed questions and 5 out-of-scope")
        source = {
            "kind": "VAT_WRONG",
            "refs": {},
            "amount": "0.50",
            "message": "VAT difference AED 0.50.",
        }
        repo.upsert(
            "exceptions",
            {
                "id": "gate",
                "kind": source["kind"],
                "outlet": "OUT-JBR",
                "date_or_week": "2026-07-01",
                "refs": "{}",
                "amount": "0.50",
                "message": source["message"],
                "payload": dumps(source),
            },
        )
        drafts = generate_drafts(repo, llm=Missing())
        brief = generate_brief(repo, 1, llm=Missing())
        require(len(drafts) == 1 and drafts[0]["provenance"] == TEMPLATE, "Missing template draft")
        require(
            brief["provenance"] == TEMPLATE and len(brief["body"].split()) <= 120,
            "Missing short template brief",
        )
        print("PASS m4 offline CacheMiss stores marked draft and brief templates")
