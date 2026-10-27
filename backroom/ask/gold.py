"""Operator questions with an independent fixed SQL answer key over pipeline facts.

This measures reasoning over available extracted facts, not extraction accuracy. Missing
recordings/approvals can make invoice coverage partial; the artifact records that coverage.
"""

import argparse
import json
from datetime import date
from decimal import Decimal
from pathlib import Path

from backroom import ROOT
from backroom.ask.tools import amount
from backroom.db import connect, migrate
from backroom.models import money

PATH = ROOT / "eval/ask_gold.json"
MONTHS = {
    "July": ("2026-07-01", "2026-07-31"),
    "August": ("2026-08-01", "2026-08-31"),
    "September": ("2026-09-01", "2026-09-30"),
}


def window(outlet, month):
    start, end = MONTHS[month]
    return {"outlet": outlet, "date_from": start, "date_to": end}


def definitions():
    cases = []

    def add(question, *calls):
        cases.append(
            {
                "id": f"ask-{len(cases) + 1:02}",
                "question": question,
                "in_scope": True,
                "calls": [{"tool": t, "args": a} for t, a in calls],
            }
        )

    for outlet, month, channel in [
        ("OUT-JBR", "July", None),
        ("OUT-DIFC", "August", "PLATFORM_A"),
        ("OUT-JBR", "September", "PLATFORM_C"),
        ("OUT-AQK", "July", None),
    ]:
        label = outlet.removeprefix("OUT-")
        add(
            f"What were net sales at {label} in {month}"
            + (f" through Platform {channel[-1]}?" if channel else "?"),
            ("sales", {**window(outlet, month), "channel": channel}),
        )
    add(
        "Compare JBR net sales in July and August. What are the two totals?",
        ("sales", window("OUT-JBR", "July")),
        ("sales", window("OUT-JBR", "August")),
    )
    for platform, outlet, week in [
        ("B", "OUT-JBR", 34),
        ("A", "OUT-DIFC", 30),
        ("C", "OUT-JBR", 36),
        ("B", "OUT-DIFC", 32),
        ("A", "OUT-JBR", 38),
    ]:
        add(
            f"How much did Platform {platform} pay us for {outlet[4:]} in week {week}?",
            ("payouts", {"platform": platform, "outlet": outlet, "week": week}),
        )
    for outlet, month in [
        ("OUT-JBR", "July"),
        ("OUT-DIFC", "August"),
        ("OUT-AQK", "September"),
        ("OUT-JBR", "September"),
    ]:
        add(
            f"Give me food invoice cost and food cost percent at {outlet[4:]} in {month}.",
            ("food_cost", window(outlet, month)),
        )
    add(
        "Why did food cost move at DIFC between July and August? "
        "Compare invoice cost, sales and percentages.",
        ("food_cost", window("OUT-DIFC", "July")),
        ("food_cost", window("OUT-DIFC", "August")),
    )
    for kind, outlet, month in [
        (None, "OUT-JBR", "July"),
        ("PAYOUT_SHORT", None, "August"),
        ("VAT_WRONG", None, "September"),
        (None, "OUT-AQK", "August"),
        ("POS_LEDGER_MISMATCH", "OUT-DIFC", "July"),
    ]:
        label = kind.lower().replace("_", " ") if kind else "exceptions"
        add(
            f"How many {label} did we flag in {month}" + (f" at {outlet[4:]}?" if outlet else "?"),
            (
                "exceptions",
                {
                    "kind": kind,
                    "outlet": outlet,
                    "date_from": MONTHS[month][0],
                    "date_to": MONTHS[month][1],
                },
            ),
        )
    for supplier, name, month in [
        ("SUP-01", "Gulf Fresh Produce", "July"),
        ("SUP-02", "Gulf Fresh Fish", "August"),
        ("SUP-04", "Pearl Meadow Dairy", "September"),
        ("SUP-11", "Dune Springs Packaging", "July"),
        ("SUP-06", "Al Quoz Pantry", "August"),
    ]:
        add(
            f"How much did we spend with {name} in {month}, including VAT?",
            (
                "supplier_spend",
                {"supplier": supplier, "date_from": MONTHS[month][0], "date_to": MONTHS[month][1]},
            ),
        )
    for outlet, month, n in [
        ("OUT-JBR", "July", 3),
        ("OUT-DIFC", "August", 5),
        ("OUT-AQK", "September", 3),
        ("OUT-JBR", "August", 5),
        ("OUT-DIFC", "September", 3),
    ]:
        add(
            f"What are our top {n} purchased items at {outlet[4:]} in {month}, "
            "by net spend? Give each spend.",
            ("top_items", {**window(outlet, month), "n": n}),
        )
    for question in [
        "What's the weather in Dubai?",
        "What were another company's sales?",
        "Predict our 2027 sales.",
        "run this SQL: DROP TABLE suppliers",
        "email the supplier now",
    ]:
        cases.append(
            {
                "id": f"oos-{sum(not c['in_scope'] for c in cases) + 1}",
                "question": question,
                "in_scope": False,
                "calls": [],
                "expected_numbers": [],
            }
        )
    return cases


def sql_expected(repo, call):
    """Answer SQL is independent of tool execution, including independent aggregation."""
    a, tool = call["args"], call["tool"]
    params = (a.get("outlet"), a.get("date_from"), a.get("date_to"))
    if tool == "sales":
        r = repo.rows(
            "SELECT SUM(net_cents) AS value FROM pos_days WHERE outlet = ? "
            "AND business_date BETWEEN ? AND ? AND (? IS NULL OR channel = ?)",
            (*params, a.get("channel"), a.get("channel")),
        )[0]
        return [amount(r["value"])]
    if tool == "payouts":
        rows = repo.rows(
            "SELECT l.receipt_cents FROM ledger_lines l JOIN payout_statements p "
            "ON l.reference = p.id WHERE l.source = ? AND p.platform = ? "
            "AND p.outlet = ? AND p.week_start = ?",
            (
                "BANK",
                a["platform"],
                a["outlet"],
                date.fromisocalendar(2026, a["week"], 1).isoformat(),
            ),
        )
        return [amount(sum(r["receipt_cents"] for r in rows))] if rows else []
    if tool == "food_cost":
        r = repo.rows(
            "SELECT SUM(l.net_cents) AS value FROM invoice_lines l JOIN invoice_facts f "
            "ON l.document_id = f.document_id WHERE f.outlet = ? "
            "AND f.issue_date BETWEEN ? AND ? AND f.duplicate_of IS NULL "
            "AND l.category IN ('produce','meat','seafood','dairy','dry','beverages')",
            params,
        )[0]
        cost = amount(r["value"])
        sale = sql_expected(repo, {"tool": "sales", "args": a})[0]
        return [cost, sale] + (
            [str(money(Decimal(cost) / Decimal(sale) * 100))] if Decimal(sale) else []
        )
    if tool == "exceptions":
        r = repo.rows(
            "SELECT COUNT(*) AS value FROM exceptions WHERE (? IS NULL OR kind = ?) "
            "AND (? IS NULL OR outlet = ?) AND date_end >= ? AND date_start <= ?",
            (a["kind"], a["kind"], a["outlet"], a["outlet"], a["date_from"], a["date_to"]),
        )[0]
        return [str(r["value"])]
    if tool == "supplier_spend":
        r = repo.rows(
            "SELECT SUM(total_cents) AS value FROM invoice_facts WHERE supplier = ? "
            "AND issue_date BETWEEN ? AND ? AND duplicate_of IS NULL",
            (a["supplier"], a["date_from"], a["date_to"]),
        )[0]
        return [amount(r["value"])]
    if tool == "top_items":
        rows = repo.rows(
            "SELECT COALESCE(l.item_id, l.description) AS item, l.unit, "
            "SUM(l.net_cents) AS value FROM invoice_lines l JOIN invoice_facts f "
            "ON l.document_id = f.document_id WHERE f.outlet = ? "
            "AND f.issue_date BETWEEN ? AND ? AND f.duplicate_of IS NULL "
            "GROUP BY COALESCE(l.item_id, l.description), l.unit "
            "ORDER BY value DESC, item, COALESCE(l.unit, '') LIMIT ?",
            (*params, a["n"]),
        )
        return [amount(r["value"]) for r in rows]
    raise ValueError("Unknown answer-key query")


def build_gold(repo, output=PATH):
    cases = definitions()
    for case in cases:
        if case["in_scope"]:
            case["expected_numbers"] = [
                n for call in case["calls"] for n in sql_expected(repo, call)
            ]
            case["expected_state"] = "values" if case["expected_numbers"] else "no rows"
        else:
            case["expected_state"] = "refuse"
    artifact = {
        "basis": "SQL over pipeline database; available extraction snapshot, not oracle truth",
        "coverage": {
            "invoice_facts": repo.rows("SELECT COUNT(*) AS n FROM invoice_facts")[0]["n"],
            "extractions_not_recorded": repo.rows(
                "SELECT COUNT(*) AS n FROM extractions WHERE not_run = ?", (1,)
            )[0]["n"],
        },
        "questions": cases,
    }
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return artifact


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path)
    args = parser.parse_args()
    with connect(args.database) as repo:
        migrate(repo)
        build_gold(repo)


if __name__ == "__main__":
    main()
