"""The model chooses from six fixed queries; arguments never become SQL text.

Food cost is received invoice food lines / POS net sales, not inventory consumption.
Top items ranks purchased invoice descriptions by net spend, not POS menu sales.
Duplicates are excluded and signed credit-note lines reduce spend.
"""

import json
import re
from datetime import date
from decimal import Decimal
from types import MappingProxyType
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backroom.models import money
from backroom.names.match import Matcher, approved_names, automatic_names, masters, rejected_names

FIRST, LAST = date(2026, 7, 1), date(2026, 9, 30)
KINDS = frozenset(
    {
        "PAYOUT_SHORT",
        "PAYOUT_OVER",
        "COMMISSION_OVER_CONTRACT",
        "REFUND_NOT_IN_POS",
        "DUPLICATE_INVOICE",
        "VAT_WRONG",
        "LINES_NOT_SUM",
        "TOTAL_WRONG",
        "TRN_INVALID",
        "TRN_SHARED",
        "CREDIT_NOTE_UNLINKED",
        "MISSING_FIELD",
        "POS_LEDGER_MISMATCH",
        "LEDGER_BILL_MISSING",
        "LEDGER_BILL_AMOUNT_MISMATCH",
        "PAYOUT_NET_MISMATCH",
        "COMMISSION_VAT_MISMATCH",
        "POS_DAY_MISSING",
        "PAYOUT_POS_GROSS_MISMATCH",
        "REFUND_MISMATCH",
    }
)


def iso_day(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError("Use an ISO date")
    parsed = date.fromisoformat(value)
    if not FIRST <= parsed <= LAST:
        raise ValueError("Date outside available quarter")
    return value


class Args(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    @model_validator(mode="after")
    def dates(self):
        start, end = getattr(self, "date_from", None), getattr(self, "date_to", None)
        for value in (start, end):
            if value is not None:
                iso_day(value)
        if start and end and start > end:
            raise ValueError("Reversed date range")
        return self


class Range(Args):
    date_from: str
    date_to: str


class OutletRange(Range):
    outlet: str


class Sales(OutletRange):
    channel: Literal["DINE_IN", "PLATFORM_A", "PLATFORM_B", "PLATFORM_C"] | None = None


class Payouts(Args):
    platform: str
    outlet: str
    week: int = Field(ge=27, le=40)


class Exceptions(Args):
    kind: str | None = None
    outlet: str | None = None
    date_from: str | None = None
    date_to: str | None = None


class Spend(Range):
    supplier: str


class TopItems(OutletRange):
    n: int = Field(ge=1, le=20)


def amount(cents):
    return str(money(Decimal(cents or 0) / 100))


SALES_SQL = """
SELECT business_date, channel, gross_cents, net_cents, orders FROM pos_days
WHERE outlet = ? AND business_date BETWEEN ? AND ? AND (CAST(? AS TEXT) IS NULL OR channel = ?)
ORDER BY business_date, channel
"""
PAYOUTS_SQL = """
SELECT p.id, p.week_start, p.week_end, p.paid_date, p.net_cents,
 (SELECT SUM(l.receipt_cents) FROM ledger_lines l WHERE l.source = 'BANK'
 AND l.reference = p.id) AS paid_cents
FROM payout_statements p WHERE p.platform = ? AND p.outlet = ?
AND p.week_start = ? ORDER BY p.id
"""
LINES_SQL = """
SELECT f.document_id, f.invoice_number, f.supplier_name, f.issue_date,
 l.description, l.item_id, l.category, l.unit, l.quantity, l.net_cents
FROM invoice_facts f JOIN invoice_lines l ON l.document_id = f.document_id
WHERE f.outlet = ? AND f.issue_date BETWEEN ? AND ? AND f.duplicate_of IS NULL
ORDER BY f.document_id, l.line_no
"""
SPEND_SQL = """
SELECT document_id, invoice_number, supplier_name, issue_date, total_cents
FROM invoice_facts WHERE supplier = ? AND issue_date BETWEEN ? AND ?
AND duplicate_of IS NULL ORDER BY issue_date, document_id
"""
# Pipeline projects ISO date spans; week labels are never compared lexically.
EXCEPTIONS_SQL = """SELECT * FROM exceptions
WHERE (CAST(? AS TEXT) IS NULL OR kind = ?) AND (CAST(? AS TEXT) IS NULL OR outlet = ?)
AND (CAST(? AS TEXT) IS NULL OR date_end >= ?)
AND (CAST(? AS TEXT) IS NULL OR date_start <= ?) ORDER BY id"""


def sales(repo, a: Sales):
    raw = repo.rows(SALES_SQL, (a.outlet, a.date_from, a.date_to, a.channel, a.channel))
    if any(r["net_cents"] is None for r in raw):
        raise ValueError("Run pipeline to project money columns")
    rows = [
        {
            "date": r["business_date"],
            "channel": r["channel"],
            "gross_sales": amount(r["gross_cents"]),
            "net_sales": amount(r["net_cents"]),
            "orders": r["orders"],
        }
        for r in raw
    ]
    return rows, {
        "net_sales": amount(sum(r["net_cents"] for r in raw)),
        "gross_sales": amount(sum(r["gross_cents"] for r in raw)),
        "orders": sum(r["orders"] for r in raw),
        "row_count": len(rows),
    }


def payouts(repo, a: Payouts):
    start = date.fromisocalendar(2026, a.week, 1).isoformat()
    raw = repo.rows(PAYOUTS_SQL, (a.platform, a.outlet, start))
    rows = [
        {
            "statement": r["id"],
            "week": a.week,
            "week_start": r["week_start"],
            "week_end": r["week_end"],
            "paid_date": r["paid_date"],
            "statement_net": amount(r["net_cents"]),
            "bank_paid": amount(r["paid_cents"]) if r["paid_cents"] is not None else None,
        }
        for r in raw
    ]
    paid = [r["paid_cents"] for r in raw if r["paid_cents"] is not None]
    return rows, {
        "statement_net": amount(sum(r["net_cents"] for r in raw)),
        "bank_paid": amount(sum(paid)) if len(paid) == len(raw) and raw else None,
        "row_count": len(rows),
    }


def food_cost(repo, a: OutletRange):
    raw = repo.rows(LINES_SQL, (a.outlet, a.date_from, a.date_to))
    food = [
        r
        for r in raw
        if r["category"] in {"produce", "meat", "seafood", "dairy", "dry", "beverages"}
    ]
    cost = sum(r["net_cents"] for r in food)
    _, sale = sales(repo, Sales(**a.model_dump()))
    net = Decimal(sale["net_sales"])
    pct = str(money(Decimal(cost) / net)) if net else None
    rows = [
        {**{k: v for k, v in r.items() if k != "net_cents"}, "line_net": amount(r["net_cents"])}
        for r in food
    ]
    return rows, {
        "food_cost": amount(cost),
        "net_sales": str(net),
        "food_cost_percent": pct,
        "line_count": len(rows),
        "unclassified_lines": sum(r["category"] is None for r in raw),
    }


def supplier_spend(repo, a: Spend):
    raw = repo.rows(SPEND_SQL, (a.supplier, a.date_from, a.date_to))
    rows = [
        {
            **{k: v for k, v in r.items() if k != "total_cents"},
            "total": amount(r["total_cents"]) if r["total_cents"] is not None else None,
        }
        for r in raw
    ]
    return rows, {
        "supplier_spend": amount(sum(r["total_cents"] or 0 for r in raw)),
        "invoice_count": len(rows),
        "missing_totals": sum(r["total_cents"] is None for r in raw),
    }


def top_items(repo, a: TopItems):
    raw = repo.rows(LINES_SQL, (a.outlet, a.date_from, a.date_to))
    groups = {}
    for r in raw:
        key = (r["item_id"] or r["description"], r["unit"])
        group = groups.setdefault(
            key,
            {
                "item": key[0],
                "unit": key[1],
                "net_cents": 0,
                "quantity": Decimal(0),
                "quantity_missing": False,
                "line_count": 0,
            },
        )
        group["net_cents"] += r["net_cents"]
        group["quantity"] += Decimal(r["quantity"] or "0")
        group["quantity_missing"] |= r["quantity"] is None
        group["line_count"] += 1
    selected = sorted(groups.values(), key=lambda r: (-r["net_cents"], r["item"], r["unit"] or ""))[
        : a.n
    ]
    # Show the catalogue name for matched items (not the "ITM-039" code); unmatched lines keep
    # the printed description.
    names = {r["id"]: r["name"] for r in repo.rows("SELECT id, name FROM items", ())}
    rows = [
        {
            "item": names.get(r["item"], r["item"]),
            "unit": r["unit"],
            "net_spend": amount(r["net_cents"]),
            "quantity": None if r["quantity_missing"] else str(r["quantity"]),
            "line_count": r["line_count"],
        }
        for r in selected
    ]
    return rows, {
        "net_spend": amount(sum(r["net_cents"] for r in selected)),
        "item_count": len(rows),
    }


def exceptions(repo, a: Exceptions):
    raw = repo.rows(
        EXCEPTIONS_SQL,
        (a.kind, a.kind, a.outlet, a.outlet, a.date_from, a.date_from, a.date_to, a.date_to),
    )
    rows = []
    for r in raw:
        refs = json.loads(r["refs"])
        rows.append(
            {k: v for k, v in r.items() if k not in {"payload", "amount_cents"}} | {"refs": refs}
        )
    return rows, {
        "exception_count": len(rows),
        "amount": str(money(sum((Decimal(r["amount"]) for r in rows), Decimal(0)))),
    }


REGISTRY = MappingProxyType(
    {
        "sales": (Sales, sales),
        "payouts": (Payouts, payouts),
        "food_cost": (OutletRange, food_cost),
        "exceptions": (Exceptions, exceptions),
        "supplier_spend": (Spend, supplier_spend),
        "top_items": (TopItems, top_items),
    }
)


class Tools:
    def __init__(self, repo):
        self.repo = repo
        source = {
            table: [json.loads(r["payload"]) for r in repo.rows(sql)]
            for table, sql in (
                ("outlets", "SELECT payload FROM outlets"),
                ("suppliers", "SELECT payload FROM suppliers"),
                ("items", "SELECT payload FROM items"),
            )
        }
        self.matcher = Matcher(
            masters(source),
            approved_names(repo),
            automatic=automatic_names(repo),
            rejected=rejected_names(repo),
        )
        self.sequence = 0

    def call(self, tool: str, args: dict):
        if tool not in REGISTRY:
            raise ValueError("Unknown tool")
        schema, function = REGISTRY[tool]
        a = schema.model_validate(args)
        for kind in ("outlet", "supplier"):
            value = getattr(a, kind, None)
            if value is not None:
                canonical = (
                    self.matcher.supplier_identity(value)
                    if kind == "supplier"
                    else self.matcher.resolve(kind, value)
                )
                if not canonical or canonical.startswith("printed:"):
                    raise ValueError("Unknown or unapproved " + kind)
                setattr(a, kind, canonical)
        if isinstance(a, Payouts):
            a.platform = {
                "A": "A",
                "B": "B",
                "C": "C",
                "Platform A": "A",
                "Platform B": "B",
                "Platform C": "C",
            }.get(a.platform, "")
            if not a.platform:
                raise ValueError("Unknown platform")
        if isinstance(a, Exceptions) and a.kind is not None and a.kind not in KINDS:
            raise ValueError("Unknown exception kind")
        rows, totals = function(self.repo, a)
        self.sequence += 1
        return {
            "tool_call_id": f"call_{self.sequence}",
            "tool": tool,
            "args": a.model_dump(),
            "rows": rows,
            "totals": totals,
        }

    def schemas(self):
        return {name: model.model_json_schema() for name, (model, _) in REGISTRY.items()}
