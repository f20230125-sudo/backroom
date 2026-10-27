"""Project extracted facts, never truth, into portable SQL integer-money columns."""

import json

from backroom.models import money


def cents(value):
    return int(money(value) * 100) if value is not None else None


def project_invoices(repo, invoices, matcher):
    repo.execute("DELETE FROM invoice_lines")
    repo.execute("DELETE FROM invoice_facts")
    suppliers = {r["id"]: json.loads(r["payload"]) for r in repo.rows("SELECT * FROM suppliers")}
    items = {r["id"]: json.loads(r["payload"]) for r in repo.rows("SELECT * FROM items")}
    seen = {}
    for invoice in sorted(invoices, key=lambda i: i.doc_id):
        record = invoice.record
        if not record.issue_date or not record.invoice_number:
            continue
        key = (invoice.supplier_id, record.invoice_number)
        duplicate = seen.get(key) if invoice.supplier_id else None
        if invoice.supplier_id:
            seen.setdefault(key, invoice.doc_id)
        repo.upsert(
            "invoice_facts",
            dict(
                document_id=invoice.doc_id,
                supplier=invoice.supplier_id,
                outlet=invoice.outlet,
                issue_date=record.issue_date.isoformat(),
                invoice_number=record.invoice_number,
                supplier_name=record.supplier_name or "",
                total_cents=cents(record.total),
                duplicate_of=duplicate,
            ),
            conflict=("document_id",),
        )
        for index, line in enumerate(record.lines):
            if line.line_net is None:
                continue
            item_id = matcher.resolve("item", line.description)
            category = items.get(item_id, {}).get("category")
            # Synthetic suppliers each have one category; retain unrecognised descriptions
            # verbatim rather than making fuzzy item merges without approval.
            category = category or suppliers.get(invoice.supplier_id, {}).get("category")
            repo.upsert(
                "invoice_lines",
                dict(
                    document_id=invoice.doc_id,
                    line_no=index,
                    description=line.description or "",
                    item_id=item_id,
                    category=category,
                    unit=line.unit,
                    quantity=str(line.quantity) if line.quantity is not None else None,
                    net_cents=cents(line.line_net),
                ),
                conflict=("document_id", "line_no"),
            )
