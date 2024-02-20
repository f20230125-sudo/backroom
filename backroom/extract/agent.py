"""One evidence-checked extraction and at most one repair; source errors are immutable."""

import json
from pathlib import Path

from pydantic import ValidationError

from backroom import DATA, settings
from backroom.extract.classify import classify
from backroom.extract.payouts_csv import parse_csv
from backroom.extract.schema import output_schema
from backroom.extract.text import DocumentError, DocumentText, read_document
from backroom.extract.types import ExtractionResult, Issue, PayoutRecord
from backroom.extract.validate import payout_arithmetic, scalar_fields, validate
from backroom.llm.providers import ProviderError
from backroom.llm.router import AllEndpointsExhausted
from backroom.models import Document, InvoiceRecord

PROMPTS = Path(__file__).parent / "prompts"


class _Meter:
    def __init__(self, llm):
        self.llm = llm
        self.hits = 0
        self.calls = 0
        self.model = None

    def complete(self, *args, **kwargs):
        before = getattr(self.llm, "calls_made", 0)
        try:
            response = self.llm.complete(*args, **kwargs)
        finally:
            self.calls += getattr(self.llm, "calls_made", before) - before
        self.model = response.model
        self.hits += int(response.cached)
        if not response.cached and not hasattr(self.llm, "calls_made"):
            self.calls += 1
        return response


def _parse(text: str, model):
    raw = json.loads(text)

    # All schema numbers here are Decimal facts, explicitly requested as strings.
    def reject_numbers(value):
        if type(value) in {float, int}:
            raise ValueError("Decimal facts must be JSON strings")
        if isinstance(value, dict):
            for child in value.values():
                reject_numbers(child)
        if isinstance(value, list):
            for child in value:
                reject_numbers(child)

    reject_numbers(raw)
    return model.model_validate(raw)


def extract(
    doc: Document | DocumentText | Path | str,
    *,
    llm=None,
    offline: bool = True,
    data_root: Path = DATA,
) -> ExtractionResult:
    """Only source bytes enter classification/extraction; manifest truth is never consulted."""
    result = ExtractionResult(prompt_version="invoice_v1")
    llm = llm if llm is not None else settings.build_llm(offline=offline)
    meter = _Meter(llm)
    try:
        if isinstance(doc, DocumentText):
            source = doc
        else:
            path = data_root / doc.path if isinstance(doc, Document) else Path(doc)
            source = read_document(path)
        classification = classify(source, llm=meter)
        result.classification = classification.kind
        if classification.confidence < 0.8:
            result.issues = [
                Issue(code="CLASSIFICATION_UNCERTAIN", field="kind", message=classification.reason)
            ]
            return result
        if classification.kind == "email_other":
            result.status = "accepted"
            result.prompt_version = "classification_v1"
            return result
        if source.media_type == "text/csv":
            record = parse_csv(source.text)
            result.record = record
            result.prompt_version = "payout_csv_v1"
            result.issues = payout_arithmetic(record)
            for issue in result.issues:
                issue.document_problem = True
            result.status = "document_problem" if result.issues else "accepted"
            return result
        payout = classification.kind == "payout_statement"
        result.prompt_version = "payout_v1" if payout else "invoice_v1"
        model = PayoutRecord if payout else InvoiceRecord
        prompt = (PROMPTS / f"{result.prompt_version}.md").read_text(encoding="utf-8")
        # Role boundaries, not XML parsing, establish authority. Escape literal delimiters
        # without removing any document text used by evidence validation.
        document = source.text.replace("<document>", "&lt;document&gt;").replace(
            "</document>", "&lt;/document&gt;"
        )
        messages = [
            {"role": "system", "content": prompt},
            {"role": "user", "content": f"<document>\n{document}\n</document>"},
        ]
        findings = []
        locked = {}
        for attempt in range(2):
            result.attempts += 1
            response = meter.complete(
                messages, schema=output_schema(model), temperature=0, max_tokens=12000
            )
            try:
                record = _parse(response.text, model)
                issues = validate(record, source.text)
                if locked and any(
                    scalar_fields(record).get(key) != value for key, value in locked.items()
                ):
                    issues.append(
                        Issue(
                            code="SUPPORTED_FACT_CHANGED",
                            field="record",
                            message="Repair changed source-backed facts of a document finding",
                        )
                    )
                result.record = record
            except (ValueError, ValidationError) as exc:
                # Do not echo hostile output or Pydantic's input values into repair instructions.
                issues = [
                    Issue(
                        code="OUTPUT_INVALID",
                        field="record",
                        message=f"Output must match the schema ({type(exc).__name__})",
                    )
                ]
                result.record = None
            result.issues = issues + [finding for finding in findings if finding not in issues]
            retryable = [issue for issue in result.issues if not issue.document_problem]
            if not retryable:
                result.status = "document_problem" if result.issues else "accepted"
                break
            if attempt == 0:
                findings = [issue for issue in issues if issue.document_problem]
                if findings and result.record:
                    bad = {issue.field for issue in issues if not issue.document_problem}
                    locked = {
                        key: value
                        for key, value in scalar_fields(result.record).items()
                        if key not in bad
                    }
                messages.extend(
                    [
                        {"role": "assistant", "content": response.text},
                        {
                            "role": "user",
                            "content": "Repair only unsupported or missing facts. "
                            "Do not correct source arithmetic or source-backed facts. Issues:\n"
                            + json.dumps(
                                [issue.model_dump() for issue in result.issues], sort_keys=True
                            ),
                        },
                    ]
                )
        return result
    except settings.CacheMiss:
        result.not_run = True
        result.status = "review"
        result.issues.append(
            Issue(
                code="CACHE_MISS",
                field="record",
                message="Not run: no recording for this request; run --record with a key",
            )
        )
        return result
    except (DocumentError, OSError, ValueError, ProviderError, AllEndpointsExhausted) as exc:
        result.status = "review"
        result.issues.append(
            Issue(
                code="EXTRACTION_FAILED",
                field="record",
                message=str(exc)
                if isinstance(exc, DocumentError)
                else f"Cannot extract ({type(exc).__name__})",
            )
        )
        return result
    finally:
        result.cache_hits = meter.hits
        result.model_calls = meter.calls
        result.cached = meter.hits > 0 and meter.calls == 0
        result.model = meter.model
