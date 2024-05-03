"""References carry stable source identities into both storage and evaluation."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from backroom.models import InvoiceRecord, Record, money
from backroom.names.normalise import normalise


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

    @property
    def supplier_key(self) -> str | None:
        """Unknown printed names can group documents without inventing master IDs."""
        if self.supplier_id:
            return self.supplier_id
        name = normalise(self.record.supplier_name or "")
        return "printed:" + name if name else None


# Messages are read by people: show outlet names, keep the codes in refs and in the outlet field.
OUTLET_NAMES = {"OUT-JBR": "JBR Walk", "OUT-DIFC": "DIFC", "OUT-AQK": "Central Kitchen (Al Quoz)"}


def outlet_name(code: str | None) -> str:
    return OUTLET_NAMES.get(code or "", code or "")


def aed(value: Decimal) -> str:
    return f"AED {money(value):,.2f}"


def week_label(start: date, end: date) -> str:
    span = (
        f"{start.day}-{end.day} {end:%b}"
        if (start.year, start.month) == (end.year, end.month)
        else f"{start.day} {start:%b}-{end.day} {end:%b}"
    )
    return f"week {start.isocalendar().week} ({span})"
