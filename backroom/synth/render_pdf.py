"""Render invented stationery with invariant metadata; held-out output is never inspected."""

from datetime import date
from functools import lru_cache
from io import BytesIO
from pathlib import Path
from textwrap import wrap
from unicodedata import lookup, name

from reportlab.lib.pagesizes import A4, landscape
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas

from backroom.models import Document, InvoiceRecord, PayoutStatement
from backroom.synth.catalogue import SUPPLIERS

LABELS = {
    "L1": (
        "Supplier",
        "Supplier TRN",
        "Address",
        "Emirate",
        "Buyer",
        "Buyer TRN",
        "Invoice number",
        "Issue date",
        "Due date",
        "Currency",
        "Deliver to",
        "Original invoice",
        "Net",
        "VAT",
        "Total",
    ),
    "L2": (
        "Vendor",
        "Tax registration",
        "Registered office",
        "Emirate code",
        "Billed to",
        "Customer TRN",
        "Document reference",
        "Document date",
        "Payment deadline",
        "Denomination",
        "Delivery site",
        "Credit against",
        "Subtotal",
        "Tax amount",
        "Balance due",
    ),
    # English labels remain distinct even on machines without an Arabic font.
    "L3": (
        "Seller name",
        "Seller tax identifier",
        "Seller location",
        "Seller region",
        "Customer name",
        "Customer tax identifier",
        "Bill identifier",
        "Bill issued",
        "Settlement date",
        "Money code",
        "Goods destination",
        "Prior bill identifier",
        "Taxable value",
        "Value added tax",
        "Bill value",
    ),
    "L4": (
        "Supply undertaking",
        "TRN No.",
        "Place of registration",
        "Territory code",
        "Purchasing entity",
        "Purchaser registration No.",
        "Tax Invoice No.",
        "Raised on",
        "Remit before",
        "Tender code",
        "Consignment destination",
        "Related tax document",
        "Untaxed consideration",
        "Tax levied",
        "Amount Payable",
    ),
    "E1": (
        "Supplier",
        "Supplier TRN",
        "Supplier address",
        "Supplier emirate",
        "Buyer",
        "Buyer TRN",
        "Invoice",
        "Issued",
        "Due",
        "Currency",
        "Deliver to",
        "Original invoice",
        "Net",
        "VAT",
        "Total",
    ),
    "E2": (
        "our trading name",
        "our tax registration",
        "our address",
        "our emirate",
        "your legal entity",
        "your tax registration",
        "invoice reference",
        "dated",
        "payable by",
        "currency",
        "delivery point",
        "credited document",
        "before tax",
        "tax",
        "payable amount",
    ),
}
FIELDS = (
    "supplier_name",
    "supplier_trn",
    "supplier_address",
    "supplier_address_emirate",
    "buyer_name",
    "buyer_trn",
    "invoice_number",
    "issue_date",
    "due_date",
    "currency",
    "deliver_to",
    "original_invoice_number",
    "net",
    "vat",
    "total",
)
ARABIC_LABELS = {
    "supplier_name": "المورد",
    "supplier_trn": "الرقم الضريبي",
    "supplier_address": "العنوان",
    "supplier_address_emirate": "الإمارة",
    "buyer_name": "المشتري",
    "buyer_trn": "رقم المشتري الضريبي",
    "invoice_number": "رقم الفاتورة",
    "issue_date": "تاريخ الإصدار",
    "due_date": "تاريخ الاستحقاق",
    "currency": "العملة",
    "deliver_to": "موقع التسليم",
    "original_invoice_number": "الفاتورة الأصلية",
    "net": "الصافي",
    "vat": "ضريبة القيمة المضافة",
    "total": "الإجمالي",
}


@lru_cache(maxsize=1)
def arabic_font() -> str | None:
    """Use an installed font without copying or downloading system font assets."""
    candidates = [
        Path("C:/Windows/Fonts/tahoma.ttf"),
        Path("/usr/share/fonts/truetype/noto/NotoNaskhArabic-Regular.ttf"),
    ]
    for path in candidates:
        if path.is_file():
            pdfmetrics.registerFont(TTFont("BackroomArabic", str(path)))
            return "BackroomArabic"
    return None


@lru_cache(maxsize=128)
def _arabic_forms(character: str) -> dict[str, str]:
    forms = {}
    for form in ("ISOLATED", "INITIAL", "MEDIAL", "FINAL"):
        try:
            forms[form] = lookup(f"{name(character)} {form} FORM")
        except (KeyError, ValueError):
            pass
    return forms


def arabic_label(value: str) -> str:
    """Join these undiacritised Arabic-only labels when no shaping package is installed.

    Unicode presentation forms keep labels as text; English labels carry extraction quotes.
    This deliberately handles our fixed labels, rather than arbitrary mixed-direction prose.
    """
    forms = [_arabic_forms(character) for character in value]
    result = []
    for i, character in enumerate(value):
        previous = i > 0 and "INITIAL" in forms[i - 1] and "FINAL" in forms[i]
        following = i + 1 < len(value) and "INITIAL" in forms[i] and "FINAL" in forms[i + 1]
        form = (
            "MEDIAL"
            if previous and following
            else ("FINAL" if previous else "INITIAL" if following else "ISOLATED")
        )
        result.append(forms[i].get(form, character))
    return "".join(reversed(result))


def invoice_fields(record: InvoiceRecord, layout: str) -> list[tuple[str, str, str]]:
    """Quotes reflect printed values, including date and money formatting, not idealised values."""
    fields = []
    record.quotes = {}
    for key, label in zip(FIELDS, LABELS[layout], strict=True):
        value = getattr(record, key)
        if value is None:
            continue
        if isinstance(value, date):
            value = value.strftime("%d %B %Y" if layout in ("L4", "E2") else "%Y-%m-%d")
        elif key in ("net", "vat", "total"):
            value = f"{value:,.2f}"
        else:
            value = str(value)
        record.quotes[key] = value
        fields.append((key, label, value))
    for i, line in enumerate(record.lines):
        for field, value in line.model_dump().items():
            record.quotes[f"lines.{i}.{field}"] = (
                f"{value:,.2f}"
                if field == "line_net"
                else f"{value:.2f}"
                if field == "unit_price"
                else str(value)
            )
    title = "Credit Note" if record.is_credit_note else "Tax Invoice"
    record.quotes["is_credit_note"] = title
    record.quotes["suspicious_instruction"] = (
        record.notes if record.suspicious_instruction else title
    )
    if record.notes:
        record.quotes["notes"] = record.notes
    return fields


class Paper:
    def __init__(self, title: str, *, wide: bool = False):
        self.buffer = BytesIO()
        self.width, self.height = landscape(A4) if wide else A4
        self.canvas = Canvas(
            self.buffer, pagesize=(self.width, self.height), invariant=1, pageCompression=1
        )
        self.canvas.setAuthor("Saffron & Salt synthetic data")
        self.canvas.setTitle(title)
        self.canvas.setSubject("Fictional AED business document")
        self.canvas.setCreator("Backroom M1")

    def text(self, x: int, y: int, value: str, *, size: int = 9, bold: bool = False) -> None:
        self.canvas.setFont("Helvetica-Bold" if bold else "Helvetica", size)
        self.canvas.drawString(x, self.height - y, value)

    def rule(self, y: int) -> None:
        self.canvas.line(30, self.height - y, self.width - 30, self.height - y)

    def arabic(self, right: int, y: int, value: str) -> None:
        if font := arabic_font():
            self.canvas.setFont(font, 8)
            self.canvas.drawRightString(right, self.height - y, arabic_label(value))

    def finish(self) -> bytes:
        self.canvas.save()
        return self.buffer.getvalue()


def invoice_pdf(doc: Document) -> bytes:
    record = doc.truth
    if not isinstance(record, InvoiceRecord):
        raise TypeError("Invoice rendering requires InvoiceRecord")
    title = "Credit Note" if record.is_credit_note else "Tax Invoice"
    fields = invoice_fields(record, doc.layout)
    paper = Paper(f"{title} {record.invoice_number}", wide=doc.layout == "L4")
    paper.text(30, 32, title, size=18, bold=True)
    if doc.layout == "L2":
        paper.canvas.rect(24, paper.height - 322, paper.width - 48, 272)
    elif doc.layout == "L3":
        paper.arabic(565, 32, "فاتورة ضريبية")
    totals = [(key, label, value) for key, label, value in fields if key in ("net", "vat", "total")]
    headers = [
        (key, label, value) for key, label, value in fields if key not in ("net", "vat", "total")
    ]
    for i, (key, label, value) in enumerate(headers):
        paper.text(32, 66 + 20 * i, f"{label}: {value}", size=8)
        if doc.layout == "L3":
            paper.arabic(565, 66 + 20 * i, ARABIC_LABELS[key])
    supplier = next(s for s in SUPPLIERS if s.id == doc.supplier_id)
    paper.text(32, 312, f"Registered legal name: {supplier.legal_name}", size=8)
    if doc.layout == "L4":
        for i, (_, label, value) in enumerate(totals):
            paper.text(545, 75 + 25 * i, f"{label}: AED {value}", bold=True)
    top = 350
    if doc.layout == "L4":
        columns = [
            (32, "Units supplied"),
            (125, "Measure"),
            (200, "Supply particulars"),
            (475, "Rate each"),
            (585, "Levy %"),
            (700, "Extended value"),
        ]
    elif doc.layout == "L3":
        columns = [
            (32, "Goods description"),
            (225, "Count"),
            (280, "UOM"),
            (335, "Price AED"),
            (420, "Tax %"),
            (485, "Goods value"),
        ]
    elif doc.layout == "L2":
        columns = [
            (32, "Product / service"),
            (225, "Quantity"),
            (280, "Unit"),
            (335, "Rate"),
            (420, "VAT rate"),
            (485, "Extension"),
        ]
    else:
        columns = [
            (32, "Description"),
            (225, "Qty"),
            (280, "Unit"),
            (335, "Unit price"),
            (420, "VAT %"),
            (485, "Line net"),
        ]
    for x, label in columns:
        paper.text(x, top, label, size=8, bold=True)
    paper.rule(top + 8)
    for i, line in enumerate(record.lines):
        values = [
            line.description,
            str(line.quantity),
            line.unit,
            f"{line.unit_price:.2f}",
            str(line.vat_rate),
            f"{line.line_net:,.2f}",
        ]
        if doc.layout == "L4":
            values = [values[1], values[2], values[0], *values[3:]]
        for (x, _), value in zip(columns, values, strict=True):
            paper.text(x, top + 28 + 24 * i, value, size=8)
    bottom = top + 38 + 24 * len(record.lines)
    paper.rule(bottom)
    if doc.layout != "L4":
        for i, (key, label, value) in enumerate(totals):
            paper.text(335, bottom + 28 + 22 * i, f"{label}: AED {value}", bold=True)
            if doc.layout == "L3":
                paper.arabic(295, bottom + 28 + 22 * i, ARABIC_LABELS[key])
    if record.notes:
        for i, text in enumerate(wrap(record.notes, 100 if doc.layout == "L4" else 90)):
            paper.text(32, bottom + 115 + 14 * i, text, size=8)
    paper.text(32, int(paper.height) - 24, "Synthetic document | UAE | Amounts in AED", size=8)
    return paper.finish()


def payout_pdf(statement: PayoutStatement) -> bytes:
    paper = Paper(f"Platform C settlement {statement.id}", wide=True)
    paper.text(30, 35, "Platform C | Merchant settlement advice", size=17, bold=True)
    for i, text in enumerate(
        [
            f"Settlement reference: {statement.id}",
            f"Merchant site: {statement.outlet_name} ({statement.outlet_code})",
            f"Service window: {statement.week_start:%d %b %Y} to {statement.week_end:%d %b %Y}",
            f"Transfer date: {statement.paid_date:%d %b %Y}",
            f"Commission: {statement.commission_rate * 100}% of gross; commission VAT: 5%",
        ]
    ):
        paper.text(30, 67 + i * 21, text)
    columns = [
        (30, "Service day"),
        (150, "Returned AED"),
        (255, "Food gross"),
        (355, "Service fee"),
        (455, "Fee VAT"),
        (545, "Promo credit"),
        (660, "Other +/-"),
    ]
    for x, label in columns:
        paper.text(x, 200, label, size=8, bold=True)
    paper.rule(210)
    for i, line in enumerate(statement.lines):
        values = [
            line.business_date.strftime("%d %b %Y"),
            f"{line.refunds:.2f}",
            f"{line.gross:.2f}",
            f"{line.commission:.2f}",
            f"{line.vat_on_commission:.2f}",
            f"{line.cofunded_promo:.2f}",
            f"{line.adjustments:.2f}",
        ]
        for (x, _), value in zip(columns, values, strict=True):
            paper.text(x, 237 + i * 27, value, size=8)
    paper.rule(440)
    paper.text(455, 473, f"Net transfer AED {statement.net_payout:,.2f}", bold=True, size=13)
    paper.text(
        30, 525, "Synthetic Platform C | Restaurant-funded discounts are in Other +/-", size=8
    )
    return paper.finish()
