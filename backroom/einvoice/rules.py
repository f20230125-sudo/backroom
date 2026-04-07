"""Port the TRN and emirate rules from invoice-ready; accept legacy master codes explicitly."""

import re
from collections import defaultdict

from backroom.models import Record

TRN = re.compile(r"^1\d{12}03$", re.ASCII)
EMIRATES = frozenset({"AUH", "DXB", "SHJ", "UAQ", "FUJ", "AJM", "RAK"})
# Masters use ISO subdivisions; PINT AE uses the codes above.
EMIRATE_ALIASES = {
    "AZ": "AUH",
    "DU": "DXB",
    "SH": "SHJ",
    "UQ": "UAQ",
    "FU": "FUJ",
    "AJ": "AJM",
    "RK": "RAK",
}
MANDATORY = (
    "supplier_name",
    "supplier_trn",
    "supplier_address",
    "supplier_address_emirate",
    "buyer_name",
    "buyer_trn",
    "invoice_number",
    "issue_date",
    "currency",
    "deliver_to",
    "net",
    "vat",
    "total",
    "lines",
)


class Finding(Record):
    record_type: str
    record_id: str
    rule: str
    message: str


def valid_trn(value: str) -> bool:
    return bool(TRN.fullmatch(value))


def valid_emirate(value: str) -> bool:
    return EMIRATE_ALIASES.get(value, value) in EMIRATES


def check_invoices(invoices):
    findings = []
    originals = {
        (i.supplier_key, i.record.invoice_number)
        for i in invoices
        if i.supplier_key and not i.record.is_credit_note and i.record.invoice_number
    }
    unresolved_originals = {
        i.record.invoice_number
        for i in invoices
        if not i.supplier_key and not i.record.is_credit_note and i.record.invoice_number
    }
    trns = defaultdict(dict)
    for invoice in sorted(invoices, key=lambda i: i.doc_id):
        record = invoice.record

        def add(rule, reason, invoice=invoice):
            findings.append(
                Finding(
                    record_type="document",
                    record_id=invoice.doc_id,
                    rule=rule,
                    message=f"Document {invoice.doc_id} would block an e-invoice because {reason}.",
                )
            )

        missing = [
            field
            for field in MANDATORY
            if getattr(record, field) is None
            or getattr(record, field) == ""
            or getattr(record, field) == []
        ]
        for index, line in enumerate(record.lines):
            missing.extend(
                f"line {index + 1} {field}"
                for field in (
                    "description",
                    "quantity",
                    "unit",
                    "unit_price",
                    "vat_rate",
                    "line_net",
                )
                if getattr(line, field) is None or getattr(line, field) == ""
            )
        if missing:
            add(
                "MISSING_FIELD",
                "mandatory fields are missing: "
                + ", ".join(field.replace("_", " ") for field in missing),
            )
        invalid_trns = []
        for field in ("supplier_trn", "buyer_trn"):
            value = getattr(record, field)
            if value and not valid_trn(value):
                invalid_trns.append(f"{field.replace('_trn', ' TRN')} {value}")
        if invalid_trns:
            add(
                "IBR-132-AE",
                " and ".join(invalid_trns)
                + " must have 15 digits starting with 1 and ending in 03",
            )
        emirate = record.supplier_address_emirate
        if emirate and not valid_emirate(emirate):
            add("IBR-128-AE", f"emirate {emirate} must map to AUH, DXB, SHJ, UAQ, FUJ, AJM or RAK")
        if (
            record.is_credit_note
            and invoice.supplier_key
            and record.original_invoice_number not in unresolved_originals
        ):
            if (
                not record.original_invoice_number
                or (invoice.supplier_key, record.original_invoice_number) not in originals
            ):
                add(
                    "CREDIT_NOTE_UNLINKED",
                    (
                        f"credit note {record.invoice_number} refers to invoice "
                        f"{record.original_invoice_number}, which is absent from this supplier's "
                        "received invoices"
                        if record.original_invoice_number
                        else f"credit note {record.invoice_number} "
                        "does not identify an original invoice"
                    ),
                )
        if invoice.supplier_key and record.supplier_trn and valid_trn(record.supplier_trn):
            trns[record.supplier_trn].setdefault(invoice.supplier_key, invoice.doc_id)
    for trn, identities in sorted(trns.items()):
        if len(identities) > 1:
            ids = sorted(identities.values())
            findings.append(
                Finding(
                    record_type="document_group",
                    record_id="|".join(ids),
                    rule="TRN_SHARED",
                    message=f"Documents {', '.join(ids)} would block an e-invoice "
                    f"because distinct suppliers share TRN {trn}.",
                )
            )
    return findings


def check_masters(suppliers):
    """Master checks are distinct from document detection metrics."""
    findings = []
    by_trn = defaultdict(list)
    for supplier in suppliers:
        missing = [
            name
            for name, value in (
                ("legal_name", supplier.legal_name),
                ("trn", supplier.trn),
                ("address.street", supplier.address.street),
                ("address.emirate", supplier.address.emirate),
            )
            if not value or not value.strip()
        ]
        if missing:
            findings.append(
                Finding(
                    record_type="supplier",
                    record_id=supplier.id,
                    rule="MISSING_FIELD",
                    message=f"Supplier {supplier.id} would block an e-invoice "
                    "because mandatory fields are missing: "
                    f"{', '.join(name.replace('_', ' ').replace('.', ' ') for name in missing)}.",
                )
            )
        if not valid_trn(supplier.trn):
            findings.append(
                Finding(
                    record_type="supplier",
                    record_id=supplier.id,
                    rule="IBR-132-AE",
                    message=f"Supplier {supplier.id} would block an e-invoice "
                    f"because TRN {supplier.trn} is invalid.",
                )
            )
        if not valid_emirate(supplier.address.emirate):
            findings.append(
                Finding(
                    record_type="supplier",
                    record_id=supplier.id,
                    rule="IBR-128-AE",
                    message=f"Supplier {supplier.id} would block an e-invoice "
                    f"because emirate {supplier.address.emirate} is invalid.",
                )
            )
        by_trn[supplier.trn].append(supplier.id)
    for trn, ids in sorted(by_trn.items()):
        if valid_trn(trn) and len(ids) > 1:
            findings.append(
                Finding(
                    record_type="supplier_group",
                    record_id="|".join(sorted(ids)),
                    rule="TRN_SHARED",
                    message=f"Suppliers {', '.join(sorted(ids))} would block an e-invoice "
                    f"because they share TRN {trn}.",
                )
            )
    return findings
