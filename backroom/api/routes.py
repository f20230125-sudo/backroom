"""Versionless demo API contract. Money crosses the JSON boundary as decimal strings."""

import csv
import io
import json
from decimal import Decimal, DecimalException

from flask import Blueprint, Response, current_app, jsonify, request
from pydantic import ValidationError
from werkzeug.exceptions import BadRequest, Forbidden, NotFound

from backroom.api.data import (
    artifact,
    collection,
    db,
    exceptions,
    integer,
    one,
    recon_filters,
    selection,
)
from backroom.api.security import READ_ONLY
from backroom.ask.tools import Tools
from backroom.einvoice.rules import check_invoices
from backroom.extract.types import ExtractionResult
from backroom.extract.validate import invoice_arithmetic, validate
from backroom.models import InvoiceRecord
from backroom.names.match import approve
from backroom.recon.engine import check_documents
from backroom.recon.types import Invoice

bp = Blueprint("api", __name__, url_prefix="/api")
KINDS = {
    "invoice",
    "credit_note",
    "payout_statement",
    "email_invoice_body",
    "email_other",
    "email_with_attachment",
}
STATUSES = {"accepted", "review", "document_problem", "not_run"}


def body():
    if not request.is_json:
        raise BadRequest("Send application/json")
    try:
        value = json.loads(request.get_data(), parse_float=Decimal, parse_constant=invalid_number)
    except (ValueError, UnicodeDecodeError) as exc:
        raise BadRequest("Invalid JSON") from exc
    if not isinstance(value, dict):
        raise BadRequest("Send a JSON object")
    return value


def invalid_number(_value):
    raise ValueError("Non-finite number")


def local_only():
    if current_app.config["APP_ENV"] != "local":
        raise Forbidden(READ_ONLY)


@bp.get("/health")
def health():
    db().rows("SELECT version FROM schema_migrations LIMIT 1")
    return jsonify(
        ok=True,
        mode=current_app.config["APP_ENV"],
        readonly=(current_app.config["APP_ENV"] != "local"),
    )


@bp.get("/overview")
def overview():
    runs = collection("SELECT * FROM run_log ORDER BY id DESC LIMIT 1")
    grouped = {}
    for row in exceptions():
        grouped.setdefault(row["kind"], []).append(row)
    return jsonify(
        last_run=runs[0] if runs else None,
        exceptions_by_kind=grouped,
        brief=latest_brief_data(),
        hours_saved=artifact("RESULTS_PATH").get("hours_saved"),
        mode=current_app.config["APP_ENV"],
    )


@bp.get("/documents")
def documents():
    page, size = integer("page", 1), integer("page_size", 25, 100)
    kind, status = selection("kind", KINDS), selection("status", STATUSES)
    held = selection("held_out", {"true", "false"})
    # The portable schema keeps held_out in metadata, so apply this small demo filter in Python.
    rows = collection(
        "SELECT * FROM documents WHERE (CAST(? AS TEXT) IS NULL OR kind = ?) "
        "AND (CAST(? AS TEXT) IS NULL OR status = ?) ORDER BY id",
        (kind, kind, status, status),
    )
    if held:
        rows = [r for r in rows if bool(r.get("held_out")) == (held == "true")]
    return jsonify(
        items=rows[(page - 1) * size : page * size], total=len(rows), page=page, page_size=size
    )


@bp.get("/documents/<doc_id>")
def document(doc_id):
    doc = one("SELECT * FROM documents WHERE id = ?", (doc_id,))
    extracted = collection("SELECT * FROM extractions WHERE document_id = ?", (doc_id,))
    sources = db().rows("SELECT * FROM document_sources WHERE document_id = ?", (doc_id,))
    extraction = extracted[0] if extracted else None
    return jsonify(
        **doc,
        text=sources[0]["text"] if sources else "",
        source_error=sources[0]["error"] if sources else "Source text not ingested yet",
        extraction=extraction,
        quotes=(extraction or {}).get("record", {}).get("quotes", {})
        if (extraction or {}).get("record")
        else {},
        issues=collection(
            "SELECT * FROM extraction_issues WHERE document_id = ? ORDER BY id", (doc_id,)
        ),
    )


@bp.get("/review")
def reviews():
    status = selection("status", {"open", "resolved"})
    return jsonify(
        items=collection(
            "SELECT * FROM review_queue WHERE (CAST(? AS TEXT) IS NULL OR status = ?) ORDER BY id",
            (status, status),
        )
    )


@bp.post("/review/<review_id>/resolve")
def resolve(review_id):
    local_only()
    repo = db(write=True)
    if not repo.execute(
        "UPDATE review_queue SET status = ? WHERE id = ?", ("resolved", review_id)
    ).rowcount:
        raise NotFound("No such review item")
    repo.commit()
    return jsonify(id=review_id, status="resolved")


@bp.get("/recon")
def recon():
    outlet, start, end = recon_filters()
    payouts = collection(
        "SELECT p.*, (SELECT SUM(l.receipt_cents) FROM ledger_lines l "
        "WHERE l.source = 'BANK' AND l.reference = p.id) AS bank_paid_cents "
        "FROM payout_statements p WHERE (CAST(? AS TEXT) IS NULL OR p.outlet = ?) "
        "AND (CAST(? AS TEXT) IS NULL OR p.week_start = ?) "
        "ORDER BY p.week_start, p.outlet, p.platform",
        (outlet, outlet, start, start),
    )
    for row in payouts:
        cents = row.pop("bank_paid_cents")
        row["bank_paid"] = str(Decimal(cents) / 100) if cents is not None else None
    return jsonify(
        exceptions=exceptions(outlet, start, end),
        payouts=payouts,
        outlets=collection("SELECT id, name FROM outlets ORDER BY id"),
    )


@bp.get("/exceptions")
def exception_list():
    outlet, start, end = recon_filters()
    kinds = {r["kind"] for r in db().rows("SELECT DISTINCT kind FROM exceptions")}
    kind = selection("kind", kinds)
    return jsonify(items=exceptions(outlet, start, end, kind))


@bp.get("/names")
def names():
    return jsonify(
        items=collection("SELECT * FROM name_map ORDER BY entity_type, variant"),
        readonly=current_app.config["APP_ENV"] != "local",
        approval_message=READ_ONLY,
    )


@bp.post("/names/<entity_type>/approve")
@bp.post("/names/<entity_type>/reject")
def name_decision(entity_type):
    local_only()
    values = body()
    if set(values) != {"variant", "canonical_id"} or any(
        not isinstance(v, str) or not v or len(v) > 500 for v in values.values()
    ):
        raise BadRequest("Provide variant and canonical_id strings")
    repo = db(write=True)
    try:
        approve(
            repo,
            entity_type,
            values["variant"],
            values["canonical_id"],
            approved=request.path.endswith("/approve"),
        )
    except ValueError as exc:
        raise BadRequest(str(exc)) from exc
    repo.commit()
    return jsonify(status="approved" if request.path.endswith("/approve") else "rejected")


@bp.get("/einvoice")
def einvoice():
    return jsonify(items=collection("SELECT * FROM einvoice_findings ORDER BY rule, record_id"))


@bp.get("/ask/recorded")
def recorded():
    stored = {r["id"]: r for r in collection("SELECT * FROM ask_log ORDER BY id")}
    evaluation = artifact("RESULTS_PATH").get("ask", {})
    evaluated = {q["id"]: q for q in evaluation.get("questions", [])}
    gold = artifact("ASK_GOLD_PATH")
    rows = []
    for case in gold["questions"]:
        scored = evaluated.get(case["id"], {})
        result = stored.get(case["id"]) or scored.get("result")
        # Stored rows also carry their own id and question; the evaluated answer does not.
        answer = {k: v for k, v in (result or {}).items() if k not in {"id", "question"}}
        same_snapshot = answer == scored.get("result")
        state = scored.get("state", "not run")
        if result and not same_snapshot:
            state = (
                "not run"
                if result.get("not_run")
                else ("refused" if result.get("refused") else "recorded (not evaluated)")
            )
        rows.append(
            {
                **case,
                "result": result,
                "state": state,
                "evaluation_state": scored.get("state", "not run"),
                "answer_source": "ask_log" if case["id"] in stored else "evaluation_snapshot",
            }
        )
    return jsonify(
        items=rows, evaluation=evaluation, basis=gold.get("basis"), coverage=gold.get("coverage")
    )


@bp.get("/results")
def results():
    return jsonify(artifact("RESULTS_PATH"))


@bp.get("/runs")
def runs():
    return jsonify(items=collection("SELECT * FROM run_log ORDER BY id DESC"))


@bp.get("/drafts")
def drafts():
    return jsonify(items=collection("SELECT * FROM drafts ORDER BY id"), sent=False)


def latest_brief_data():
    rows = collection(
        "SELECT b.* FROM briefs b JOIN run_log r ON b.id = CAST(r.id AS TEXT) "
        "ORDER BY r.id DESC LIMIT 1"
    )
    return rows[0] if rows else None


@bp.get("/brief/latest")
def brief():
    return jsonify(brief=latest_brief_data())


EXPORTS = {
    "documents": "SELECT id, kind, status, layout, business_date FROM documents ORDER BY id",
    "exceptions": "SELECT kind, outlet, date_or_week, amount, message FROM exceptions ORDER BY id",
    "names": "SELECT entity_type, variant, canonical_id, decision, status FROM name_map "
    "ORDER BY entity_type, variant",
    "einvoice": "SELECT record_type, record_id, rule, message FROM einvoice_findings ORDER BY id",
    "review": "SELECT id, document_id, reason, status FROM review_queue ORDER BY id",
}


def safe_cell(value):
    text = "" if value is None else str(value)
    return "'" + text if text.lstrip().startswith(("=", "+", "-", "@")) else text


@bp.get("/export/<name>.csv")
def export(name):
    if name not in EXPORTS:
        raise NotFound("Unknown export")
    cursor = db().execute(EXPORTS[name])
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow([c[0] for c in cursor.description])
    writer.writerows([[safe_cell(v) for v in row] for row in cursor.fetchall()])
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="{name}.csv"',
        },
    )


@bp.post("/check")
def check():
    values = body()
    if "doc_id" in values:
        if set(values) != {"doc_id"} or not isinstance(values["doc_id"], str):
            raise BadRequest("Provide doc_id alone")
        sample = one("SELECT * FROM extractions WHERE document_id = ?", (values["doc_id"],))
        if not sample.get("record") or "supplier_name" not in sample["record"]:
            raise BadRequest("This sample has no recorded invoice extraction")
        raw = sample["record"]
        text = db().rows(
            "SELECT text FROM document_sources WHERE document_id = ?", (values["doc_id"],)
        )
        text = text[0]["text"] if text else None
    elif "invoice" in values:
        if set(values) - {"invoice", "text"} or not isinstance(values["invoice"], dict):
            raise BadRequest("Provide invoice JSON and optional source text")
        raw, text = values["invoice"], values.get("text")
    else:
        raw, text = values, None
    if text is not None and not isinstance(text, str):
        raise BadRequest("Source text must be a string")
    try:
        record = InvoiceRecord.model_validate(raw)
        matcher = Tools(db()).matcher
        candidate = Invoice(
            "checker-input",
            record,
            matcher.supplier_identity(record.supplier_name, record.supplier_trn),
            matcher.resolve("outlet", record.deliver_to),
        )
        existing = []
        for row in db().rows("SELECT document_id, payload FROM extractions WHERE not_run = 0"):
            if row["document_id"] == values.get("doc_id"):
                continue
            r = ExtractionResult.model_validate_json(row["payload"]).record
            if isinstance(r, InvoiceRecord):
                existing.append(
                    Invoice(
                        row["document_id"],
                        r,
                        matcher.supplier_identity(r.supplier_name, r.supplier_trn),
                    )
                )
        issues = validate(record, text) if text is not None else invoice_arithmetic(record)
        findings = [
            f.model_dump()
            for f in check_invoices([*existing, candidate])
            if "checker-input" in f.record_id.split("|")
        ]
        duplicates = [
            e.model_dump(mode="json")
            for e in check_documents([*existing, candidate])
            if e.kind == "DUPLICATE_INVOICE" and "checker-input" in e.refs.values()
        ]
    except (ValidationError, DecimalException, ValueError, TypeError) as exc:
        raise BadRequest(
            "Invalid invoice fields. Use ISO dates and finite decimal amounts."
        ) from exc
    return jsonify(
        issues=[i.model_dump() for i in issues],
        findings=findings,
        duplicates=duplicates,
        evidence_checked=text is not None,
        writes=0,
    )
