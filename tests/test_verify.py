"""Corrupt individual facts to prove the gate can reject a superficially plausible corpus."""

from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from backroom import ROOT
from backroom.models import money
from backroom.synth.render_csv import records_csv
from backroom.synth.scenario import build
from backroom.verify import (
    held_out_and_emails,
    invoice_arithmetic,
    m1,
    names,
    payout_arithmetic,
    pos_arithmetic,
    references,
    tax_identifiers,
)


@pytest.fixture(scope="module", autouse=True)
def temp_root():
    (ROOT / ".local/temp").mkdir(parents=True, exist_ok=True)


@pytest.mark.parametrize("kind,field", [("VAT_WRONG", "vat"), ("LINES_NOT_SUM", "net")])
def test_unplanted_invoice_error_fails(kind, field):
    truth = build().truth
    doc = next(doc for doc in truth.documents if doc.doc_id == "DOC-001")
    setattr(doc.truth, field, getattr(doc.truth, field) + Decimal("5.00"))
    if field == "net":
        doc.truth.vat = money(doc.truth.net * Decimal("0.05"))
    doc.truth.total = doc.truth.net + doc.truth.vat
    with pytest.raises(ValueError, match=kind):
        invoice_arithmetic(truth)


def test_repaired_document_with_stale_problem_fails():
    truth = build().truth
    doc = next(doc for doc in truth.documents if doc.doc_id == "DOC-004")
    doc.truth.vat -= Decimal("17.00")
    doc.truth.total = doc.truth.net + doc.truth.vat
    with pytest.raises(ValueError, match="VAT_WRONG"):
        invoice_arithmetic(truth)


def test_unplanted_bad_trn_fails():
    truth = build().truth
    next(doc for doc in truth.documents if doc.doc_id == "DOC-001").truth.supplier_trn = "BAD"
    with pytest.raises(ValueError, match="TRNs do not match"):
        tax_identifiers(truth)


def test_missing_heldout_flag_fails():
    truth = build().truth
    next(doc for doc in truth.documents if doc.layout == "L4").held_out = False
    with pytest.raises(ValueError, match="held-out flags"):
        held_out_and_emails(truth)


def test_email_share_check_rejects_spec_volume_conflict():
    truth = build().truth
    # The approximate 25-email volume cannot satisfy the explicit 25% gate.
    non_emails = [doc for doc in truth.documents if not doc.kind.startswith("email_")]
    emails = [doc for doc in truth.documents if doc.kind.startswith("email_")]
    truth.documents = non_emails + emails[:20] + [doc for doc in emails if doc.layout == "E2"]
    with pytest.raises(ValueError, match="Email share"):
        held_out_and_emails(truth)


def test_fake_first_seen_variant_fails():
    truth = build().truth
    truth.name_variants[0].variant = "unused trading name"
    with pytest.raises(ValueError, match="Variant absent"):
        names(truth)


def test_pos_gate_reads_actual_csv_not_just_truth():
    scenario = build()
    scenario.pos[0].net_sales += Decimal("0.01")
    with TemporaryDirectory(prefix="m1-corrupt-pos-", dir=ROOT / ".local/temp") as directory:
        root = Path(directory)
        for i in range(0, len(scenario.pos), 4):
            path = root / scenario.truth.feeds[i // 4].path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(records_csv(scenario.pos[i : i + 4]))
        with pytest.raises(ValueError, match="POS net arithmetic"):
            pos_arithmetic(root, scenario.truth)


def test_nonexistent_and_out_of_bounds_refs_fail():
    truth = build().truth
    with TemporaryDirectory(prefix="m1-bad-ref-", dir=ROOT / ".local/temp") as directory:
        root = Path(directory)
        with pytest.raises(ValueError, match="Bad file ref"):
            references(root, truth)
        first = truth.problems[0]
        ref = next(iter(first.refs.values()))
        path = root / ref.path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("header\nrow\n", encoding="utf-8")
        ref.row = 999
        with pytest.raises(ValueError, match="Bad row ref"):
            references(root, truth)


def test_statement_arithmetic_failure_precedes_feed_read(monkeypatch):
    truth = build().truth
    statement = truth.documents[0].truth
    statement.net_payout += Decimal("0.01")
    monkeypatch.setattr("backroom.verify.read_pos", lambda *_: build().pos)
    with pytest.raises(ValueError, match="Payout footer"):
        payout_arithmetic(ROOT, truth)


def test_m1_gate_prints_pass_and_returns_success(capsys):
    temporary = ROOT / ".local/temp"
    temporary.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="m1-cli-", dir=temporary) as directory:
        assert m1(Path(directory) / "unpopulated")
    output = capsys.readouterr().out
    assert "PASS byte determinism" in output and "FAIL" not in output
    assert "Document counts per layout" in output


def test_gate_reports_failure_when_a_check_fails(monkeypatch, capsys):
    monkeypatch.setattr(
        "backroom.verify.check_dataset", lambda _: [("intentional corruption", False, "bad")]
    )
    temporary = ROOT / ".local/temp"
    temporary.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="m1-fail-", dir=temporary) as directory:
        assert not m1(Path(directory) / "unpopulated")
    assert "FAIL intentional corruption: bad" in capsys.readouterr().out


def test_failed_cli_exits_nonzero(monkeypatch):
    from backroom.verify import main

    monkeypatch.setattr("sys.argv", ["backroom.verify", "m1"])
    monkeypatch.setattr("backroom.verify.m1", lambda: False)
    with pytest.raises(SystemExit) as exit_info:
        main()
    assert exit_info.value.code == 1
