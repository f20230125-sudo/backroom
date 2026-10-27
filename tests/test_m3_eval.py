"""Detection scoring must fail when extracted facts differ from the answer key."""

from decimal import Decimal

from backroom import DATA
from backroom.extract.types import ExtractionResult
from backroom.inputs import manifest
from backroom.m3_gate import oracle_results
from backroom.names.match import Matcher, masters
from backroom.names.normalise import exact_key
from eval.m3 import evaluate_m3, name_metrics


def approved_matcher(source):
    return Matcher(
        masters(source),
        {
            (v["entity_type"], exact_key(v["variant"])): v["canonical_id"]
            for v in source["name_variants"]
        },
    )


def test_detection_uses_extracted_values_not_document_truth():
    source = manifest(DATA)
    records = oracle_results(source)
    # Repair an actual plant only in extracted output: it must become a miss.
    result = records["DOC-005"].model_copy(deep=True)
    result.record.vat -= Decimal("17")
    result.record.total -= Decimal("17")
    records["DOC-005"] = result
    # Add an unplanted bad tax identifier: it must become a false alarm.
    result = records["DOC-001"].model_copy(deep=True)
    result.record.supplier_trn = "BAD"
    records["DOC-001"] = result
    # Missing recordings cannot be scored as either a successful detection or a miss.
    records["DOC-009"] = ExtractionResult(prompt_version="not_run", not_run=True)
    scores = evaluate_m3(source, DATA, results=records, matcher=approved_matcher(source))
    by_kind = scores["reconciliation"]["by_kind"]
    assert by_kind["VAT_WRONG"] == {"found": 2, "missed": 1, "not_run": 0, "false_alarms": 0}
    assert by_kind["TRN_INVALID"] == {"found": 1, "missed": 0, "not_run": 1, "false_alarms": 1}


def test_no_document_records_means_not_run_while_parsed_feed_checks_still_run():
    source = manifest(DATA)
    scores = evaluate_m3(source, DATA, results={})["reconciliation"]
    for kind in (
        "VAT_WRONG",
        "LINES_NOT_SUM",
        "TRN_INVALID",
        "DUPLICATE_INVOICE",
        "CREDIT_NOTE_UNLINKED",
        "MISSING_FIELD",
        "TRN_SHARED",
    ):
        row = scores["by_kind"][kind]
        assert row["found"] == row["missed"] == row["false_alarms"] == 0
        assert row["not_run"] > 0
    assert scores["by_kind"]["PAYOUT_SHORT"]["found"] == 4
    assert scores["by_kind"]["COMMISSION_OVER_CONTRACT"]["found"] == 3
    assert scores["by_kind"]["REFUND_NOT_IN_POS"]["found"] == 3
    assert scores["by_kind"]["POS_LEDGER_MISMATCH"]["found"] == 3


def test_names_metrics_include_all_five_hard_negatives_and_reviews():
    scores = name_metrics(manifest(DATA))
    assert scores["precision"] >= 0.95 and scores["recall"] > 0
    assert 0 < scores["review_share"] < 1
    assert len(scores["hard_negatives"]) == 5
    assert not any(row["false_merge"] or row["auto_merge"] for row in scores["hard_negatives"])


def test_m3_gate_failure_is_reported(monkeypatch, capsys):
    from backroom import verify

    monkeypatch.setattr(verify, "m2", lambda _: True)

    def fail(_):
        raise ValueError("corrupt engine")

    monkeypatch.setattr("backroom.m3_gate.checks", fail)
    assert not verify.m3()
    assert "FAIL m3: corrupt engine" in capsys.readouterr().out
