"""Offline milestone gates verify source facts as well as the planted-problem manifest."""

import argparse
import csv
import hashlib
import re
from collections import Counter, defaultdict
from datetime import timedelta
from decimal import Decimal
from email import policy
from email.parser import BytesParser
from pathlib import Path
from tempfile import TemporaryDirectory

import pdfplumber

from backroom import DATA, ROOT
from backroom.models import (
    ZERO,
    GroundTruth,
    InvoiceRecord,
    LedgerLine,
    PayoutStatement,
    POSRow,
    money,
)
from backroom.synth.catalogue import VARIANTS, item_code, item_description
from backroom.synth.generate import generate
from backroom.synth.scenario import END, PROBLEM_COUNTS, SEED, START

TRN = re.compile(r"^1\d{12}03$", re.ASCII)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def file_hashes(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def inventory(root: Path, truth: GroundTruth) -> None:
    require(
        truth.seed == SEED and truth.date_from == START and truth.date_to == END,
        "Seed or reporting window differs from SPEC",
    )
    require(truth.timezone == "Asia/Dubai", "Wrong timezone")
    require(
        len(truth.outlets) == 3 and len(truth.suppliers) == 18 and len(truth.items) == 60,
        "Master counts differ from 3 outlets, 18 suppliers, 60 items",
    )
    require(
        {p.id: p.commission_rate for p in truth.platforms}
        == {"A": Decimal("0.25"), "B": Decimal("0.22"), "C": Decimal("0.28")},
        "Wrong contracts",
    )
    require(all(p.commission_vat_rate == Decimal("0.05") for p in truth.platforms), "Wrong fee VAT")
    paths = [doc.path for doc in truth.documents] + [feed.path for feed in truth.feeds]
    require(len(paths) == len(set(paths)), "Duplicate manifest paths")
    require(
        len({doc.doc_id for doc in truth.documents}) == len(truth.documents), "Duplicate doc IDs"
    )
    actual = {
        path.relative_to(root).as_posix()
        for path in (root / "generated").rglob("*")
        if path.is_file()
    }
    require(set(paths) == actual, "Manifest and generated file inventory differ")
    require(
        all((root / path).resolve().is_relative_to(root.resolve()) for path in paths),
        "Unsafe source path",
    )
    require(
        sum(
            doc.path.endswith(".pdf") and doc.kind in ("invoice", "credit_note")
            for doc in truth.documents
        )
        == 90,
        "Expected 90 standalone invoice PDFs",
    )
    require(
        sum(doc.kind == "credit_note" and doc.layout == "L2" for doc in truth.documents) == 3,
        "Expected 3 L2 credit notes",
    )
    for feed in truth.feeds:
        rows = csv_rows(root / feed.path)
        require(len(rows) == feed.rows, f"Wrong feed row count: {feed.path}")
    require(sum(feed.kind == "pos" for feed in truth.feeds) == 184, "Expected 184 POS files")


def references(root: Path, truth: GroundTruth) -> None:
    require(
        Counter(p.kind for p in truth.problems) == PROBLEM_COUNTS, "Problem counts differ from SPEC"
    )
    require(len({p.id for p in truth.problems}) == 31, "Problem IDs must be unique")
    documents = {doc.doc_id: doc for doc in truth.documents}
    for problem in truth.problems:
        require(
            problem.expected_exception_kind == problem.kind, f"Wrong expected kind: {problem.id}"
        )
        require(bool(problem.refs), f"No refs: {problem.id}")
        for ref in problem.refs.values():
            path = (root / ref.path).resolve()
            require(
                path.is_relative_to(root.resolve()) and path.is_file(),
                f"Bad file ref: {problem.id}",
            )
            if ref.row is not None:
                require(
                    path.suffix == ".csv" and 1 <= ref.row <= len(csv_rows(path)),
                    f"Bad row ref: {problem.id}",
                )
            if ref.doc_id is not None:
                require(
                    ref.doc_id in documents and documents[ref.doc_id].path == ref.path,
                    f"Bad document ref: {problem.id}",
                )
            if ref.record_id is not None:
                doc = documents.get(ref.doc_id)
                require(
                    doc is not None
                    and isinstance(doc.truth, PayoutStatement)
                    and ref.record_id in {line.id for line in doc.truth.lines},
                    f"Bad statement line ref: {problem.id}",
                )


def planted_docs(truth: GroundTruth, kind: str) -> set[str]:
    return {
        ref.doc_id
        for problem in truth.problems
        if problem.kind == kind
        for ref in problem.refs.values()
        if ref.doc_id
    }


def tax_identifiers(truth: GroundTruth) -> None:
    require(all(TRN.fullmatch(s.trn) for s in truth.suppliers), "Invalid master supplier TRN")
    suppliers = {supplier.id: supplier for supplier in truth.suppliers}
    buyers = set()
    invalid = set()
    for doc in truth.documents:
        if isinstance(doc.truth, InvoiceRecord):
            record = doc.truth
            if record.buyer_trn:
                buyers.add(record.buyer_trn)
            if not record.supplier_trn or not TRN.fullmatch(record.supplier_trn):
                invalid.add(doc.doc_id)
            else:
                require(
                    record.supplier_trn == suppliers[doc.supplier_id].trn,
                    "Invoice TRN differs from supplier master",
                )
            require(
                record.buyer_trn is None or bool(TRN.fullmatch(record.buyer_trn)),
                "Invalid buyer TRN",
            )
            if record.buyer_trn is None:
                require(
                    doc.doc_id in planted_docs(truth, "MISSING_FIELD"),
                    "Unplanted missing buyer TRN",
                )
    require(invalid == planted_docs(truth, "TRN_INVALID"), "Invalid TRNs do not match plants")
    require(
        len(buyers) == 1 and buyers.isdisjoint(s.trn for s in truth.suppliers),
        "Buyer must have one distinct legal-entity TRN",
    )
    shared: dict[str, set[str]] = defaultdict(set)
    for supplier in truth.suppliers:
        shared[supplier.trn].add(supplier.id)
    pairs = [ids for ids in shared.values() if len(ids) > 1]
    require(len(pairs) == 1 and len(pairs[0]) == 2, "Expected exactly one shared-TRN pair")
    docs = {doc.doc_id: doc for doc in truth.documents}
    require(
        {docs[doc_id].supplier_id for doc_id in planted_docs(truth, "TRN_SHARED")} == pairs[0],
        "Shared-TRN refs identify the wrong suppliers",
    )


def read_pos(root: Path, truth: GroundTruth) -> list[POSRow]:
    return [
        POSRow.model_validate(row)
        for feed in truth.feeds
        if feed.kind == "pos"
        for row in csv_rows(root / feed.path)
    ]


def read_ledger(root: Path) -> list[LedgerLine]:
    return [
        LedgerLine.model_validate(row) for row in csv_rows(root / "generated/ledger/ledger.csv")
    ]


def pos_arithmetic(root: Path, truth: GroundTruth) -> None:
    rows = read_pos(root, truth)
    require(len(rows) == 736, "Expected 736 POS channel rows")
    keys = {(row.outlet_code, row.business_date, row.channel) for row in rows}
    expected = {
        (outlet, START + timedelta(days=day), channel)
        for outlet in ("OUT-JBR", "OUT-DIFC")
        for day in range(92)
        for channel in ("DINE_IN", "PLATFORM_A", "PLATFORM_B", "PLATFORM_C")
    }
    require(keys == expected, "Missing or extra outlet/day/channel")
    for row in rows:
        require(
            row.net_sales == row.gross_sales - row.discounts - row.refunds - row.voids,
            f"POS net arithmetic: {row.business_date} {row.outlet_code} {row.channel}",
        )
        require(row.vat_included == money(row.net_sales * 5 / 105), "POS included VAT arithmetic")
        require(row.covers_or_orders > 0, "Empty trading channel")
    for outlet in ("OUT-JBR", "OUT-DIFC"):
        for day in range(92):
            daily = [
                row
                for row in rows
                if row.outlet_code == outlet and row.business_date == START + timedelta(days=day)
            ]
            total = sum((row.gross_sales for row in daily), ZERO)
            delivery = sum((row.gross_sales for row in daily if row.channel != "DINE_IN"), ZERO)
            require(
                Decimal("0.30") <= delivery / total <= Decimal("0.45"),
                "Delivery share outside 30-45%",
            )
    weekend = [row.gross_sales for row in rows if row.business_date.weekday() in (4, 5)]
    weekday = [row.gross_sales for row in rows if row.business_date.weekday() not in (4, 5)]
    require(sum(weekend) / len(weekend) > sum(weekday) / len(weekday), "No weekend peak")
    dip = [
        row.gross_sales
        for row in rows
        if 10 <= row.business_date.day <= 24 and row.business_date.month == 7
    ]
    august = [row.gross_sales for row in rows if row.business_date.month == 8]
    require(
        sum(dip) / len(dip) < sum(august) / len(august) * Decimal("0.80"), "No visible July dip"
    )


def payout_arithmetic(root: Path, truth: GroundTruth) -> None:
    docs = [doc for doc in truth.documents if isinstance(doc.truth, PayoutStatement)]
    require(len(docs) == 84, "Expected 14 cycles x 3 platforms x 2 outlets")
    contracts = {p.id: p.commission_rate for p in truth.platforms}
    pos = {(r.outlet_code, r.business_date, r.channel): r for r in read_pos(root, truth)}
    over, missing_refunds = set(), set()
    coverage = set()
    for doc in docs:
        statement = doc.truth
        require(
            statement.week_start.weekday() == 0
            and statement.week_end == statement.week_start + timedelta(days=6),
            "Statement is not Monday-Sunday",
        )
        require(
            statement.paid_date == statement.week_end + timedelta(days=3),
            "Payout is not following Wednesday",
        )
        require(
            statement.net_payout
            == sum(
                (
                    line.gross
                    - line.commission
                    - line.vat_on_commission
                    - line.refunds
                    + line.cofunded_promo
                    + line.adjustments
                    for line in statement.lines
                ),
                ZERO,
            ),
            f"Payout footer: {doc.doc_id}",
        )
        if statement.commission_rate > contracts[statement.platform]:
            over.add(doc.doc_id)
        for line in statement.lines:
            require(
                line.commission == money(line.gross * statement.commission_rate), "Commission math"
            )
            require(
                line.vat_on_commission == money(line.commission * Decimal("0.05")),
                "Commission VAT math",
            )
            require(
                START <= line.business_date <= END
                and statement.week_start <= line.business_date <= statement.week_end,
                "Statement line outside its reporting window",
            )
            key = (statement.outlet_code, line.business_date, f"PLATFORM_{statement.platform}")
            require(key not in coverage, "Duplicate payout coverage")
            coverage.add(key)
            require(line.gross == pos[key].gross_sales, "Statement gross differs from POS")
            if line.refunds != pos[key].refunds:
                require(line.refunds > 0 and pos[key].refunds == 0, "Unexpected refund discrepancy")
                missing_refunds.add(doc.doc_id)
        if doc.layout in ("CSV_A", "CSV_B"):
            rows = csv_rows(root / doc.path)
            amount_field = "net_payout" if doc.layout == "CSV_A" else "transfer_amount"
            require(
                Decimal(rows[-1][amount_field]) == statement.net_payout,
                "CSV footer differs from truth",
            )
            order = (
                [
                    "gross",
                    "commission",
                    "vat_on_commission",
                    "refunds",
                    "cofunded_promo",
                    "adjustments",
                ]
                if doc.layout == "CSV_A"
                else [
                    "refunds",
                    "gross",
                    "cofunded_promo",
                    "adjustments",
                    "vat_on_commission",
                    "commission",
                ]
            )
            keys = list(rows[0])[9:15]
            require(len(rows) == len(statement.lines) + 1, "CSV line count differs from truth")
            for row, line in zip(rows[:-1], statement.lines, strict=True):
                require(
                    all(
                        Decimal(row[key]) == getattr(line, field)
                        for key, field in zip(keys, order, strict=True)
                    ),
                    "CSV statement amounts differ from truth",
                )
    require(len(coverage) == 552, "Payout coverage must include every platform outlet-day once")
    require(
        over == planted_docs(truth, "COMMISSION_OVER_CONTRACT"),
        "Commission plants differ from facts",
    )
    require(
        missing_refunds == planted_docs(truth, "REFUND_NOT_IN_POS"),
        "Refund plants differ from facts",
    )


def invoice_arithmetic(truth: GroundTruth) -> None:
    found: dict[str, set[str]] = defaultdict(set)
    records = [doc for doc in truth.documents if isinstance(doc.truth, InvoiceRecord)]
    for doc in records:
        record = doc.truth
        require(record.total == record.net + record.vat, f"Invoice total math: {doc.doc_id}")
        require(record.due_date >= record.issue_date, "Invoice due before issue")
        require(
            START <= record.issue_date <= END and record.currency == "AED", "Invoice date/currency"
        )
        require(
            all(line.line_net == money(line.quantity * line.unit_price) for line in record.lines),
            "Line math",
        )
        if sum((line.line_net for line in record.lines), ZERO) != record.net:
            found["LINES_NOT_SUM"].add(doc.doc_id)
        if abs(record.vat - money(record.net * Decimal("0.05"))) > Decimal("0.01"):
            found["VAT_WRONG"].add(doc.doc_id)
        if any(
            getattr(record, field) is None
            for field in (
                "supplier_name",
                "supplier_address",
                "supplier_address_emirate",
                "buyer_name",
                "buyer_trn",
                "invoice_number",
                "issue_date",
                "due_date",
                "deliver_to",
            )
        ):
            found["MISSING_FIELD"].add(doc.doc_id)
        if record.is_credit_note:
            original = next(
                (
                    other
                    for other in records
                    if other.supplier_id == doc.supplier_id
                    and other.truth.invoice_number == record.original_invoice_number
                ),
                None,
            )
            if original is None:
                found["CREDIT_NOTE_UNLINKED"].add(doc.doc_id)
            else:
                require(original.truth.issue_date <= record.issue_date, "Credit precedes original")
    for kind in ("VAT_WRONG", "LINES_NOT_SUM", "MISSING_FIELD", "CREDIT_NOTE_UNLINKED"):
        require(found[kind] == planted_docs(truth, kind), f"{kind} plants differ from facts")
    invoice_numbering(truth)


def invoice_numbering(truth: GroundTruth) -> None:
    """Only planted re-sends can reuse a supplier's document number."""
    records = [doc for doc in truth.documents if isinstance(doc.truth, InvoiceRecord)]
    identities: dict[tuple[str, str], list[str]] = defaultdict(list)
    for doc in records:
        identities[(doc.supplier_id, doc.truth.invoice_number)].append(doc.doc_id)
    duplicate_groups = {frozenset(ids) for ids in identities.values() if len(ids) > 1}
    expected = {
        frozenset(ref.doc_id for ref in p.refs.values())
        for p in truth.problems
        if p.kind == "DUPLICATE_INVOICE"
    }
    require(duplicate_groups == expected, "Duplicate plants differ from facts")
    for supplier in truth.suppliers:
        counters = {
            False: supplier.stationery.invoice_sequence_start,
            True: supplier.stationery.credit_note_sequence_start,
        }
        originals = sorted(
            (
                doc
                for doc in records
                if doc.supplier_id == supplier.id and not doc.attachment_doc_id
            ),
            key=lambda doc: (doc.truth.issue_date, doc.doc_id),
        )
        for doc in originals:
            record = doc.truth
            credit = record.is_credit_note
            template = (
                supplier.stationery.credit_note_format
                if credit
                else supplier.stationery.invoice_format
            )
            expected_number = template.format(
                seq=counters[credit],
                year=record.issue_date.year,
                yy=record.issue_date.year % 100,
                month=record.issue_date.month,
            )
            require(
                record.invoice_number == expected_number,
                f"Supplier invoice sequence/format: {doc.doc_id}",
            )
            counters[credit] += 1


def invoice_categories(truth: GroundTruth) -> None:
    """Canonical item provenance makes category checks independent of messy printed aliases."""
    items = {item.id: item for item in truth.items}
    suppliers = {supplier.id: supplier for supplier in truth.suppliers}
    for doc in truth.documents:
        if not isinstance(doc.truth, InvoiceRecord):
            continue
        require(doc.supplier_id in suppliers, f"Unknown invoice supplier: {doc.doc_id}")
        require(
            len(doc.line_item_ids) == len(doc.truth.lines),
            f"Missing line item provenance: {doc.doc_id}",
        )
        supplier = suppliers[doc.supplier_id]
        for item_id, line in zip(doc.line_item_ids, doc.truth.lines, strict=True):
            require(item_id in items, f"Unknown line item: {doc.doc_id}")
            item = items[item_id]
            require(
                item.category == supplier.category,
                f"Line item category differs from supplier category: {doc.doc_id} {item_id}",
            )
            descriptions = {
                item_description(supplier, item, variant)
                for variant in [item.name, *VARIANTS.get(item.id, [])]
            }
            require(
                line.description in descriptions and line.unit == item.unit,
                f"Line description/unit differs from catalogue item: {doc.doc_id} {item_id}",
            )


def ledger_reconciliation(root: Path, truth: GroundTruth) -> None:
    ledger = read_ledger(root)
    short = set()
    for doc in truth.documents:
        if isinstance(doc.truth, PayoutStatement):
            bank = [
                line for line in ledger if line.source == "BANK" and line.reference == doc.doc_id
            ]
            require(
                len(bank) == 1 and bank[0].date == doc.truth.paid_date,
                "Missing or repeated bank receipt",
            )
            if bank[0].debit - bank[0].credit != doc.truth.net_payout:
                require(bank[0].debit < doc.truth.net_payout, "Unexpected bank overpayment")
                short.add(doc.doc_id)
    require(short == planted_docs(truth, "PAYOUT_SHORT"), "Bank shortfalls differ from plants")
    aliases = {s.legal_name: s.id for s in truth.suppliers}
    aliases.update({s.trade_name: s.id for s in truth.suppliers})
    aliases.update(
        {v.variant: v.canonical_id for v in truth.name_variants if v.entity_type == "supplier"}
    )
    billed = {
        (aliases[line.contact], line.reference): line.debit - line.credit
        for line in ledger
        if line.source == "BILL"
    }
    missing = set()
    for doc in truth.documents:
        if isinstance(doc.truth, InvoiceRecord):
            key = (doc.supplier_id, doc.truth.invoice_number)
            if key not in billed:
                missing.add(doc.doc_id)
            else:
                require(billed[key] == doc.truth.total, "Bill amount differs from invoice")
    require(
        missing == planted_docs(truth, "LEDGER_BILL_MISSING"), "Missing bills differ from plants"
    )
    sums: dict[tuple[str, str], Decimal] = defaultdict(lambda: ZERO)
    for row in read_pos(root, truth):
        sums[(row.outlet_code, str(row.business_date))] += row.net_sales
    observed: dict[tuple[str, str], Decimal] = defaultdict(lambda: ZERO)
    for line in ledger:
        if line.source == "SALES":
            outlet = line.description.removeprefix("Daily sales ")
            observed[(outlet, str(line.date))] += line.credit - line.debit
    require(sums.keys() == observed.keys(), "Ledger sales dates/outlets incomplete")
    mismatches = {
        key for key, value in sums.items() if abs(value - observed[key]) > Decimal("1.00")
    }
    expected = set()
    for problem in truth.problems:
        if problem.kind == "POS_LEDGER_MISMATCH":
            row = csv_rows(root / problem.refs["pos"].path)[problem.refs["pos"].row - 1]
            expected.add((row["outlet_code"], row["business_date"]))
    require(mismatches == expected, "Sales journal mismatches differ from plants")


def held_out_and_emails(truth: GroundTruth) -> None:
    for layout in ("L4", "E2"):
        require(any(doc.layout == layout for doc in truth.documents), f"Missing held-out {layout}")
    require(
        all(doc.held_out == (doc.layout in ("L4", "E2")) for doc in truth.documents),
        "Incorrect held-out flags",
    )
    emails = [doc for doc in truth.documents if doc.kind.startswith("email_")]
    require(len(emails) * 4 >= len(truth.documents), "Email share is below 25%")
    require(
        sum(doc.kind == "email_with_attachment" for doc in emails) == 10,
        "Expected 10 attachment emails",
    )
    require(sum(doc.layout == "E2" for doc in emails) == 5, "Expected 5 held-out emails")
    require(sum(doc.kind == "email_other" for doc in emails) == 3, "Expected 3 email negatives")


def names(truth: GroundTruth) -> None:
    require(len(truth.name_variants) == 40, "Expected 40 variants")
    require(
        len({v.canonical_id for v in truth.name_variants}) == 15, "Expected 15 variant entities"
    )
    require(len(truth.hard_negatives) == 5, "Expected 5 hard negatives")
    docs = {doc.doc_id: doc for doc in truth.documents}
    ids = {record.id for record in [*truth.outlets, *truth.suppliers, *truth.items]}
    for variant in truth.name_variants:
        require(
            variant.canonical_id in ids and variant.first_seen_doc in docs,
            "Invalid variant reference",
        )
        doc = docs[variant.first_seen_doc]
        require(
            variant.variant in doc.truth.model_dump_json(), "Variant absent from its first document"
        )
    for pair in truth.hard_negatives:
        require(
            pair.left_id in ids and pair.right_id in ids and pair.left_id != pair.right_id,
            "Hard negative must refer to two real, different entities",
        )


def document_sources(root: Path, truth: GroundTruth) -> None:
    """Read development text; held-out outputs remain sealed except byte comparison."""
    plants = {plant.doc_id: plant for plant in truth.security_plants}
    suppliers = {supplier.id: supplier for supplier in truth.suppliers}
    require(len(plants) == 2, "Expected two security plants")
    flagged = {
        doc.doc_id
        for doc in truth.documents
        if isinstance(doc.truth, InvoiceRecord) and doc.truth.suspicious_instruction
    }
    require(flagged == plants.keys(), "Security flags differ from plants")
    for doc in truth.documents:
        if doc.held_out:
            continue
        if doc.path.endswith(".eml"):
            message = BytesParser(policy=policy.default).parsebytes((root / doc.path).read_bytes())
            text = message.get_body(preferencelist=("plain",)).get_content()
            attachments = list(message.iter_attachments())
            require(
                bool(attachments) == (doc.kind == "email_with_attachment"),
                "Wrong attachment presence",
            )
            if attachments:
                from io import BytesIO

                with pdfplumber.open(BytesIO(attachments[0].get_payload(decode=True))) as pdf:
                    text += "\n" + "\n".join(page.extract_text() or "" for page in pdf.pages)
        elif doc.path.endswith(".pdf"):
            with pdfplumber.open(root / doc.path) as pdf:
                text = "\n".join(page.extract_text() or "" for page in pdf.pages)
        else:
            continue
        if isinstance(doc.truth, InvoiceRecord):
            require(bool(doc.truth.quotes), f"Missing source quotes: {doc.doc_id}")
            normal = " ".join(text.split())
            for key, quote in doc.truth.quotes.items():
                require(
                    " ".join(quote.split()) in normal,
                    f"Quote absent from source: {doc.doc_id} {key}",
                )
            supplier = suppliers[doc.supplier_id]
            if supplier.stationery.show_contact:
                require(
                    supplier.stationery.phone in text and supplier.stationery.email in text,
                    f"Supplier contact clutter absent: {doc.doc_id}",
                )
            if supplier.stationery.show_bank:
                require(
                    supplier.stationery.iban in text and supplier.stationery.bank_name in text,
                    f"Supplier bank clutter absent: {doc.doc_id}",
                )
            if supplier.stationery.show_item_codes and doc.layout in ("L1", "L2"):
                require(
                    "Item code" in text
                    and all(item_code(supplier, item_id) in text for item_id in doc.line_item_ids),
                    f"Supplier item code column absent: {doc.doc_id}",
                )
        elif isinstance(doc.truth, PayoutStatement):
            statement = doc.truth
            require(
                statement.id in text and statement.outlet_code in text,
                "PDF statement identity absent from source",
            )
            require(f"{statement.net_payout:,.2f}" in text, "PDF net payout absent from source")
            for line in statement.lines:
                require(
                    line.business_date.strftime("%d %b %Y") in text,
                    "PDF statement date absent from source",
                )
                for field in (
                    "gross",
                    "commission",
                    "vat_on_commission",
                    "refunds",
                    "cofunded_promo",
                    "adjustments",
                ):
                    require(
                        f"{getattr(line, field):.2f}" in text,
                        f"PDF statement amount absent from source: {field}",
                    )
        if doc.doc_id in plants:
            plant = plants[doc.doc_id]
            require(
                plant.path == doc.path and " ".join(plant.text.split()) in " ".join(text.split()),
                "Security text absent from document body",
            )
            require(
                doc.truth.total > ZERO and doc.truth.notes == plant.text,
                "Security text changed invoice facts",
            )


def check_dataset(root: Path) -> list[tuple[str, bool, str]]:
    """Each check fails independently so a broken corpus produces an actionable report."""
    try:
        truth = GroundTruth.model_validate_json(
            (root / "ground_truth.json").read_text(encoding="utf-8")
        )
    except (OSError, ValueError) as exc:
        return [("ground-truth schema", False, str(exc))]
    checks = [
        ("inventory and volumes", lambda: inventory(root, truth)),
        ("31 problem counts and file/row references", lambda: references(root, truth)),
        ("TRNs and shared registration", lambda: tax_identifiers(truth)),
        ("POS arithmetic and trading patterns", lambda: pos_arithmetic(root, truth)),
        (
            "statement arithmetic, calendar and POS reconciliation",
            lambda: payout_arithmetic(root, truth),
        ),
        ("invoice arithmetic and document problem facts", lambda: invoice_arithmetic(truth)),
        (
            "supplier invoice sequences, unique except planted duplicates",
            lambda: invoice_numbering(truth),
        ),
        ("every invoice line matches its supplier category", lambda: invoice_categories(truth)),
        (
            "ledger receipts, bills and sales discrepancies",
            lambda: ledger_reconciliation(root, truth),
        ),
        ("held-out flags and email share >=25%", lambda: held_out_and_emails(truth)),
        ("40 observed variants and 5 hard negatives", lambda: names(truth)),
        (
            "development source quotes, attachments and security plants",
            lambda: document_sources(root, truth),
        ),
    ]
    results = []
    for name, check in checks:
        try:
            check()
        except (ValueError, KeyError, TypeError, IndexError, OSError, AttributeError) as exc:
            results.append((name, False, str(exc)))
        else:
            results.append((name, True, ""))
    print(
        "Document counts per layout:",
        dict(sorted(Counter(d.layout for d in truth.documents).items())),
    )
    print(
        "Document counts per kind:", dict(sorted(Counter(d.kind for d in truth.documents).items()))
    )
    return results


def m1(data_root: Path = DATA) -> bool:
    temporary = ROOT / ".local" / "temp"
    temporary.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="m1-gate-", dir=temporary) as directory:
        first, second = Path(directory) / "first", Path(directory) / "second"
        generate(first)
        generate(second)
        first_hashes = file_hashes(first)
        results = [
            (
                "byte determinism (all files, including PDF metadata)",
                first_hashes == file_hashes(second),
                "",
            )
        ]
        if (data_root / "ground_truth.json").exists():
            # Ignore unrelated data files; ground truth and generated/ are the generator's outputs.
            current = {
                f"generated/{key}": value
                for key, value in file_hashes(data_root / "generated").items()
            }
            current["ground_truth.json"] = hashlib.sha256(
                (data_root / "ground_truth.json").read_bytes()
            ).hexdigest()
            results.append(("populated data matches seeded generator", current == first_hashes, ""))
            results.extend(check_dataset(data_root))
        else:
            results.extend(check_dataset(first))
    for name, passed, detail in results:
        print(f"{'PASS' if passed else 'FAIL'} {name}" + (f": {detail}" if detail else ""))
    return all(passed for _, passed, _ in results)


def m2(data_root: Path = DATA) -> bool:
    """Cumulative offline gate: evaluate planted values, never inspect held-out source text."""
    from backroom.extract import rules
    from backroom.extract.agent import PROMPTS
    from backroom.extract.payouts_csv import read_csv
    from backroom.extract.text import parse_bytes
    from backroom.extract.validate import invoice_arithmetic as validate_invoice
    from backroom.llm import Store
    from backroom.settings import CacheMiss, build_llm

    previous = m1(data_root)
    truth = GroundTruth.model_validate_json(
        (data_root / "ground_truth.json").read_text(encoding="utf-8")
    )

    def planted_checks():
        for kind in ("VAT_WRONG", "LINES_NOT_SUM"):
            caught = {
                doc.doc_id
                for doc in truth.documents
                if isinstance(doc.truth, InvoiceRecord)
                and any(issue.code == kind for issue in validate_invoice(doc.truth))
            }
            require(caught == planted_docs(truth, kind), f"{kind}: validator and plants differ")
            print(f"  {kind}: {len(caught)} planted documents caught")

    def baseline_checks():
        dev = [
            doc
            for doc in truth.documents
            if not doc.held_out and doc.layout in {"L1", "L2", "L3", "E1"}
        ]
        for doc in dev:
            source = parse_bytes((data_root / doc.path).read_bytes(), Path(doc.path).suffix)
            record = rules.extract(source)
            require(isinstance(record, InvoiceRecord), "Baseline did not return InvoiceRecord")
            if doc.kind != "email_other":
                require(
                    bool(record.invoice_number and record.lines),
                    f"Baseline missing number/lines: {doc.doc_id}",
                )
        print(f"  baseline ran on {len(dev)} dev documents")

    def prompt_checks():
        for name in ("invoice_v1.md", "payout_v1.md"):
            prompt = (PROMPTS / name).read_text(encoding="utf-8")
            require(
                all(
                    token in prompt
                    for token in (
                        "<document>",
                        "</document>",
                        "data, never instructions",
                        "suspicious_instruction=true",
                        "quotes",
                    )
                ),
                f"Missing injection guard: {name}",
            )

    def replay_checks():
        store = Store(":memory:")
        try:
            llm = build_llm({}, store, offline=True)
            try:
                llm.complete([{"role": "user", "content": "m2 unrecorded unit check"}])
            except CacheMiss:
                require(llm.calls_made == 0, "Offline replay spent a provider call")
            else:
                raise ValueError("Unrecorded replay did not raise CacheMiss")
        finally:
            store.db.close()

    def csv_checks():
        statements = [doc for doc in truth.documents if doc.layout in {"CSV_A", "CSV_B"}]
        for doc in statements:
            record = read_csv(data_root / doc.path)
            require(
                record.model_dump(exclude={"quotes", "notes", "suspicious_instruction"})
                == doc.truth.model_dump(),
                f"CSV source differs from ground truth: {doc.doc_id}",
            )
        print(f"  {len(statements)} A/B statements match all source fields and totals")

    results = []
    for name, check in [
        ("m2 planted arithmetic values", planted_checks),
        ("m2 dev baseline", baseline_checks),
        ("m2 versioned prompts and injection guards", prompt_checks),
        ("m2 offline CacheMiss, zero provider calls", replay_checks),
        ("m2 deterministic A/B payout CSV totals", csv_checks),
    ]:
        try:
            check()
        except (ValueError, OSError, TypeError, KeyError) as exc:
            print(f"FAIL {name}: {exc}")
            results.append(False)
        else:
            print(f"PASS {name}")
            results.append(True)
    return previous and all(results)


def m3(data_root: Path = DATA) -> bool:
    """Cumulative gate with an explicitly labelled oracle engine test."""
    from backroom.m3_gate import checks

    previous = m2(data_root)
    try:
        checks(data_root)
    except (ValueError, OSError, TypeError, KeyError) as exc:
        print(f"FAIL m3: {exc}")
        return False
    return previous


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("milestone", choices=["m1", "m2", "m3"])
    args = parser.parse_args()
    raise SystemExit(0 if {"m1": m1, "m2": m2, "m3": m3}[args.milestone]() else 1)


if __name__ == "__main__":
    main()
