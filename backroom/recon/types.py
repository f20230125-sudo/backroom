"""References carry stable source identities into both storage and evaluation."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from backroom.models import InvoiceRecord, Record, money


class ExceptionRecord(Record):
    kind: str
    outlet: str | None
    date_or_week: str | None
    refs: dict[str, str]
    amount: Decimal
    message: str


@dataclass(frozen=True)
class Invoice:
    doc_id: str
    record: InvoiceRecord
    supplier_id: str | None
    outlet: str | None = None


def aed(value: Decimal) -> str:
    return f"AED {money(value):,.2f}"


def week_label(start: date, end: date) -> str:
    span = (
        f"{start.day}-{end.day} {end:%b}"
        if (start.year, start.month) == (end.year, end.month)
        else f"{start.day} {start:%b}-{end.day} {end:%b}"
    )
    return f"week {start.isocalendar().week} ({span})"
