"""Ledger comparisons depend on approved identities, never on fuzzy suggestions."""

from collections import defaultdict

from backroom.config import MONEY_TOLERANCE, SALES_TOLERANCE
from backroom.models import ZERO, money
from backroom.recon.types import ExceptionRecord, aed


def check_ledger(pos, ledger, invoices, matcher):
    out = []
    sales = defaultdict(lambda: ZERO)
    journals = defaultdict(lambda: ZERO)
    bills = defaultdict(lambda: ZERO)
    unresolved_bills = set()
    for row in pos:
        sales[(row.outlet_code, row.business_date)] += row.net_sales
    for row in ledger:
        if row.source == "SALES":
            outlet = matcher.resolve("outlet", row.contact)
            # The structured reference is an explicit canonical ID in this feed.
            if not outlet:
                outlet = next(
                    (
                        p.outlet_code
                        for p in pos
                        if row.reference.startswith(f"SALES-{p.outlet_code}-")
                    ),
                    None,
                )
            if outlet:
                journals[(outlet, row.date)] += row.credit - row.debit
        if row.source == "BILL":
            supplier = matcher.resolve("supplier", row.contact)
            if supplier:
                bills[(supplier, row.reference)] += row.debit - row.credit
            else:
                unresolved_bills.add(row.reference)
    for (outlet, day), net in sorted(sales.items()):
        booked = journals[(outlet, day)]
        if abs(net - booked) > SALES_TOLERANCE:
            out.append(
                ExceptionRecord(
                    kind="POS_LEDGER_MISMATCH",
                    outlet=outlet,
                    date_or_week=day.isoformat(),
                    refs={"pos": f"{outlet}|{day}", "journal": f"SALES-{outlet}-{day}"},
                    amount=money(booked - net),
                    message=f"{outlet} on {day} has POS net {aed(net)} "
                    f"against ledger sales {aed(booked)}.",
                )
            )
    seen = set()
    for invoice in sorted(invoices, key=lambda i: i.doc_id):
        record = invoice.record
        key = (invoice.supplier_id, record.invoice_number)
        if not all(key) or key in seen or record.total is None:
            continue
        seen.add(key)
        booked = bills.get(key)
        if booked is None and record.invoice_number in unresolved_bills:
            # A pending name approval is not evidence that the bill is absent.
            continue
        kind = "LEDGER_BILL_MISSING" if booked is None else "LEDGER_BILL_AMOUNT_MISMATCH"
        if booked is None or abs(booked - record.total) > MONEY_TOLERANCE:
            value = booked if booked is not None else ZERO
            out.append(
                ExceptionRecord(
                    kind=kind,
                    outlet=invoice.outlet,
                    date_or_week=record.issue_date.isoformat() if record.issue_date else None,
                    refs={"invoice": invoice.doc_id},
                    amount=money(record.total - value),
                    message=f"Invoice {record.invoice_number} totals {aed(record.total)} against "
                    f"{'no ledger bill' if booked is None else 'a ledger bill'} of {aed(value)}.",
                )
            )
    return out
