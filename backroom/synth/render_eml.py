"""Freeze MIME identifiers, dates and boundaries as well as the attached PDF bytes."""

from datetime import datetime, time, timedelta, timezone
from email import policy
from email.message import EmailMessage
from email.utils import format_datetime

from backroom.models import Document, InvoiceRecord, Supplier
from backroom.synth.render_pdf import bank_line, contact_line, invoice_fields, invoice_pdf


def email_bytes(doc: Document, supplier: Supplier | None = None) -> bytes:
    if doc.kind != "email_other" and supplier is None:
        raise TypeError("Invoice emails require supplier stationery")
    record = doc.truth
    msg = EmailMessage(policy=policy.SMTP)
    msg["From"] = (
        f"Accounts desk <{supplier.stationery.email}>"
        if supplier
        else "Accounts desk <accounts@supplier.example>"
    )
    msg["To"] = "payables@saffron-salt.example"
    msg["Message-ID"] = f"<{doc.doc_id.lower()}@backroom.example>"
    day = record.issue_date if isinstance(record, InvoiceRecord) else datetime(2026, 9, 29).date()
    msg["Date"] = format_datetime(datetime.combine(day, time(10, 30), timezone(timedelta(hours=4))))
    if doc.kind == "email_other":
        msg["Subject"] = record["subject"]
        msg.set_content(record["body"])
    elif doc.kind == "email_with_attachment":
        msg["Subject"] = f"Supplier invoice {record.invoice_number} attached"
        msg.set_content(
            f"Dear accounts team,\nPlease find {record.invoice_number} attached for booking.\n"
            "Regards,\nSupplier accounts desk\n"
        )
        msg.add_attachment(
            invoice_pdf(doc, supplier),
            maintype="application",
            subtype="pdf",
            filename=f"{record.invoice_number}.pdf",
        )
        msg.set_boundary(f"backroom-m1-{doc.doc_id}")
    else:
        title = "Credit Note" if record.is_credit_note else "Tax Invoice"
        msg["Subject"] = f"{title} {record.invoice_number} - details below"
        fields = invoice_fields(record, doc.layout)
        lines = [
            f"{line.description}; quantity {line.quantity} {line.unit}; "
            f"unit price {line.unit_price:.2f}; "
            f"VAT {line.vat_rate}%; line net {line.line_net:,.2f}"
            for line in record.lines
        ]
        if doc.layout == "E1":
            body = "\n".join(
                [
                    "Dear accounts team,",
                    title,
                    *[f"{label}: {value}" for _, label, value in fields],
                    "Items:",
                    *lines,
                    record.notes or "",
                    "Regards, Supplier accounts desk",
                ]
            )
        else:
            body = (
                f"Hello team, here is the {title} we discussed. "
                + ", and ".join(f"{label} is {value}" for _, label, value in fields)
                + ". We delivered the following: "
                + ". Also, ".join(lines)
                + ". Please arrange settlement when convenient. Many thanks, the accounts desk."
            )
        if supplier.stationery.show_contact:
            body += "\n" + contact_line(supplier)
        if supplier.stationery.show_bank:
            body += "\n" + bank_line(supplier)
        msg.set_content(body)
    return msg.as_bytes()
