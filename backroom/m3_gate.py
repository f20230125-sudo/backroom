"""Oracle-only engine checks; these must never be presented as extraction accuracy."""

from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from backroom.db import connect, migrate
from backroom.extract.types import ExtractionResult, PayoutRecord
from backroom.inputs import manifest
from backroom.models import InvoiceRecord
from backroom.names.match import Entity, Matcher, approve, approved_names, masters, propose
from backroom.pipeline import load_masters, run_pipeline
from eval.m3 import evaluate_m3


def oracle_results(source):
    """Ground-truth-derived records, used only to test the engines independently."""
    out = {}
    for doc in source["documents"]:
        model = PayoutRecord if doc["kind"] == "payout_statement" else InvoiceRecord
        record = None if doc["kind"] == "email_other" else model.model_validate(doc["truth"])
        out[doc["doc_id"]] = ExtractionResult(
            record=record,
            status="accepted",
            prompt_version="ORACLE_ground_truth_not_extraction",
            classification=doc["kind"],
        )
    return out


def approve_oracle_names(repo, source):
    """Simulate explicit human approvals using the answer key, only in gate databases."""
    load_masters(repo, source)
    matcher = Matcher(masters(source))
    for variant in source["name_variants"]:
        propose(repo, matcher.match(variant["entity_type"], variant["variant"]))
        approve(repo, variant["entity_type"], variant["variant"], variant["canonical_id"])


def identity_evidence_checks():
    """Guard the identity policy with independent examples, without extraction truth."""
    number = "123456789012303"
    matcher = Matcher(
        [
            Entity("supplier", "P", ("Harbour Produce Trading LLC",), number),
            Entity("supplier", "F", ("Harbour Fish Trading LLC",), number),
            Entity("item", "T", ("Tomato Roma",)),
            Entity("item", "B", ("Kraft Bowl",)),
            Entity("item", "L", ("Kraft Bowl Lid",)),
        ]
    )
    if matcher.supplier_identity("Harbour Produce", number) != "P" or (
        matcher.supplier_identity("Harbour Fish", number) != "F"
    ):
        raise ValueError("Shared TRN must defer to distinct name identities")
    if matcher.supplier_identity("Harbour", number) != "printed:harbour":
        raise ValueError("Shared TRN cannot resolve an ambiguous name")
    unique = Matcher([Entity("supplier", "P", ("Harbour Produce",), number)])
    if unique.supplier_identity("Different trading name", number) != "P":
        raise ValueError("Unique valid TRN must resolve a different printed supplier name")
    if matcher.match("item", "ROMA TOMATOES (5kg crate) - CHILLED").decision != "auto_match":
        raise ValueError("Item pack annotations should not hide exact identity")
    review = matcher.match("supplier", "Harbour")
    if review.decision != "review" or not review.suggested_id or review.score is None:
        raise ValueError("Ambiguous reviews must carry a scored suggestion")
    if matcher.match("item", "Kraft Bowl").canonical_id != "B":
        raise ValueError("An item and its lid must remain distinct")
    print("PASS m3.1 unique/shared TRN identities, item annotations and scored ambiguous reviews")


def checks(data_root):
    identity_evidence_checks()
    source = manifest(data_root)
    results = oracle_results(source)
    temp_root = Path(".local/temp")
    temp_root.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=temp_root, prefix="m3-") as directory:
        snapshots = []
        for index in range(2):
            with connect(Path(directory) / f"run-{index}.sqlite") as repo:
                migrate(repo)
                first = repo.snapshot(include_runs=True)
                migrate(repo)
                if first != repo.snapshot(include_runs=True):
                    raise ValueError("Second migration changed database contents")
                approve_oracle_names(repo, source)
                run_pipeline(
                    repo=repo,
                    data_root=data_root,
                    results=results,
                    clock=lambda: datetime(2026, 10, 27, tzinfo=UTC),
                    timer=lambda: 0,
                )
                snapshots.append(repo.snapshot(include_runs=True))
                if index == 0:
                    matcher = Matcher(masters(source), approved_names(repo))
                    scores = evaluate_m3(source, data_root, results=results, matcher=matcher)
        if snapshots[0] != snapshots[1]:
            raise ValueError("Independent full runs differ (including fixed-clock run logs)")
        print("PASS m3 empty SQLite migrations twice")
        print("PASS m3 two empty-to-full ORACLE runs identical, including fixed-clock audit rows")
        totals = scores["reconciliation"]["totals"]
        print(f"  ORACLE engine check, NOT extraction results: {totals}")
        for kind, row in scores["reconciliation"]["by_kind"].items():
            print(f"  {kind}: {row}")
        if totals != {"found": 31, "missed": 0, "not_run": 0, "false_alarms": 0}:
            raise ValueError("Oracle engines must find exactly 31 problems with zero false alarms")
        print("PASS m3 ORACLE engines find all 31 problems, zero false alarms")
        names = scores["names"]
        print(
            f"  names precision={names['precision']} recall={names['recall']} "
            f"F1={names['f1']} review_share={names['review_share']}"
        )
        if (
            names["precision"] is None
            or names["precision"] < 0.95
            or len(names["hard_negatives"]) != 5
            or any(row["false_merge"] for row in names["hard_negatives"])
        ):
            raise ValueError("Name precision or hard-negative check failed")
        print("PASS m3 names precision >= 0.95; all five hard negatives avoid auto-merge")
        return scores
