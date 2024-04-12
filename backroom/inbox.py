"""The operator pipeline consumes signed ingress; the web function never extracts mail."""

import base64
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from backroom import ROOT
from backroom.db.repo import dumps
from backroom.extract.agent import extract
from backroom.extract.text import DocumentError, read_document
from backroom.extract.types import ExtractionResult, Issue
from backroom.inputs import offline_llm


def consume_inbox(repo, business_date, *, llm=None):
    fresh = {}
    present = {r["id"] for r in repo.rows("SELECT id FROM documents")}
    pending = [
        (row, json.loads(row["payload"]))
        for row in repo.rows("SELECT * FROM inbox ORDER BY id")
        if json.loads(row["payload"]).get("status") == "pending"
        or "inbox-" + row["id"] not in present
    ]
    if not pending:
        return fresh
    owned = llm is None
    llm = llm if llm is not None else offline_llm()
    temporary = ROOT / ".local/temp"
    temporary.mkdir(parents=True, exist_ok=True)
    try:
        for row, payload in pending:
            doc_id = "inbox-" + row["id"]
            with TemporaryDirectory(prefix="codex-inbox-", dir=temporary) as directory:
                path = Path(directory) / "message.eml"
                path.write_bytes(base64.b64decode(payload["raw_base64"], validate=True))
                try:
                    source = read_document(path)
                    result = extract(path, llm=llm, offline=True)
                    text, error = source.text, None
                except (DocumentError, ValueError):
                    text, error = "", "Email could not be read; operator review required"
                    result = ExtractionResult(
                        prompt_version="inbox",
                        status="review",
                        issues=[Issue(code="SOURCE_UNREADABLE", field="record", message=error)],
                    )
            status = "not_run" if result.not_run else result.status
            repo.upsert(
                "documents",
                {
                    "id": doc_id,
                    "path": "inbox/" + row["id"] + ".eml",
                    "kind": result.classification or "email_other",
                    "layout": "INBOX",
                    "business_date": business_date,
                    "status": status,
                    "payload": dumps({"held_out": False, "origin": "signed_inbox"}),
                },
            )
            repo.upsert(
                "document_sources",
                {
                    "document_id": doc_id,
                    "text": text,
                    "media_type": "message/rfc822",
                    "error": error,
                },
                conflict=("document_id",),
            )
            repo.upsert(
                "extractions",
                {
                    "document_id": doc_id,
                    "status": status,
                    "prompt_version": result.prompt_version,
                    "model_calls": result.model_calls,
                    "cache_hits": result.cache_hits,
                    "not_run": int(result.not_run),
                    "payload": result.model_dump_json(),
                },
                conflict=("document_id",),
            )
            for index, issue in enumerate(result.issues):
                repo.upsert(
                    "extraction_issues",
                    {
                        "id": f"{doc_id}:{index}",
                        "document_id": doc_id,
                        "code": issue.code,
                        "field": issue.field,
                        "payload": issue.model_dump_json(),
                    },
                )
            repo.execute(
                "UPDATE inbox SET payload = ? WHERE id = ?",
                (
                    dumps({**payload, "status": "processed", "document_id": doc_id}),
                    row["id"],
                ),
            )
            fresh[doc_id] = result
    finally:
        if owned:
            llm.store.close()
    return fresh
