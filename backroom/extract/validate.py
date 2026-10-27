"""Pure evidence and arithmetic checks; only supported facts can explain source errors."""

import re
import unicodedata
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel

from backroom.extract.types import Issue, PayoutRecord
from backroom.models import ZERO, InvoiceRecord, PayoutStatement, money

TOLERANCE = Decimal("0.01")
TRN = re.compile(r"^1\d{12}03$", re.ASCII)
ANNOTATIONS = {"quotes", "notes", "suspicious_instruction"}
INSTRUCTION = re.compile(
    r"ignore\s+(?:all\s+)?(?:previous|prior|above)\s+instructions|"
    r"\bsystem\s*:|\bai assistant\b|\bforward\s+all\s+invoices\b|"
    r"\bset\s+(?:the\s+)?total\s+to\b|\bmark\s+this\s+invoice\s+as\s+paid\b",
    re.I,
)


def normalise(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).casefold().replace(",", "").split())


def suspicious(text: str) -> bool:
    return bool(INSTRUCTION.search(text))


def scalar_fields(record: BaseModel, prefix: str = "") -> dict:
    fields = {}
    for key in type(record).model_fields:
        if key in ANNOTATIONS:
            continue
        value = getattr(record, key)
        name = f"{prefix}{key}"
        if isinstance(value, list):
            for index, line in enumerate(value):
                fields.update(scalar_fields(line, f"{name}.{index}."))
        else:
            fields[name] = value
    return fields


def quote_supports(value, quote: str, field: str = "") -> bool:
    """Use token equality, not substrings (0 is not supported by a quote of 100)."""
    text = normalise(quote)
    if isinstance(value, bool):
        if field == "is_credit_note":
            return (
                bool(re.search(r"\bcredit note\b", text))
                if value
                else (bool(re.search(r"\b(?:tax )?invoice\b", text)) and "credit note" not in text)
            )
        return str(value).lower() in text
    if isinstance(value, Decimal):
        for match in re.finditer(r"(?<![\w.])[-+]?\d+(?:\.\d+)?(?![\w.])", text):
            number = Decimal(match.group())
            if value == number:
                return True
            if field == "commission_rate" and text[match.end() :].lstrip().startswith("%"):
                if value == number / Decimal("100"):
                    return True
        return False
    if isinstance(value, date):
        patterns = [
            (r"\d{4}-\d{2}-\d{2}", "%Y-%m-%d"),
            (r"\d{2}/\d{2}/\d{4}", "%d/%m/%Y"),
            (r"\d{1,2} [a-z]+ \d{4}", "%d %b %Y"),
            (r"\d{1,2} [a-z]+ \d{4}", "%d %B %Y"),
        ]
        for pattern, fmt in patterns:
            for candidate in re.findall(pattern, text):
                try:
                    if datetime.strptime(candidate, fmt).date() == value:
                        return True
                except ValueError:
                    continue
        return False
    return bool(re.search(r"(?<!\w)" + re.escape(normalise(str(value))) + r"(?!\w)", text))


def evidence_issues(record: InvoiceRecord | PayoutRecord, text: str) -> list[Issue]:
    issues = []
    for field, value in scalar_fields(record).items():
        if value is None:
            optional = field == "original_invoice_number" and not getattr(
                record, "is_credit_note", False
            )
            if not optional:
                issues.append(
                    Issue(code="MISSING_FIELD", field=field, message="Source fact is missing")
                )
            continue
        quote = record.quotes.get(field)
        if not quote or not normalise(quote):
            issues.append(Issue(code="QUOTE_MISSING", field=field, message="No source quote"))
        elif normalise(quote) not in normalise(text):
            issues.append(
                Issue(code="QUOTE_NOT_FOUND", field=field, message="Quote is absent from source")
            )
        elif suspicious(quote):
            issues.append(
                Issue(
                    code="QUOTE_UNSAFE",
                    field=field,
                    message="Instruction-like text cannot substantiate a business fact",
                )
            )
        elif not quote_supports(value, quote, field):
            issues.append(
                Issue(code="QUOTE_DISAGREES", field=field, message="Quote does not support value")
            )
    if not record.lines:
        issues.append(
            Issue(code="MISSING_LINES", field="lines", message="No source lines extracted")
        )
    if suspicious(text) and not record.suspicious_instruction:
        issues.append(
            Issue(
                code="INSTRUCTION_NOT_FLAGGED",
                field="suspicious_instruction",
                message="Instruction-like source text was not flagged",
            )
        )
    if record.notes and normalise(record.notes) not in normalise(text):
        issues.append(
            Issue(code="UNSUPPORTED_NOTES", field="notes", message="Notes are not source text")
        )
    return issues


def invoice_arithmetic(record: InvoiceRecord) -> list[Issue]:
    """No evidence dependency: usable with ground-truth values in the milestone gate."""
    issues = []

    def add(code, field, message):
        issues.append(Issue(code=code, field=field, message=message))

    if record.net is not None:
        complete_lines = record.lines and all(line.line_net is not None for line in record.lines)
        line_sum = sum((line.line_net for line in record.lines if line.line_net is not None), ZERO)
        if complete_lines and abs(line_sum - record.net) > TOLERANCE:
            add("LINES_NOT_SUM", "net", f"Line nets sum to {line_sum}, stated net is {record.net}")
        if record.vat is not None:
            rate = (
                ZERO
                if record.lines and all(line.vat_rate == ZERO for line in record.lines)
                else Decimal("0.05")
            )
            expected = money(record.net * rate)
            if abs(record.vat - expected) > TOLERANCE:
                add("VAT_WRONG", "vat", f"VAT is {record.vat}, expected {expected}")
        if record.vat is not None and record.total is not None:
            if abs(record.total - record.net - record.vat) > TOLERANCE:
                add(
                    "TOTAL_WRONG",
                    "total",
                    f"Total {record.total} differs from net plus VAT {record.net + record.vat}",
                )
    for field in ("supplier_trn", "buyer_trn"):
        value = getattr(record, field)
        if value is not None and not TRN.fullmatch(value):
            add("TRN_INVALID", field, "TRN must be 15 digits starting with 1 and ending in 03")
    if record.issue_date and record.due_date and record.due_date < record.issue_date:
        add("DUE_BEFORE_ISSUE", "due_date", "Due date precedes issue date")
    return issues


def payout_arithmetic(record: PayoutStatement) -> list[Issue]:
    # Missing inputs stay null and are reported by evidence_issues, never treated as zero.
    if any(value is None for value in scalar_fields(record).values()):
        return []
    expected = sum(
        (
            line.gross
            - line.commission
            - line.vat_on_commission
            - line.refunds
            + line.cofunded_promo
            + line.adjustments
            for line in record.lines
        ),
        ZERO,
    )
    issues = []
    if abs(expected - record.net_payout) > TOLERANCE:
        issues.append(
            Issue(
                code="PAYOUT_NET_WRONG",
                field="net_payout",
                message=f"Net payout {record.net_payout}, expected {expected}",
            )
        )
    if record.week_end < record.week_start or record.paid_date < record.week_end:
        issues.append(
            Issue(code="PAYOUT_DATES", field="week_end", message="Invalid settlement dates")
        )
    for index, line in enumerate(record.lines):
        if not record.week_start <= line.business_date <= record.week_end:
            issues.append(
                Issue(
                    code="PAYOUT_DATES",
                    field=f"lines.{index}.business_date",
                    message="Service day outside settlement period",
                )
            )
    return issues


def validate(record: InvoiceRecord | PayoutRecord, text: str) -> list[Issue]:
    issues = evidence_issues(record, text)
    bad = {issue.field for issue in issues}
    arithmetic = (
        invoice_arithmetic(record)
        if isinstance(record, InvoiceRecord)
        else payout_arithmetic(record)
    )
    for issue in arithmetic:
        dependencies = {
            "VAT_WRONG": {"net", "vat"} | {f"lines.{i}.vat_rate" for i in range(len(record.lines))},
            "LINES_NOT_SUM": {"net"} | {f"lines.{i}.line_net" for i in range(len(record.lines))},
            "TOTAL_WRONG": {"net", "vat", "total"},
            "TRN_INVALID": {issue.field},
            "DUE_BEFORE_ISSUE": {"issue_date", "due_date"},
        }.get(issue.code, set(scalar_fields(record)))
        issue.document_problem = not bool(dependencies & bad)
        issues.append(issue)
    return issues
