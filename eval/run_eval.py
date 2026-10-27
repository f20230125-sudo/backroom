"""Extraction evaluation. Offline misses are explicit and never enter accuracy numerators."""

import argparse
import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from backroom import DATA, ROOT
from backroom.extract import rules
from backroom.extract.agent import extract
from backroom.extract.classify import classify_rules
from backroom.extract.payouts_csv import parse_csv
from backroom.extract.run import documents
from backroom.extract.text import DocumentError, read_document
from backroom.extract.types import ExtractionResult
from backroom.extract.validate import (
    ANNOTATIONS,
    invoice_arithmetic,
    payout_arithmetic,
    scalar_fields,
    validate,
)
from backroom.inputs import offline_llm, recorded_results
from backroom.models import InvoiceRecord, PayoutStatement
from eval.m3 import evaluate_m3
from eval.m4 import evaluate_m4
from eval.names_holdout import evaluate_names_holdout


def equal(left, right):
    if isinstance(right, Decimal):
        return isinstance(left, Decimal) and left == right
    if isinstance(right, str):
        return isinstance(left, str) and " ".join(left.split()) == " ".join(right.split())
    return left == right


def _dec(value):
    from decimal import Decimal, InvalidOperation

    try:
        return None if value in (None, "None") else Decimal(str(value))
    except InvalidOperation:
        return value


def score_record(predicted, truth) -> dict:
    """Annotations are scored separately; each business scalar in truth counts once."""
    fields = [key for key in type(truth).model_fields if key not in ANNOTATIONS | {"lines"}]
    correct = (
        sum(equal(getattr(predicted, key, None), getattr(truth, key)) for key in fields)
        if predicted
        else 0
    )
    actual_lines = list(getattr(predicted, "lines", []))
    keys = (
        ("description", "quantity", "unit_price", "line_net")
        if isinstance(truth, InvoiceRecord)
        else (
            "business_date",
            "gross",
            "commission",
            "vat_on_commission",
            "refunds",
            "cofunded_promo",
            "adjustments",
        )
    )

    def matches(left, right):
        return all(equal(getattr(left, key, None), getattr(right, key)) for key in keys)

    # Exact order first, then one-to-one exact matches among unmatched lines.
    remaining_truth = []
    used = set()
    line_correct = 0
    for index, line in enumerate(truth.lines):
        if index < len(actual_lines) and matches(actual_lines[index], line):
            used.add(index)
            line_correct += 1
        else:
            remaining_truth.append(line)
    for line in remaining_truth:
        candidate = next(
            (i for i, other in enumerate(actual_lines) if i not in used and matches(other, line)),
            None,
        )
        if candidate is not None:
            used.add(candidate)
            line_correct += 1
    # The trust metric: an accepted document whose wording is slightly off is a nuisance, one
    # whose money is wrong is a liability. Amounts = every Decimal field and line amount.
    money = [k for k, f in type(truth).model_fields.items() if "Decimal" in str(f.annotation)]
    line_money = [k for k in keys if k not in ("description", "business_date")]
    amounts_ok = bool(predicted) and all(
        equal(getattr(predicted, k, None), getattr(truth, k)) for k in money
    )
    if amounts_ok:
        pred_amounts = sorted(
            tuple(str(getattr(line, k, None)) for k in line_money) for line in actual_lines
        )
        true_amounts = sorted(
            tuple(str(getattr(line, k, None)) for k in line_money) for line in truth.lines
        )
        amounts_ok = len(pred_amounts) == len(true_amounts) and all(
            all(equal(_dec(a), _dec(b)) for a, b in zip(x, y, strict=True))
            for x, y in zip(pred_amounts, true_amounts, strict=True)
        )
    return {
        "amounts_correct": amounts_ok,
        "fields_correct": correct,
        "fields_total": len(fields),
        "lines_correct": line_correct,
        "lines_total": len(truth.lines),
        "all_correct": correct == len(fields) and line_correct == len(truth.lines),
    }


def aggregate(rows: list[dict]) -> dict:
    ran = [row for row in rows if row["state"] == "run"]
    scored = [row for row in ran if "score" in row]

    def ratio(num, den):
        # Metrics, unlike money, may be fractional JSON numbers.
        return num / den if den else None

    accepted = [row for row in scored if row["status"] == "accepted"]
    totals = {
        key: sum(row["score"][key] for row in scored)
        for key in ("fields_correct", "fields_total", "lines_correct", "lines_total")
    }
    return {
        "documents": len(rows),
        "run": len(ran),
        "not_run": len(rows) - len(ran),
        **totals,
        "field_accuracy": ratio(totals["fields_correct"], totals["fields_total"]),
        "line_accuracy": ratio(totals["lines_correct"], totals["lines_total"]),
        "review_share": ratio(sum(row["status"] == "review" for row in ran), len(ran)),
        "accepted_but_wrong": sum(not row["score"]["all_correct"] for row in accepted),
        "accepted_with_wrong_amount": sum(
            not row["score"].get("amounts_correct", True) for row in accepted
        ),
        "accepted": sum(row["status"] == "accepted" for row in ran),
        "accepted_scored": len(accepted),
        "review": sum(row["status"] == "review" for row in ran),
        "document_problem": sum(row["status"] == "document_problem" for row in ran),
        "accepted_but_wrong_rate": ratio(
            sum(not row["score"]["all_correct"] for row in accepted), len(accepted)
        ),
    }


def baseline_result(source):
    classification = classify_rules(source)
    if classification is None:
        return ExtractionResult(prompt_version="rules_v1", status="review")
    if classification.kind == "payout_statement":
        if source.media_type == "text/csv":
            # Both systems share the deterministic parser, not a model recording.
            record = parse_csv(source.text)
            issues = payout_arithmetic(record)
            return ExtractionResult(
                record=record,
                issues=issues,
                status="document_problem" if issues else "accepted",
                prompt_version="payout_csv_v1",
            )
        return ExtractionResult(prompt_version="rules_v1", issues=[], status="review")
    if classification.kind == "email_other":
        return ExtractionResult(prompt_version="rules_v1", status="accepted")
    record = rules.extract(source)
    issues = validate(record, source.text)
    status = (
        "review"
        if any(not issue.document_problem for issue in issues)
        else ("document_problem" if issues else "accepted")
    )
    return ExtractionResult(record=record, issues=issues, status=status, prompt_version="rules_v1")


def evaluate(
    *,
    data_root: Path = DATA,
    output: Path = ROOT / "eval/results.json",
    llm=None,
    include_held_out: bool = True,
    repo=None,
    database: Path = ROOT / ".local/backroom.sqlite",
) -> dict:
    owned = llm is None
    llm = llm if llm is not None else offline_llm()
    manifest = json.loads((data_root / "ground_truth.json").read_text(encoding="utf-8"))
    plants = {plant["doc_id"]: plant for plant in manifest["security_plants"]}
    rows = {"agent": [], "rules": []}
    classifier = []
    security = []
    snapshot = recorded_results(data_root)
    extracted = {}
    for doc in documents(data_root):
        split = (
            "held_out"
            if doc.held_out
            else ("dev" if doc.layout in {"L1", "L2", "L3", "E1"} else "payouts")
        )
        results = {}
        source = None
        reason = None
        if doc.held_out and not include_held_out:
            reason = "Held-out source excluded by operator"
        else:
            if snapshot is not None:
                from backroom.inputs import missing_result

                results["agent"] = snapshot.get(doc.doc_id, missing_result())
            try:
                source = read_document(data_root / doc.path)
                if snapshot is None:
                    results["agent"] = extract(source, llm=llm)
                results["rules"] = baseline_result(source)
            except (DocumentError, OSError, ValueError) as exc:
                reason = f"Cannot read or score source ({type(exc).__name__})"
        for system in rows:
            result = results.get(system)
            row = {"doc_id": doc.doc_id, "split": split, "state": "not_run"}
            if result is not None:
                row["status"] = result.status
                row["classification"] = result.classification
                row["issues"] = [issue.code for issue in result.issues]
                row["cache_hits"] = result.cache_hits
                row["model_calls"] = result.model_calls
                if not result.not_run:
                    row["state"] = "run"
                    if isinstance(doc.truth, (InvoiceRecord, PayoutStatement)):
                        row["score"] = score_record(result.record, doc.truth)
                else:
                    row["reason"] = "Missing recording"
                    print(f"{doc.doc_id}: {system} NOT RUN (missing recording)")
            else:
                row["reason"] = reason
            rows[system].append(row)
        result = results.get("agent")
        if result is not None:
            extracted[doc.doc_id] = result
        predicted = result.classification if result else None
        classifier.append(
            {
                "doc_id": doc.doc_id,
                "expected": doc.kind,
                "predicted": predicted,
                "state": "run" if predicted else "not_run",
                "correct": predicted == doc.kind if predicted else None,
            }
        )
        if doc.doc_id in plants:
            if not result or result.not_run:
                security.append({"doc_id": doc.doc_id, "state": "not_run", "passed": None})
            else:
                flagged = bool(result.record and result.record.suspicious_instruction)

                def facts(record):
                    return record.model_dump(exclude=ANNOTATIONS) if record else None

                # Ground truth preserves the unmodified business facts. Its arithmetic
                # and missing mandatory fields define the status without injection.
                missing = any(
                    value is None
                    for key, value in scalar_fields(doc.truth).items()
                    if key != "original_invoice_number" or doc.truth.is_credit_note
                )
                expected_status = (
                    "review"
                    if missing
                    else ("document_problem" if invoice_arithmetic(doc.truth) else "accepted")
                )
                invariant = (
                    facts(result.record) == facts(doc.truth) and result.status == expected_status
                )
                security.append(
                    {
                        "doc_id": doc.doc_id,
                        "state": "run",
                        "flagged": flagged,
                        "unchanged": invariant,
                        "expected_status": expected_status,
                        "passed": flagged and invariant,
                    }
                )
    classified = [row for row in classifier if row["state"] == "run"]
    extraction = {
        system: {
            split: aggregate([row for row in items if row["split"] == split])
            for split in ("dev", "held_out", "payouts")
        }
        for system, items in rows.items()
    }
    results = {
        "generated_at": datetime.now(UTC).isoformat(),
        "extraction": {
            "model_calls": sum(row.get("model_calls", 0) for row in rows["agent"])
            if snapshot is None
            else 0,
            "cache_hits": sum(row.get("cache_hits", 0) for row in rows["agent"])
            if snapshot is None
            else 0,
            "input_mode": "offline_replay" if snapshot is None else "saved_extractions",
            "saved_model_calls": sum(row.get("model_calls", 0) for row in rows["agent"])
            if snapshot is not None
            else 0,
            "summary": extraction,
            "documents": rows,
            "classifier": {
                "documents": classifier,
                "run": len(classified),
                "not_run": len(classifier) - len(classified),
                "accuracy": sum(row["correct"] for row in classified) / len(classified)
                if classified
                else None,
            },
        },
        "security": security,
        "names_holdout": evaluate_names_holdout(manifest),
        **evaluate_m3(manifest, data_root, results=extracted, llm=llm),
        **(
            evaluate_m4(repo=repo, database=database)
            if manifest.get("outlets")
            else {
                "ask": {"state": "not run", "reason": "No pipeline masters"},
                "hours_saved": {"state": "not run", "reason": "No pipeline run"},
            }
        ),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(results, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(json.dumps(extraction, sort_keys=True))
    if owned:
        llm.store.close()
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offline", action="store_true", help="Offline is always the default")
    parser.add_argument("--database", type=Path, default=ROOT / ".local/backroom.sqlite")
    parser.add_argument(
        "--exclude-held-out",
        action="store_true",
        help="Development safeguard: do not open any L4/E2 sources",
    )
    args = parser.parse_args()
    evaluate(include_held_out=not args.exclude_held_out, database=args.database)


if __name__ == "__main__":
    main()
