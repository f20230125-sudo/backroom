"""Document arithmetic uses extracted values; missing facts do not become invented zeros."""

from collections import defaultdict

from backroom.config import MONEY_TOLERANCE, VAT_RATE
from backroom.models import ZERO, money
from backroom.recon.ledger import check_ledger
from backroom.recon.payouts import check_payouts
from backroom.recon.types import ExceptionRecord, aed


def check_documents(invoices):
    out = []
    groups = defaultdict(list)
    for invoice in sorted(invoices, key=lambda i: i.doc_id):
        r = invoice.record

        def add(kind, actual, expected, invoice=invoice, r=r):
            descriptions = {
                "LINES_NOT_SUM": f"states net {aed(actual)}, but its lines total {aed(expected)}",
                "VAT_WRONG": f"states VAT {aed(actual)}, but the calculated VAT is {aed(expected)}",
                "TOTAL_WRONG": f"states total {aed(actual)}, but net plus VAT is {aed(expected)}",
            }
            out.append(
                ExceptionRecord(
                    kind=kind,
                    outlet=invoice.outlet,
                    date_or_week=r.issue_date.isoformat() if r.issue_date else None,
                    refs={"invoice": invoice.doc_id},
                    amount=money(actual - expected),
                    message=f"Invoice {r.invoice_number or invoice.doc_id} {descriptions[kind]}.",
                )
            )

        if r.net is not None:
            if r.lines and all(line.line_net is not None for line in r.lines):
                total = sum((line.line_net for line in r.lines), ZERO)
                if abs(total - r.net) > MONEY_TOLERANCE:
                    add("LINES_NOT_SUM", r.net, total)
            if r.vat is not None and r.lines and all(line.vat_rate is not None for line in r.lines):
                rate = ZERO if all(line.vat_rate == ZERO for line in r.lines) else VAT_RATE
                expected = money(r.net * rate)
                if abs(r.vat - expected) > MONEY_TOLERANCE:
                    add("VAT_WRONG", r.vat, expected)
            if r.total is not None and r.vat is not None:
                if abs(r.total - r.net - r.vat) > MONEY_TOLERANCE:
                    add("TOTAL_WRONG", r.total, r.net + r.vat)
        if invoice.supplier_key and r.invoice_number:
            groups[(invoice.supplier_key, r.invoice_number)].append(invoice)
    for group in groups.values():
        original = group[0]
        for duplicate in group[1:]:
            r = duplicate.record
            # An incomplete total cannot supply a monetary duplicate finding.
            if r.total is None:
                continue
            out.append(
                ExceptionRecord(
                    kind="DUPLICATE_INVOICE",
                    outlet=duplicate.outlet,
                    date_or_week=r.issue_date.isoformat() if r.issue_date else None,
                    refs={"original": original.doc_id, "duplicate": duplicate.doc_id},
                    amount=r.total,
                    message=f"Invoice {r.invoice_number} from "
                    f"{r.supplier_name or duplicate.supplier_id} "
                    f"for {aed(r.total)} appears in both {original.doc_id} and {duplicate.doc_id}.",
                )
            )
    return out


def reconcile(*, statements, pos, ledger, invoices, matcher, through=None):
    return (
        check_payouts(statements, pos, ledger, through=through)
        + check_ledger(pos, ledger, invoices, matcher)
        + check_documents(invoices)
    )
