"""Strict source dialect parsers: no filename, manifest or model is needed."""

import csv
from datetime import datetime
from io import StringIO
from pathlib import Path

from backroom.extract.types import PayoutRecord
from backroom.models import PayoutLine

A = dict(
    zip(
        [
            "id",
            "outlet_code",
            "outlet_name",
            "week_start",
            "week_end",
            "paid_date",
            "commission_rate",
            "line_id",
            "business_date",
            "gross",
            "commission",
            "vat_on_commission",
            "refunds",
            "cofunded_promo",
            "adjustments",
            "net_payout",
        ],
        [
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
        ],
        strict=True,
    )
)
B = dict(
    zip(
        A,
        [
            "settlement_ref",
            "site_code",
            "site_label",
            "period_from",
            "period_to",
            "transfer_date",
            "service_rate",
            "line_ref",
            "business_day",
            "food_value",
            "service_charge",
            "fee_tax",
            "returned",
            "promo_credit",
            "other_amount",
            "transfer_amount",
        ],
        strict=True,
    )
)


def parse_csv(text: str) -> PayoutRecord:
    reader = csv.DictReader(StringIO(text.lstrip("\ufeff")))
    headers = reader.fieldnames or []
    if set(headers) == set(A.values()):
        mapping, platform, fmt = A, "A", "%d/%m/%Y"
    elif set(headers) == set(B.values()):
        mapping, platform, fmt = B, "B", "%Y-%m-%d"
    else:
        raise ValueError("Unrecognised payout CSV headers")
    rows = list(reader)
    if not rows or any(None in row or any(v is None for v in row.values()) for row in rows):
        raise ValueError("Empty or malformed payout CSV")
    first = rows[0]
    metadata = list(A)[:7]
    if any(not first[mapping[key]].strip() for key in metadata):
        raise ValueError("Missing statement metadata")
    if any(row[mapping[key]] != first[mapping[key]] for row in rows for key in metadata):
        raise ValueError("Inconsistent statement metadata")
    if rows[-1][mapping["line_id"]] != "TOTAL" or any(
        row[mapping["line_id"]] == "TOTAL" for row in rows[:-1]
    ):
        raise ValueError("Expected exactly one final TOTAL row")

    def day(value):
        return datetime.strptime(value, fmt).date()

    values = {key: first[mapping[key]] for key in metadata}
    for key in ("week_start", "week_end", "paid_date"):
        values[key] = day(values[key])
    lines = []
    quotes = {key: first[mapping[key]] for key in metadata}
    # Platform is identified by the distinct header dialect; all other facts are literal cells.
    quotes["platform"] = platform
    for index, row in enumerate(rows[:-1]):
        if not row[mapping["line_id"]].strip():
            raise ValueError("Missing service row id")
        if row[mapping["net_payout"]]:
            raise ValueError("Unexpected total on service row")
        line = {key: row[mapping[key]] for key in list(A)[9:15]}
        line.update(id=row[mapping["line_id"]], business_date=day(row[mapping["business_date"]]))
        lines.append(PayoutLine.model_validate(line))
        for key in line:
            source_key = "line_id" if key == "id" else key
            quotes[f"lines.{index}.{key}"] = row[mapping[source_key]]
    if not lines or len({line.id for line in lines}) != len(lines):
        raise ValueError("Missing or duplicate service rows")
    quotes["net_payout"] = rows[-1][mapping["net_payout"]]
    return PayoutRecord(
        **values, platform=platform, lines=lines, net_payout=quotes["net_payout"], quotes=quotes
    )


def read_csv(path: Path | str) -> PayoutRecord:
    from backroom.extract.text import MAX_BYTES, DocumentError

    path = Path(path)
    if path.stat().st_size > MAX_BYTES:
        raise DocumentError("File exceeds 5 MB")
    return parse_csv(path.read_text(encoding="utf-8-sig"))
