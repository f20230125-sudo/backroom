"""Aggregate before comparing: order splits must not create duplicate daily alarms."""

from collections import defaultdict

from backroom.config import COMMISSION_RATES, COMMISSION_VAT_RATE, MONEY_TOLERANCE
from backroom.models import ZERO, money
from backroom.recon.types import ExceptionRecord, aed, week_label


def check_payouts(statements, pos, ledger, *, through=None):
    out = []
    pos_index = {(p.outlet_code, p.business_date, p.channel): p for p in pos}
    bank = defaultdict(lambda: ZERO)
    for row in ledger:
        if row.source == "BANK":
            bank[row.reference] += row.debit - row.credit
    for statement in statements:
        label = week_label(statement.week_start, statement.week_end)
        context = f"Platform {statement.platform} for {statement.outlet_code} {label}"

        def add(kind, amount, message, statement=statement, label=label, **refs):
            out.append(
                ExceptionRecord(
                    kind=kind,
                    outlet=statement.outlet_code,
                    date_or_week=label,
                    refs={"statement": statement.id, **refs},
                    amount=money(amount),
                    message=message,
                )
            )

        gross = sum((line.gross for line in statement.lines), ZERO)
        commission = sum((line.commission for line in statement.lines), ZERO)
        tax = sum((line.vat_on_commission for line in statement.lines), ZERO)
        refunds = sum((line.refunds for line in statement.lines), ZERO)
        promo = sum((line.cofunded_promo for line in statement.lines), ZERO)
        adjustments = sum((line.adjustments for line in statement.lines), ZERO)
        expected = gross - commission - tax - refunds + promo + adjustments
        if abs(expected - statement.net_payout) > MONEY_TOLERANCE:
            add(
                "PAYOUT_NET_MISMATCH",
                statement.net_payout - expected,
                f"{context} states {aed(statement.net_payout)} "
                f"against calculated net {aed(expected)}.",
            )
        rate = COMMISSION_RATES[statement.platform]
        # Per-line rounding is part of the feed contract, not a monetary tolerance increase.
        contract = sum((money(line.gross * rate) for line in statement.lines), ZERO)
        if statement.commission_rate > rate or commission - contract > MONEY_TOLERANCE:
            add(
                "COMMISSION_OVER_CONTRACT",
                commission - contract,
                f"{context} charges {statement.commission_rate * 100}% and {aed(commission)} "
                f"against contract {rate * 100}% and {aed(contract)}.",
            )
        expected_tax = sum(
            (money(line.commission * COMMISSION_VAT_RATE) for line in statement.lines), ZERO
        )
        if abs(tax - expected_tax) > MONEY_TOLERANCE:
            add(
                "COMMISSION_VAT_MISMATCH",
                tax - expected_tax,
                f"{context} charges commission VAT {aed(tax)} "
                f"against expected {aed(expected_tax)}.",
            )
        if through is None or statement.paid_date <= through:
            received = bank[statement.id]
            difference = statement.net_payout - received
            if abs(difference) > MONEY_TOLERANCE:
                add(
                    "PAYOUT_SHORT" if difference > ZERO else "PAYOUT_OVER",
                    difference,
                    f"{context} paid {aed(received)} "
                    f"against statement net {aed(statement.net_payout)} "
                    f"with difference {aed(difference)}.",
                )
        daily = defaultdict(lambda: [ZERO, ZERO])
        for line in statement.lines:
            daily[line.business_date][0] += line.gross
            daily[line.business_date][1] += line.refunds
        for day, (daily_gross, daily_refunds) in sorted(daily.items()):
            if through is not None and day > through:
                continue
            channel = f"PLATFORM_{statement.platform}"
            key = (statement.outlet_code, day, channel)
            row = pos_index.get(key)
            # Absent POS is a data gap, never silently turned into zero sales.
            if row is None:
                add(
                    "POS_DAY_MISSING",
                    daily_gross,
                    f"{context} has {aed(daily_gross)} on {day} with no POS row.",
                )
                continue
            ref = f"{statement.outlet_code}|{day}|{channel}"
            if abs(daily_gross - row.gross_sales) > MONEY_TOLERANCE:
                add(
                    "PAYOUT_POS_GROSS_MISMATCH",
                    daily_gross - row.gross_sales,
                    f"{context} reports gross {aed(daily_gross)} on {day} "
                    f"against POS {aed(row.gross_sales)}.",
                    pos=ref,
                )
            if abs(daily_refunds - row.refunds) > MONEY_TOLERANCE:
                add(
                    "REFUND_NOT_IN_POS" if daily_refunds > row.refunds else "REFUND_MISMATCH",
                    daily_refunds - row.refunds,
                    f"{context} refunds {aed(daily_refunds)} on {day} "
                    f"against POS refunds {aed(row.refunds)}.",
                    pos=ref,
                )
    return out
