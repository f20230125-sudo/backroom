"""CSV dialects intentionally differ; ledger and POS retain simple stable headers."""

import csv
from io import StringIO

from backroom.models import LedgerLine, PayoutStatement, POSRow


def rows_csv(headers: list[str], rows: list[list[str]]) -> bytes:
    buffer = StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(headers)
    writer.writerows(rows)
    return buffer.getvalue().encode("utf-8")


def records_csv(records: list[POSRow] | list[LedgerLine]) -> bytes:
    headers = list(type(records[0]).model_fields)
    return rows_csv(headers, [[str(getattr(record, key)) for key in headers] for record in records])


def payout_csv(statement: PayoutStatement) -> bytes:
    if statement.platform == "A":
        headers = [
            "statement_id",
            "outlet_code",
            "merchant",
            "week_start",
            "week_end",
            "paid_on",
            "fee_rate",
            "row_id",
            "sale_date",
            "gross_sales",
            "commission",
            "commission_vat",
            "refunds",
            "promo_share",
            "adjustments",
            "net_payout",
        ]
        date_format = "%d/%m/%Y"
        order = [
            "gross",
            "commission",
            "vat_on_commission",
            "refunds",
            "cofunded_promo",
            "adjustments",
        ]
    else:
        headers = [
            "settlement_ref",
            "site_code",
            "site_label",
            "period_from",
            "period_to",
            "transfer_date",
            "service_rate",
            "line_ref",
            "business_day",
            "returned",
            "food_value",
            "promo_credit",
            "other_amount",
            "fee_tax",
            "service_charge",
            "transfer_amount",
        ]
        date_format = "%Y-%m-%d"
        order = [
            "refunds",
            "gross",
            "cofunded_promo",
            "adjustments",
            "vat_on_commission",
            "commission",
        ]
    metadata = [
        statement.id,
        statement.outlet_code,
        statement.outlet_name,
        statement.week_start.strftime(date_format),
        statement.week_end.strftime(date_format),
        statement.paid_date.strftime(date_format),
        str(statement.commission_rate),
    ]
    rows = [
        metadata
        + [line.id, line.business_date.strftime(date_format)]
        + [f"{getattr(line, key):.2f}" for key in order]
        + [""]
        for line in statement.lines
    ]
    rows.append(metadata + ["TOTAL", ""] + [""] * 6 + [f"{statement.net_payout:.2f}"])
    return rows_csv(headers, rows)
