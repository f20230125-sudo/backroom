"""Ask correctness and transparent time estimates; cache misses never count as refusals."""

import json
import re
import sqlite3
from collections import Counter
from decimal import Decimal

from backroom import ROOT
from backroom.ask.agent import Citation, ask, ask_llm
from backroom.ask.gold import build_gold
from backroom.ask.numbers import check_answer, matches, numbers
from backroom.db.repo import Repo

NONE_WORDS = re.compile(r"\b(no|none|zero|nothing|not any)\b", re.IGNORECASE)


def score_ask(case, result):
    if result["not_run"]:
        return "not run"
    if not case["in_scope"]:
        return "refused" if result["refused"] else "wrong"
    if result["refused"]:
        return "refused"
    citations = [Citation.model_validate(c) for c in result["numbers_used"]]
    valid = check_answer(
        result["answer"], citations, result["tool_calls"], result.get("question", "")
    )
    expected = [Decimal(n) for n in case["expected_numbers"]]
    displayed = numbers(result["answer"])
    says_none = bool(NONE_WORDS.search(result["answer"]))
    if not expected:
        # "No rows" questions (no purchases recorded for that outlet): correct only when the
        # answer says so and introduces no unsupported number.
        return "correct" if valid and says_none else "wrong"
    # All required SQL numbers must be displayed, not merely listed in citations; a zero may
    # be stated in words ("no payout-short exceptions").
    shown = all(matches(n, displayed) or (n == 0 and says_none) for n in expected)
    return "correct" if valid and shown else "wrong"


def evaluate_ask(repo, *, llm=None):
    owned = llm is None
    llm = llm if llm is not None else ask_llm()
    rows = []
    try:
        for case in build_gold(repo, output=None)["questions"]:
            result = ask(repo, case["question"], llm=llm)
            rows.append(
                {
                    "id": case["id"],
                    "question": case["question"],
                    "in_scope": case["in_scope"],
                    "expected_numbers": case["expected_numbers"],
                    "state": score_ask(case, result),
                    "result": result,
                }
            )
    finally:
        if owned:
            llm.store.close()
    summary = {}
    for scope, name, total in [(True, "in_scope", 30), (False, "out_of_scope", 5)]:
        counts = Counter(r["state"] for r in rows if r["in_scope"] == scope)
        summary[name] = {
            "total": total,
            **{key: counts[key] for key in ("correct", "refused", "wrong", "not run")},
        }
    return {**summary, "questions": rows}


def hours_saved(repo, path=ROOT / "eval/assumptions.json"):
    assumptions = json.loads(path.read_text(encoding="utf-8"))
    rates = {}
    for row in assumptions:
        value = Decimal(str(row["minutes"]))
        if not value.is_finite() or value < 0 or not row["basis"]:
            raise ValueError("Invalid time assumption")
        rates[row["name"]] = value
    counts = {
        "auto_accepted_document": repo.rows(
            "SELECT COUNT(*) AS n FROM extractions WHERE status = ? AND not_run = ?",
            ("accepted", 0),
        )[0]["n"],
        "review_document_overhead": repo.rows(
            "SELECT COUNT(DISTINCT document_id) AS n FROM review_queue WHERE status = ?", ("open",)
        )[0]["n"],
        "outlet_day_reconciled": len(
            repo.rows("SELECT DISTINCT outlet, business_date FROM pos_days")
        ),
        "draft_produced": repo.rows("SELECT COUNT(*) AS n FROM drafts")[0]["n"],
    }
    runs = [
        r
        for r in repo.rows("SELECT * FROM run_log WHERE days = ? ORDER BY id DESC", (92,))
        if json.loads(r["payload"]).get("status") == "completed"
    ]
    duration = runs[0]["duration_ms"] if runs else None
    manual = sum(
        (
            Decimal(count) * rates[key] * (-1 if key == "review_document_overhead" else 1)
            for key, count in counts.items()
        ),
        Decimal(0),
    )
    estimated = (manual - Decimal(duration) / 60000) / 60 if duration is not None else None
    return {
        "label": "estimate using assumptions, not a measured labour study",
        "counts": counts,
        "assumptions": assumptions,
        "formula": "(auto_accepted_document*minutes - review_document_overhead*minutes + "
        "outlet_day_reconciled*minutes + draft_produced*minutes - duration_ms/60000)/60",
        "manual_minutes_saved": str(manual),
        "estimated_hours_saved": str(estimated.quantize(Decimal("0.01")))
        if estimated is not None
        else None,
        "full_pipeline_duration_ms": duration,
        "duration_state": "measured" if runs else "not run",
        "duration_run_id": runs[0]["id"] if runs else None,
        "run_coverage": json.loads(runs[0]["payload"]) if runs else None,
    }


def evaluate_m4(*, repo=None, database=ROOT / ".local/backroom.sqlite"):
    """Read an existing run so repeated evals reuse its measured duration verbatim."""
    owned = repo is None
    if owned:
        if not database.exists():
            return {
                key: {"state": "not run", "reason": "Run the M4 pipeline first"}
                for key in ("ask", "hours_saved")
            }
        repo = Repo(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True))
    try:
        migrations = repo.rows(
            "SELECT version FROM schema_migrations WHERE version = ?", ("002_ask.sql",)
        )
        if not migrations:
            return {
                key: {"state": "not run", "reason": "Run the M4 pipeline first"}
                for key in ("ask", "hours_saved")
            }
        return {"ask": evaluate_ask(repo), "hours_saved": hours_saved(repo)}
    finally:
        if owned:
            repo.close()
