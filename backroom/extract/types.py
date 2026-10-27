"""Extraction metadata stays separate from the synthetic source records."""

from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import Field

from backroom.models import InvoiceRecord, PayoutStatement, Record


class PayoutRecord(PayoutStatement):
    id: str | None = None
    platform: Literal["A", "B", "C"] | None = None
    outlet_code: str | None = None
    outlet_name: str | None = None
    week_start: date | None = None
    week_end: date | None = None
    paid_date: date | None = None
    commission_rate: Decimal | None = None
    net_payout: Decimal | None = None
    quotes: dict[str, str] = Field(default_factory=dict)
    suspicious_instruction: bool = False
    notes: str | None = None


class Issue(Record):
    code: str
    field: str
    message: str
    document_problem: bool = False


class ExtractionResult(Record):
    record: InvoiceRecord | PayoutRecord | None = None
    issues: list[Issue] = Field(default_factory=list)
    status: Literal["accepted", "review", "document_problem"] = "review"
    prompt_version: str
    model: str | None = None
    cached: bool = False
    attempts: int = 0
    cache_hits: int = 0
    model_calls: int = 0
    not_run: bool = False
    classification: str | None = None
