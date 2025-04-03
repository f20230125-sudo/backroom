"""Incremental offline ingestion; extraction recordings are always read-only inputs."""

import argparse
import hashlib
import time
from collections import Counter
from datetime import UTC, date, datetime, timedelta

from backroom import DATA
from backroom.ask.projection import cents, project_invoices
from backroom.db import connect, migrate
from backroom.db.repo import dumps
from backroom.einvoice.rules import check_invoices, check_masters
from backroom.extract.types import ExtractionResult, PayoutRecord
from backroom.inputs import csv_payouts, feeds, load_results, manifest
from backroom.models import InvoiceRecord, PayoutStatement, Supplier
from backroom.names.match import (
    Matcher,
    approved_names,
    automatic_names,
    masters,
    propose,
    rejected_names,
)
from backroom.recon.engine import reconcile
from backroom.recon.types import Invoice


def stable_id(value):
    return hashlib.sha256(dumps(value).encode()).hexdigest()


def load_masters(repo, source):
    for table, columns in (
        ("outlets", ("id", "name")),
        ("suppliers", ("id", "legal_name", "trade_name", "trn")),
        ("items", ("id", "name")),
    ):
        for row in source[table]:
            repo.upsert(table, {**{key: row[key] for key in columns}, "payload": dumps(row)})


def invoice_inputs(results, matcher):
    def supplier_id(record):
        identity = matcher.supplier_identity(record.supplier_name, record.supplier_trn)
        return identity if identity and not identity.startswith("printed:") else None

    return [
        Invoice(
            doc_id,
            result.record,
            supplier_id(result.record),
            matcher.resolve("outlet", result.record.deliver_to),
        )
        for doc_id, result in sorted(results.items())
        if not result.not_run and isinstance(result.record, InvoiceRecord)
    ]


def payout_inputs(docs, results, data_root):
    statements = csv_payouts(data_root, docs)
    for doc in docs:
        result = results.get(doc["doc_id"])
        if (
            doc["layout"] != "PDF_C"
            or result is None
            or result.not_run
            or not isinstance(result.record, PayoutRecord)
        ):
            continue
        # Incomplete model output belongs in review and cannot enter arithmetic.
        try:
            values = result.record.model_dump(include=set(PayoutStatement.model_fields))
            statement = PayoutStatement.model_validate(values)
            required = (
                "business_date",
                "gross",
                "commission",
                "vat_on_commission",
                "refunds",
                "cofunded_promo",
                "adjustments",
            )
            if not statement.lines or any(
                getattr(line, key) is None for line in statement.lines for key in required
            ):
                continue
            statements.append(statement)
        except ValueError:
            continue
    return statements


def document_day(doc, fallback, final=None):
    """Manifest dates are scheduling metadata only; business checks never read truth."""
    truth = doc.get("truth", {})
    day = date.fromisoformat(truth.get("issue_date") or truth.get("week_end") or fallback)
    # The final partial business week closes after the dataset horizon.
    return min(day, final) if final and doc["kind"] == "payout_statement" else day


def review(repo, key, reason, *, doc_id=None, payload=None):
    repo.upsert(
        "review_queue",
        {
            "id": stable_id(key),
            "document_id": doc_id,
            "reason": reason,
            "status": "open",
            "payload": dumps(payload or {}),
        },
    )


def observed_names(results, pos, ledger, statements):
    names = set()
    for result in results.values():
        r = result.record
        if result.not_run or not isinstance(r, InvoiceRecord):
            continue
        names.update(
            (kind, value)
            for kind, value in (("supplier", r.supplier_name), ("outlet", r.deliver_to))
            if value
        )
        names.update(("item", line.description) for line in r.lines if line.description)
    names.update(("outlet", row.outlet_code) for row in pos)
    names.update(
        ("supplier" if row.source == "BILL" else "outlet", row.contact)
        for row in ledger
        if row.source in {"BILL", "SALES"}
    )
    names.update(("outlet", statement.outlet_name) for statement in statements)
    return sorted(names)


def _run(repo, source, *, data_root, days, rebuild, results, record, llm, clock, timer):
    started, tick = clock(), timer()
    if rebuild:
        repo.clear_loaded()
    previous = repo.rows("SELECT watermark FROM run_log ORDER BY id DESC LIMIT 1")
    last = previous[0]["watermark"] if previous and not rebuild else None
    first, final = date.fromisoformat(source["date_from"]), date.fromisoformat(source["date_to"])
    start = date.fromisoformat(last) + timedelta(days=1) if last else first
    end = min(final, start + timedelta(days=days - 1)) if days else final
    end = max(end, first - timedelta(days=1))
    load_masters(repo, source)
    matcher = Matcher(
        masters(source),
        approved_names(repo),
        automatic=automatic_names(repo),
        rejected=rejected_names(repo),
    )
    # Revisit missing recordings even after the feed watermark reaches the end.
    docs = [d for d in source["documents"] if document_day(d, source["date_from"], final) <= end]
    loaded = {
        row["document_id"]: ExtractionResult.model_validate_json(row["payload"])
        for row in repo.rows("SELECT * FROM extractions")
    }
    pending = [d for d in docs if d["doc_id"] not in loaded or loaded[d["doc_id"]].not_run]
    replayed = results is None and not (data_root / "out/extractions.jsonl").exists()
    fresh = load_results(data_root, pending, results=results, record=record, llm=llm)
    loaded.update(fresh)
    for doc in pending:
        result = loaded[doc["doc_id"]]
        metadata = {k: v for k, v in doc.items() if k != "truth"}
        state = "not_run" if result.not_run else result.status
        repo.upsert(
            "documents",
            {
                "id": doc["doc_id"],
                "path": doc["path"],
                "kind": doc["kind"],
                "layout": doc["layout"],
                "business_date": document_day(doc, source["date_from"], final).isoformat(),
                "status": state,
                "payload": dumps(metadata),
            },
        )
        repo.upsert(
            "extractions",
            {
                "document_id": doc["doc_id"],
                "status": state,
                "prompt_version": result.prompt_version,
                "model_calls": result.model_calls,
                "cache_hits": result.cache_hits,
                "not_run": int(result.not_run),
                "payload": result.model_dump_json(),
            },
            conflict=("document_id",),
        )
        repo.execute("DELETE FROM extraction_issues WHERE document_id = ?", (doc["doc_id"],))
        for issue in result.issues:
            repo.upsert(
                "extraction_issues",
                {
                    "id": stable_id([doc["doc_id"], issue.code, issue.field]),
                    "document_id": doc["doc_id"],
                    "code": issue.code,
                    "field": issue.field,
                    "payload": issue.model_dump_json(),
                },
            )
    pos_all, ledger_all = feeds(data_root)
    pos = [row for row in pos_all if row.business_date <= end]
    ledger = [
        row for row in ledger_all if row.date <= end or (end == final and row.source == "BANK")
    ]
    for row in pos:
        repo.upsert(
            "pos_days",
            {
                "outlet": row.outlet_code,
                "business_date": row.business_date.isoformat(),
                "channel": row.channel,
                "gross_sales": str(row.gross_sales),
                "net_sales": str(row.net_sales),
                "refunds": str(row.refunds),
                "net_cents": cents(row.net_sales),
                "gross_cents": cents(row.gross_sales),
                "orders": row.covers_or_orders,
                "payload": row.model_dump_json(),
            },
            conflict=("outlet", "business_date", "channel"),
        )
    for index, row in enumerate(ledger_all, 1):
        if row.date <= end or (end == final and row.source == "BANK"):
            repo.upsert(
                "ledger_lines",
                {
                    "id": f"ledger:{index}",
                    "date": row.date.isoformat(),
                    "source": row.source,
                    "contact": row.contact,
                    "reference": row.reference,
                    "debit": str(row.debit),
                    "credit": str(row.credit),
                    "receipt_cents": cents(row.debit - row.credit),
                    "payload": row.model_dump_json(),
                },
            )
    statements = payout_inputs(docs, loaded, data_root)
    known_outlets = {row["id"] for row in source["outlets"]}
    statements = [s for s in statements if s.outlet_code in known_outlets]
    for s in statements:
        repo.upsert(
            "payout_statements",
            {
                "id": s.id,
                "platform": s.platform,
                "outlet": s.outlet_code,
                "week_start": s.week_start.isoformat(),
                "week_end": s.week_end.isoformat(),
                "paid_date": s.paid_date.isoformat(),
                "net_payout": str(s.net_payout),
                "net_cents": cents(s.net_payout),
                "payload": s.model_dump_json(),
            },
        )
        repo.execute("DELETE FROM payout_lines WHERE statement_id = ?", (s.id,))
        for index, line in enumerate(s.lines):
            repo.upsert(
                "payout_lines",
                {
                    "statement_id": s.id,
                    "line_no": index,
                    "business_date": line.business_date.isoformat(),
                    "payload": line.model_dump_json(),
                },
                conflict=("statement_id", "line_no"),
            )
    # Derived tables are replaced transactionally so approvals or newly arrived records
    # can remove stale findings without accumulating duplicates.
    for table in ("review_queue", "exceptions", "einvoice_findings"):
        repo.execute(f"DELETE FROM {table}")  # noqa: S608 -- fixed identifiers
    for kind, name in observed_names(loaded, pos, ledger, statements):
        proposal = matcher.match(kind, name, model_check=True)
        propose(repo, proposal)
        if not matcher.resolve(kind, name):
            review(
                repo,
                ["name", kind, name],
                f"Approve or reject {kind} name {name}",
                payload={
                    "entity_type": kind,
                    "variant": name,
                    "suggested_id": proposal.suggested_id,
                    "score": proposal.score,
                    "same_entity": proposal.recommendation,
                    "reason": proposal.reason,
                },
            )
    for doc_id, result in sorted(loaded.items()):
        if result.not_run or result.status == "review":
            review(
                repo,
                ["extraction", doc_id],
                "Extraction not run" if result.not_run else "Extraction needs review",
                doc_id=doc_id,
                payload={"issues": [i.model_dump() for i in result.issues]},
            )
    ingested_payouts = {s.id for s in statements}
    for doc in docs:
        if doc["kind"] == "payout_statement" and doc["doc_id"] not in ingested_payouts:
            review(
                repo,
                ["payout", doc["doc_id"]],
                "Payout extraction is incomplete",
                doc_id=doc["doc_id"],
            )
    invoices = invoice_inputs(loaded, matcher)
    project_invoices(repo, invoices, matcher)
    exceptions = reconcile(
        statements=statements,
        pos=pos,
        ledger=ledger,
        invoices=invoices,
        matcher=matcher,
        through=None if end == final else end,
    )
    findings = check_invoices(invoices) + check_masters(
        [Supplier.model_validate(s) for s in source["suppliers"]]
    )
    for exc in exceptions:
        row = exc.model_dump(mode="json")
        statement = next((s for s in statements if s.id == exc.refs.get("statement")), None)
        date_start = statement.week_start.isoformat() if statement else exc.date_or_week
        date_end = statement.week_end.isoformat() if statement else exc.date_or_week
        repo.upsert(
            "exceptions",
            {
                "id": stable_id([exc.kind, exc.refs]),
                **{k: v for k, v in row.items() if k != "refs"},
                "refs": dumps(exc.refs),
                "date_start": date_start,
                "date_end": date_end,
                "amount_cents": cents(exc.amount),
                "payload": exc.model_dump_json(),
            },
        )
    for finding in findings:
        row = finding.model_dump()
        repo.upsert(
            "einvoice_findings",
            {
                "id": stable_id([finding.record_type, finding.record_id, finding.rule]),
                **row,
                "payload": finding.model_dump_json(),
            },
        )
    run_id = repo.rows("SELECT COALESCE(MAX(id), 0) + 1 AS next_id FROM run_log")[0]["next_id"]
    from backroom.brief import generate_brief
    from backroom.drafts import generate_drafts

    drafts = generate_drafts(repo)
    generate_brief(repo, run_id)
    summary = {
        "id": run_id,
        "started": started.isoformat(),
        "finished": clock().isoformat(),
        "days": max(0, (end - start).days + 1),
        "documents": len(pending),
        "model_calls": sum(r.model_calls for r in fresh.values()) if replayed else 0,
        "cache_hits": sum(r.cache_hits for r in fresh.values()) if replayed else 0,
        "exceptions_by_kind": dumps(dict(sorted(Counter(e.kind for e in exceptions).items()))),
        "review_items": len(repo.rows("SELECT id FROM review_queue")),
        "duration_ms": max(0, (timer() - tick) // 1_000_000),
        "watermark": end.isoformat(),
    }
    repo.upsert(
        "run_log",
        {
            **summary,
            "payload": dumps(
                {
                    "status": "completed",
                    "not_run": sum(r.not_run for r in loaded.values()),
                    "extractions": len(fresh),
                    "record": record,
                    "drafts": len(drafts),
                    "briefs": 1,
                    "input_mode": "extraction" if replayed else "saved_results",
                    "saved_model_calls": 0
                    if replayed
                    else sum(r.model_calls for r in fresh.values()),
                }
            ),
        },
    )
    repo.commit()
    return summary


def run_pipeline(
    *,
    repo=None,
    data_root=DATA,
    days=None,
    offline=True,
    record=False,
    rebuild=False,
    results=None,
    llm=None,
    clock=lambda: datetime.now(UTC),
    timer=time.perf_counter_ns,
):
    if days is not None and days < 1:
        raise ValueError("Days must be positive")
    # --record affects extraction only, not name checks or downstream processing.
    owned = repo is None
    repo = repo if repo is not None else connect()
    initialised = False
    try:
        migrate(repo)
        initialised = True
        return _run(
            repo,
            manifest(data_root),
            data_root=data_root,
            days=days,
            rebuild=rebuild,
            results=results,
            record=record,
            llm=llm,
            clock=clock,
            timer=timer,
        )
    except Exception as exc:
        repo.rollback()
        if not initialised:
            raise
        previous = repo.rows("SELECT watermark FROM run_log ORDER BY id DESC LIMIT 1")
        now = clock().isoformat()
        run_id = repo.rows("SELECT COALESCE(MAX(id), 0) + 1 AS next_id FROM run_log")[0]["next_id"]
        repo.upsert(
            "run_log",
            {
                "id": run_id,
                "started": now,
                "finished": now,
                "days": 0,
                "documents": 0,
                "model_calls": 0,
                "cache_hits": 0,
                "exceptions_by_kind": "{}",
                "review_items": 0,
                "duration_ms": 0,
                "watermark": previous[0]["watermark"] if previous else None,
                "payload": dumps({"status": "failed", "error_type": type(exc).__name__}),
            },
        )
        repo.commit()
        raise
    finally:
        if owned:
            repo.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["run"])
    parser.add_argument("--days", type=int)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--offline", action="store_true")
    mode.add_argument("--record", action="store_true")
    parser.add_argument("--rebuild", action="store_true")
    args = parser.parse_args()
    try:
        result = run_pipeline(days=args.days, record=args.record, rebuild=args.rebuild)
    except Exception as exc:
        parser.exit(1, f"Pipeline failed ({type(exc).__name__}); details withheld.\n")
    print(dumps(result))


if __name__ == "__main__":
    main()
