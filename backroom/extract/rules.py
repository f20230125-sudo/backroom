"""Comparison baseline written only against L1-L3 and E1 labels and rows."""

import re
from decimal import Decimal

from backroom.extract.text import DocumentText
from backroom.extract.validate import suspicious
from backroom.models import InvoiceLine, InvoiceRecord

LABELS = {
    "supplier_name": ["Supplier", "Vendor", "Seller name"],
    "supplier_trn": ["Supplier TRN", "Tax registration", "Seller tax identifier"],
    "supplier_address": ["Address", "Registered office", "Seller location", "Supplier address"],
    "supplier_address_emirate": ["Emirate", "Emirate code", "Seller region", "Supplier emirate"],
    "buyer_name": ["Buyer", "Billed to", "Customer name"],
    "buyer_trn": ["Buyer TRN", "Customer TRN", "Customer tax identifier"],
    "invoice_number": ["Invoice number", "Document reference", "Bill identifier", "Invoice"],
    "issue_date": ["Issue date", "Document date", "Bill issued", "Issued"],
    "due_date": ["Due date", "Payment deadline", "Settlement date", "Due"],
    "currency": ["Currency", "Denomination", "Money code"],
    "deliver_to": ["Deliver to", "Delivery site", "Goods destination"],
    "original_invoice_number": ["Original invoice", "Credit against", "Prior bill identifier"],
    "net": ["Net", "Subtotal", "Taxable value"],
    "vat": ["VAT", "Tax amount", "Value added tax"],
    "total": ["Total", "Balance due", "Bill value"],
}
NUMBER = r"[-+]?\d[\d,]*(?:\.\d+)?"
TABLE = re.compile(
    rf"^(?P<description>.+?)\s+(?P<quantity>{NUMBER})\s+(?P<unit>KGM|LTR|H87|BX|CS)\s+"
    rf"(?P<unit_price>{NUMBER})\s+(?P<vat_rate>{NUMBER})\s+(?P<line_net>{NUMBER})$"
)
EMAIL = re.compile(
    rf"^(?P<description>.+?); quantity (?P<quantity>{NUMBER}) (?P<unit>\w+); "
    rf"unit price (?P<unit_price>{NUMBER}); VAT (?P<vat_rate>{NUMBER})%; "
    rf"line net (?P<line_net>{NUMBER})$"
)


def extract(source: DocumentText | str) -> InvoiceRecord:
    text = source.text if isinstance(source, DocumentText) else source
    values = {}
    quotes = {}
    lines = []
    for raw in text.splitlines():
        # Remove Arabic presentation labels, retaining the English data half.
        row = re.sub(r"[\u0600-\u06ff\ufb50-\ufdff\ufe70-\ufeff]+", "", raw).strip()
        for field, labels in LABELS.items():
            for label in labels:
                match = re.fullmatch(re.escape(label) + r":\s*(.*)", row, re.I)
                if match and field not in values:
                    value = match.group(1).strip()
                    if field in {"net", "vat", "total"}:
                        value = Decimal(value.removeprefix("AED ").replace(",", ""))
                    values[field] = value or None
                    quotes[field] = raw
        match = EMAIL.fullmatch(row) or TABLE.fullmatch(row)
        if match:
            data = match.groupdict()
            data["description"] = re.sub(r"^[A-Z]{2,5}-\d{3}\s+", "", data["description"])
            for key in ("quantity", "unit_price", "vat_rate", "line_net"):
                data[key] = Decimal(data[key].replace(",", ""))
            index = len(lines)
            lines.append(InvoiceLine(**data))
            quotes.update({f"lines.{index}.{key}": raw for key in data})
    credit = bool(re.search(r"\bCredit Note\b", text, re.I))
    title = re.search(r"\bCredit Note\b|\bTax Invoice\b", text, re.I)
    if title:
        quotes["is_credit_note"] = title.group()
    return InvoiceRecord(
        **values,
        lines=lines,
        quotes=quotes,
        is_credit_note=credit,
        suspicious_instruction=suspicious(text),
    )
