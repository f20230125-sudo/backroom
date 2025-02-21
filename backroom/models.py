"""Shared typed records; Decimal values preserve the amounts printed by source systems."""

from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ZERO = Decimal("0")


def money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Address(Record):
    street: str
    emirate: str
    country: str = "AE"


class Outlet(Record):
    id: str
    name: str
    address: Address
    trading: bool


class SupplierStationery(Record):
    """Synthetic source clutter stays outside the invoice extraction schema."""

    invoice_format: str
    credit_note_format: str
    invoice_sequence_start: int
    credit_note_sequence_start: int
    description_style: Literal["sku", "plain", "pack"]
    item_code_prefix: str
    show_item_codes: bool
    phone: str
    email: str
    bank_name: str
    iban: str
    show_contact: bool
    show_bank: bool


class Supplier(Record):
    id: str
    legal_name: str
    trade_name: str
    address: Address
    trn: str
    payment_terms_days: int
    category: str
    stationery: SupplierStationery


class Item(Record):
    id: str
    name: str
    category: str
    unit: Literal["KGM", "LTR", "H87", "BX", "CS"]
    price_min: Decimal
    price_max: Decimal
    vat_rate: Decimal = Decimal("5")


class Platform(Record):
    id: Literal["A", "B", "C"]
    name: str
    commission_rate: Decimal
    commission_vat_rate: Decimal = Decimal("0.05")


class Account(Record):
    code: str
    name: str


class POSRow(Record):
    business_date: date
    outlet_code: str
    channel: Literal["DINE_IN", "PLATFORM_A", "PLATFORM_B", "PLATFORM_C"]
    gross_sales: Decimal
    discounts: Decimal
    refunds: Decimal
    voids: Decimal
    net_sales: Decimal
    vat_included: Decimal
    covers_or_orders: int


class PayoutLine(Record):
    id: str | None = None
    business_date: date | None = None
    gross: Decimal | None = None
    commission: Decimal | None = None
    vat_on_commission: Decimal | None = None
    refunds: Decimal | None = None
    cofunded_promo: Decimal | None = None
    adjustments: Decimal | None = None


class PayoutStatement(Record):
    id: str
    platform: Literal["A", "B", "C"]
    outlet_code: str
    outlet_name: str
    week_start: date
    week_end: date
    paid_date: date
    commission_rate: Decimal
    lines: list[PayoutLine]
    net_payout: Decimal


class InvoiceLine(Record):
    description: str | None = None
    quantity: Decimal | None = None
    unit: str | None = None
    unit_price: Decimal | None = None
    vat_rate: Decimal | None = None
    line_net: Decimal | None = None


class InvoiceRecord(Record):
    supplier_name: str | None = None
    supplier_trn: str | None = None
    supplier_address: str | None = None
    supplier_address_emirate: str | None = None
    buyer_name: str | None = None
    buyer_trn: str | None = None
    invoice_number: str | None = None
    issue_date: date | None = None
    due_date: date | None = None
    currency: str | None = None
    deliver_to: str | None = None
    is_credit_note: bool | None = None
    original_invoice_number: str | None = None
    lines: list[InvoiceLine] = Field(default_factory=list)
    net: Decimal | None = None
    vat: Decimal | None = None
    total: Decimal | None = None
    quotes: dict[str, str] = Field(default_factory=dict)
    suspicious_instruction: bool = False
    notes: str | None = None


class LedgerLine(Record):
    date: date
    account: str
    contact: str
    reference: str
    description: str
    debit: Decimal
    credit: Decimal
    source: Literal["BANK", "BILL", "SALES"]


class SourceRef(Record):
    path: str
    row: int | None = None  # CSV data rows are 1-based, excluding the header.
    doc_id: str | None = None
    record_id: str | None = None


class Problem(Record):
    id: str
    kind: str
    refs: dict[str, SourceRef]
    detail: str
    expected_exception_kind: str


class SecurityPlant(Record):
    doc_id: str
    path: str
    text: str


class NameVariant(Record):
    entity_type: Literal["outlet", "supplier", "item"]
    canonical_id: str
    variant: str
    first_seen_doc: str


class HardNegative(Record):
    entity_type: str
    left_id: str
    left: str
    right_id: str
    right: str


class Document(Record):
    doc_id: str
    path: str
    kind: Literal[
        "invoice",
        "credit_note",
        "payout_statement",
        "email_invoice_body",
        "email_other",
        "email_with_attachment",
    ]
    layout: Literal["L1", "L2", "L3", "L4", "E1", "E2", "CSV_A", "CSV_B", "PDF_C"]
    held_out: bool
    supplier_id: str | None = None
    attachment_doc_id: str | None = None
    line_item_ids: list[str] = Field(default_factory=list)
    truth: InvoiceRecord | PayoutStatement | dict[str, str]


class Feed(Record):
    path: str
    kind: Literal["pos", "ledger"]
    rows: int


class GroundTruth(Record):
    seed: int
    date_from: date
    date_to: date
    timezone: str = "Asia/Dubai"
    notes: list[str]
    outlets: list[Outlet]
    suppliers: list[Supplier]
    items: list[Item]
    platforms: list[Platform]
    accounts: list[Account]
    documents: list[Document]
    feeds: list[Feed]
    problems: list[Problem]
    security_plants: list[SecurityPlant]
    name_variants: list[NameVariant]
    hard_negatives: list[HardNegative]
