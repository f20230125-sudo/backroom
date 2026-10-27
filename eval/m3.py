"""Score identities and source-linked detections; truth is used only as the answer key."""

from collections import Counter
from pathlib import PurePosixPath

from backroom.einvoice.rules import check_invoices
from backroom.inputs import feeds, load_results
from backroom.names.match import Entity, Matcher, masters
from backroom.pipeline import invoice_inputs, payout_inputs
from backroom.recon.engine import reconcile


def name_metrics(source):
    entities = masters(source)
    matcher = Matcher(entities)
    entity_types = {entity.canonical_id: entity.entity_type for entity in entities}
    tp = fp = review = predicted = 0
    rows = []
    variants = source.get("name_variants", [])
    negatives = source.get("hard_negatives", [])
    for row in variants:
        proposal = matcher.match(row["entity_type"], row["variant"])
        merged = proposal.decision == "auto_match" or proposal.recommendation is True
        correct = merged and proposal.canonical_id == row["canonical_id"]
        tp += int(correct)
        fp += int(merged and not correct)
        predicted += int(merged)
        review += int(proposal.decision == "review")
        rows.append(
            {
                "variant": row["variant"],
                "expected": row["canonical_id"],
                "predicted": proposal.canonical_id if merged else None,
                "decision": proposal.decision,
            }
        )
    negative_rows = []
    for row in negatives:
        # Challenge both directions without offering the correct entity as an easy exact hit.
        merged = auto = False
        for left, left_id, right, right_id in (
            (row["left"], row["left_id"], row["right"], row["right_id"]),
            (row["right"], row["right_id"], row["left"], row["left_id"]),
        ):
            candidate = Matcher([Entity(entity_types[right_id], right_id, (right,))]).match(
                entity_types[left_id], left
            )
            auto |= candidate.decision == "auto_match"
            merged |= candidate.decision == "auto_match" or candidate.recommendation is True
        fp += int(merged)
        predicted += int(merged)
        negative_rows.append({**row, "false_merge": merged, "auto_merge": auto})
    precision = tp / predicted if predicted else None
    recall = tp / len(variants) if variants else None
    f1 = 2 * precision * recall / (precision + recall) if precision and recall else 0
    return {
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "true_positives": tp,
        "false_positives": fp,
        "false_negatives": len(variants) - tp,
        "variants": len(variants),
        "hard_negatives": negative_rows,
        "review_share": review / len(variants) if variants else None,
        "documents": rows,
    }


def expected_key(problem):
    refs = problem["refs"]
    if "duplicate" in refs:
        return "|".join(sorted((refs["original"]["doc_id"], refs["duplicate"]["doc_id"])))
    if "statement" in refs:
        if "pos" in refs:
            path = PurePosixPath(refs["pos"]["path"])
            channel = "PLATFORM_" + refs["statement"]["doc_id"].split("-")[1]
            return f"{refs['statement']['doc_id']}|{path.parent.name}|{path.stem}|{channel}"
        return refs["statement"]["doc_id"]
    if "invoice" in refs:
        return refs["invoice"]["doc_id"]
    if "pos" in refs:
        path = PurePosixPath(refs["pos"]["path"])
        return f"{path.parent.name}|{path.stem}"
    return "|".join(sorted(ref["doc_id"] for ref in refs.values() if ref.get("doc_id")))


def detections(exceptions, findings):
    for exc in exceptions:
        if "duplicate" in exc.refs:
            yield (
                exc.kind,
                "|".join(sorted((exc.refs["original"], exc.refs["duplicate"]))),
                exc.message,
            )
            continue
        if "statement" in exc.refs and "pos" in exc.refs:
            yield exc.kind, exc.refs["statement"] + "|" + exc.refs["pos"], exc.message
            continue
        key = next(
            (exc.refs[k] for k in ("duplicate", "statement", "invoice", "pos") if k in exc.refs),
            None,
        )
        yield exc.kind, key, exc.message
    for finding in findings:
        kind = {"IBR-132-AE": "TRN_INVALID", "IBR-128-AE": "EMIRATE_INVALID"}.get(
            finding.rule, finding.rule
        )
        yield kind, finding.record_id, finding.message


def problem_metrics(source, results, exceptions, findings):
    expected = {
        (p["expected_exception_kind"], expected_key(p)): p for p in source.get("problems", [])
    }
    predicted = {(kind, key): message for kind, key, message in detections(exceptions, findings)}
    kinds = sorted({key[0] for key in expected} | {key[0] for key in predicted})
    state = {}
    for key, problem in expected.items():
        required = [
            r["doc_id"]
            for r in problem["refs"].values()
            if r.get("doc_id") and not r["doc_id"].startswith("PAY-")
        ]
        unavailable = any(
            doc_id not in results or results[doc_id].not_run or results[doc_id].record is None
            for doc_id in required
        )
        # CSV payout checks run directly from the parsed feeds; missing PDF extraction is explicit.
        statement = problem["refs"].get("statement", {}).get("doc_id")
        if statement:
            doc = next((d for d in source["documents"] if d["doc_id"] == statement), None)
            if doc and doc["layout"] == "PDF_C":
                unavailable |= (
                    statement not in results
                    or results[statement].not_run
                    or results[statement].record is None
                )
        state[key] = "not_run" if unavailable else ("found" if key in predicted else "missed")
    by_kind = {}
    for kind in kinds:
        counts = Counter(value for key, value in state.items() if key[0] == kind)
        by_kind[kind] = {
            **{name: counts[name] for name in ("found", "missed", "not_run")},
            "false_alarms": sum(key[0] == kind and key not in expected for key in predicted),
        }
    return {
        "by_kind": by_kind,
        "totals": {
            name: sum(row[name] for row in by_kind.values())
            for name in ("found", "missed", "not_run", "false_alarms")
        },
        "problems": [
            {"id": p["id"], "kind": key[0], "ref": key[1], "state": state[key]}
            for key, p in expected.items()
        ],
        "detections": [
            {"kind": kind, "ref": key, "message": value}
            for (kind, key), value in sorted(predicted.items())
        ],
    }


def evaluate_m3(source, data_root, *, results=None, matcher=None, llm=None):
    if not source.get("outlets"):
        return {
            "names": {"state": "not_run", "reason": "No canonical masters"},
            "reconciliation": {"state": "not_run", "reason": "No feeds or answer key"},
        }
    results = load_results(data_root, source["documents"], results=results, llm=llm)
    matcher = matcher if matcher is not None else Matcher(masters(source))
    pos, ledger = feeds(data_root)
    invoices = invoice_inputs(results, matcher)
    statements = payout_inputs(source["documents"], results, data_root)
    exceptions = reconcile(
        statements=statements, pos=pos, ledger=ledger, invoices=invoices, matcher=matcher
    )
    findings = check_invoices(invoices)
    return {
        "names": name_metrics(source),
        "reconciliation": problem_metrics(source, results, exceptions, findings),
    }
