"""Test arithmetic boundaries and unplanted failures, independently of the answer key."""

from datetime import date
from decimal import Decimal as D

from backroom.models import (
    InvoiceLine,
    InvoiceRecord,
    LedgerLine,
    PayoutLine,
    PayoutStatement,
    POSRow,
)
from backroom.names.match import Entity, Matcher
from backroom.recon.engine import check_documents
from backroom.recon.ledger import check_ledger
from backroom.recon.payouts import check_payouts
from backroom.recon.types import Invoice, aed, week_label


def pos(**updates):
    return POSRow.model_validate(
        {
            "business_date": "2026-08-17",
            "outlet_code": "O",
            "channel": "PLATFORM_A",
            "gross_sales": "100",
            "net_sales": "100",
            "discounts": "0",
            "refunds": "0",
            "voids": "0",
            "vat_included": "4.76",
            "covers_or_orders": 1,
            **updates,
        }
    )


def statement(**updates):
    return PayoutStatement(
        id="P",
        platform="A",
        outlet_code="O",
        outlet_name="Outlet",
        week_start="2026-08-17",
        week_end="2026-08-23",
        paid_date="2026-08-26",
        commission_rate="0.25",
        lines=[
            PayoutLine(
                id="L",
                business_date="2026-08-17",
                gross="100",
                commission="25",
                vat_on_commission="1.25",
                refunds="0",
                cofunded_promo="2",
                adjustments="-1",
            )
        ],
        net_payout="74.75",
    ).model_copy(update=updates, deep=True)


def bank(amount="74.75", **updates):
    return LedgerLine.model_validate(
        {
            "date": "2026-08-26",
            "account": "100",
            "contact": "Platform A",
            "reference": "P",
            "description": "Receipt",
            "debit": amount,
            "credit": "0",
            "source": "BANK",
            **updates,
        }
    )


def invoice(**updates):
    record = InvoiceRecord(
        supplier_name="Supplier",
        invoice_number="INV",
        issue_date="2026-08-17",
        net="100",
        vat="5",
        total="105",
        lines=[InvoiceLine(line_net="100", vat_rate="5")],
    )
    return Invoice("D1", record.model_copy(update=updates, deep=True), "S", "O")


def test_payout_arithmetic_rates_bank_and_pos_checks():
    clean = statement()
    assert check_payouts([clean], [pos()], [bank()]) == []
    assert check_payouts([clean], [pos()], [bank("74.74")]) == []
    assert check_payouts([clean], [pos()], [], through=date(2026, 8, 23)) == []
    assert check_payouts([clean], [pos()], [bank("74.73")])[0].kind == "PAYOUT_SHORT"
    assert check_payouts([clean], [pos()], [bank("75")])[0].kind == "PAYOUT_OVER"
    bad = statement(net_payout=D("75"), commission_rate=D("0.28"))
    bad.lines[0].refunds = D("5")
    bad.lines[0].vat_on_commission = D("2")
    findings = check_payouts([bad], [pos(gross_sales="90")], [bank()])
    assert {e.kind for e in findings} == {
        "PAYOUT_NET_MISMATCH",
        "COMMISSION_OVER_CONTRACT",
        "COMMISSION_VAT_MISMATCH",
        "PAYOUT_SHORT",
        "PAYOUT_POS_GROSS_MISMATCH",
        "REFUND_NOT_IN_POS",
    }
    assert all(isinstance(e.amount, D) and "AED " in e.message for e in findings)
    assert check_payouts([clean], [], [bank()])[0].kind == "POS_DAY_MISSING"
    # Splitting an order into two rows does not double-count daily differences.
    split = statement()
    line = split.lines[0]
    split.lines = [
        line.model_copy(
            update={
                k: getattr(line, k) / 2
                for k in (
                    "gross",
                    "commission",
                    "vat_on_commission",
                    "refunds",
                    "cofunded_promo",
                    "adjustments",
                )
            }
        )
        for _ in range(2)
    ]
    assert check_payouts([split], [pos()], [bank()]) == []


def test_ledger_aggregation_invoice_keys_and_tolerances():
    matcher = Matcher([Entity("outlet", "O", ("Outlet",)), Entity("supplier", "S", ("Supplier",))])
    journal = bank(
        "0",
        source="SALES",
        contact="Outlet",
        credit="100",
        reference="SALES-O-2026-08-17-400",
        date="2026-08-17",
    )
    bill = bank("105", source="BILL", contact="Supplier", reference="INV")
    assert check_ledger([pos()], [journal, bill], [invoice()], matcher) == []
    journal.credit = D("101.00")
    bill.debit = D("105.01")
    assert check_ledger([pos()], [journal, bill], [invoice()], matcher) == []
    journal.credit += D("0.01")
    bill.debit += D("0.01")
    assert {e.kind for e in check_ledger([pos()], [journal, bill], [invoice()], matcher)} == {
        "POS_LEDGER_MISMATCH",
        "LEDGER_BILL_AMOUNT_MISMATCH",
    }
    assert [e.kind for e in check_ledger([], [], [invoice(), invoice()], matcher)] == [
        "LEDGER_BILL_MISSING"
    ]
    other_supplier = Invoice("D2", invoice().record, "OTHER")
    assert len(check_ledger([], [bill], [other_supplier], matcher)) == 1
    # A bill with a pending supplier name is a review dependency, not a missing bill.
    bill.contact = "unapproved alias"
    assert check_ledger([], [bill], [invoice()], matcher) == []


def test_document_arithmetic_duplicates_zero_rates_and_unknowns():
    first = invoice()
    duplicate = Invoice("D2", first.record, "S")
    other = Invoice("D3", first.record, "OTHER")
    assert [e.kind for e in check_documents([first, duplicate, other])] == ["DUPLICATE_INVOICE"]
    assert check_documents([Invoice("missing", InvoiceRecord(), None)]) == []
    assert check_documents([invoice(net=D("100.01"), vat=D("5.01"), total=D("105.02"))]) == []
    assert {
        e.kind for e in check_documents([invoice(net=D("110"), vat=D("7"), total=D("120"))])
    } == {"LINES_NOT_SUM", "VAT_WRONG", "TOTAL_WRONG"}
    zero = invoice(vat=D("0"), total=D("100"))
    zero.record.lines[0].vat_rate = D("0")
    assert check_documents([zero]) == []


def test_money_and_week_format():
    assert aed(D("1234.565")) == "AED 1,234.57"
    assert week_label(date(2026, 8, 17), date(2026, 8, 23)) == "week 34 (17-23 Aug)"
    assert week_label(date(2026, 8, 31), date(2026, 9, 6)) == "week 36 (31 Aug-6 Sep)"
