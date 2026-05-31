"""SQLite integrations use explicitly labelled oracle records, never model calls."""

import json
import os
import sqlite3
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from backroom import DATA
from backroom.ask.tools import REGISTRY, Tools
from backroom.db import connect, migrate
from backroom.db.repo import Repo
from backroom.extract.types import ExtractionResult, Issue
from backroom.inputs import ReplayStore, load_results, manifest, recorded_results
from backroom.m3_gate import approve_oracle_names, oracle_results
from backroom.models import InvoiceRecord
from backroom.names.match import approved_names
from backroom.pipeline import run_pipeline
from eval.m3 import evaluate_m3


@pytest.fixture(scope="module")
def oracle():
    source = manifest(DATA)
    return source, oracle_results(source)


@pytest.fixture(params=["sqlite", "postgres"])
def repo(request, tmp_path):
    if request.param == "sqlite":
        with connect(tmp_path / "test.sqlite") as repo:
            yield repo
        return
    url = os.environ.get("DATABASE_URL_TEST")
    if not url:
        pytest.skip("DATABASE_URL_TEST is not set")
    from psycopg import sql

    repo = connect(database_url=url)
    schema = "m3_test_" + uuid4().hex
    repo.connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    repo.connection.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
    repo.commit()
    try:
        yield repo
    finally:
        repo.rollback()
        repo.connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
        repo.commit()
        repo.close()


def fixed_run(repo, records, **kwargs):
    return run_pipeline(
        repo=repo,
        results=records,
        clock=lambda: datetime(2026, 10, 27, tzinfo=UTC),
        timer=lambda: 0,
        **kwargs,
    )


def business_snapshot(repo):
    """Per-run briefs are audit artifacts; business facts and drafts remain idempotent."""
    return {table: rows for table, rows in repo.snapshot().items() if table != "briefs"}


def test_full_pipeline_migrations_idempotency_incremental_rebuild_and_approvals(repo, oracle):
    source, records = oracle
    migrate(repo)
    empty = repo.snapshot(include_runs=True)
    migrate(repo)
    assert empty == repo.snapshot(include_runs=True)
    approve_oracle_names(repo, source)
    approvals = approved_names(repo)
    full = fixed_run(repo, records)
    assert full["days"] == 92 and full["documents"] == len(source["documents"])
    assert full["model_calls"] == 0 and full["duration_ms"] == 0
    assert len(repo.rows("SELECT * FROM pos_days")) == 736
    assert len(repo.rows("SELECT * FROM payout_statements")) == 84
    assert repo.rows("SELECT COUNT(*) AS n FROM exceptions")[0]["n"] == 24
    before = business_snapshot(repo)
    no_op = fixed_run(repo, records)
    assert no_op["days"] == no_op["documents"] == 0
    assert before == business_snapshot(repo)
    assert len(repo.rows("SELECT * FROM briefs")) == 2
    incremental = fixed_run(repo, records, rebuild=True, days=7)
    assert incremental["watermark"] == "2026-07-07"
    assert len(repo.rows("SELECT * FROM pos_days")) == 56
    assert not any(row["kind"] == "PAYOUT_SHORT" for row in repo.rows("SELECT * FROM exceptions"))
    fixed_run(repo, records, days=3)
    assert (
        repo.rows("SELECT watermark FROM run_log ORDER BY id DESC LIMIT 1")[0]["watermark"]
        == "2026-07-10"
    )
    fixed_run(repo, records)
    assert before == business_snapshot(repo)
    assert approved_names(repo) == approvals
    assert len(repo.rows("SELECT * FROM run_log")) == 5
    assert len(repo.rows("SELECT * FROM briefs")) == 5
    assert repo.rows("SELECT COUNT(*) AS n FROM drafts")[0]["n"] > 0
    # Offline: each draft is either recorded model wording that passed the fact check or a
    # template; never unchecked text.
    allowed = {
        "model",
        "template (model not recorded)",
        "template (model wording failed fact check)",
    }
    assert all(
        json.loads(row["payload"])["provenance"] in allowed
        for row in repo.rows("SELECT payload FROM drafts")
    )
    # Evaluate separately from extraction accuracy, and prove all 31 source-linked detections.
    from backroom.names.match import Matcher, masters

    scores = evaluate_m3(source, DATA, results=records, matcher=Matcher(masters(source), approvals))
    assert scores["reconciliation"]["totals"] == {
        "found": 31,
        "missed": 0,
        "not_run": 0,
        "false_alarms": 0,
    }
    # Every Ask tool and gold query runs on this database too: PostgreSQL rejects an untyped
    # "? IS NULL" that SQLite accepts, which once broke only the hosted load.
    from backroom.ask.gold import definitions, sql_expected

    tools = Tools(repo)
    calls = [call for case in definitions() if case["in_scope"] for call in case["calls"]]
    assert {call["tool"] for call in calls} == set(REGISTRY)
    for call in calls:
        tools.call(call["tool"], call["args"])
        sql_expected(repo, call)
    tools.call("exceptions", {})
    tools.call("sales", {"outlet": "OUT-JBR", "date_from": "2026-07-01", "date_to": "2026-07-31"})


def test_two_empty_to_full_runs_identical_including_fixed_clock_logs(tmp_path, oracle):
    source, records = oracle
    snapshots = []
    for index in range(2):
        with connect(tmp_path / f"run-{index}.sqlite") as repo:
            migrate(repo)
            approve_oracle_names(repo, source)
            fixed_run(repo, records)
            snapshots.append(repo.snapshot(include_runs=True))
    assert snapshots[0] == snapshots[1]


def test_saved_snapshot_partial_tail_and_missing_rows_never_reextract(tmp_path, monkeypatch):
    root = tmp_path / "data"
    (root / "out").mkdir(parents=True)
    record = ExtractionResult(
        record=InvoiceRecord(invoice_number="EXTRACTED"), prompt_version="fake", model_calls=2
    )
    row = json.dumps({"doc_id": "A", **record.model_dump(mode="json")})
    path = root / "out/extractions.jsonl"
    path.write_bytes((row + '\n{"doc_id":"in_progress"').encode())
    monkeypatch.setattr(
        "backroom.inputs.extract",
        lambda *a, **kw: pytest.fail("Must not reextract a live snapshot"),
    )
    loaded = load_results(root, [{"doc_id": "A"}, {"doc_id": "B"}])
    assert loaded["A"].record.invoice_number == "EXTRACTED"
    assert loaded["B"].not_run
    assert len(recorded_results(root)) == 1
    path.write_text("bad complete row\n")
    with pytest.raises(ValueError, match="row 1"):
        recorded_results(root)


def test_absent_snapshot_runs_extraction_offline_and_passes_record_flag(tmp_path, monkeypatch):
    flags = []
    monkeypatch.setattr(
        "backroom.inputs.extract",
        lambda *a, **kw: flags.append(kw["offline"]) or ExtractionResult(prompt_version="fake"),
    )
    docs = [{"doc_id": "A", "path": "source.pdf"}]
    load_results(tmp_path, docs, llm=object())
    load_results(tmp_path, docs, llm=object(), record=True)
    assert flags == [True, False]


def test_readonly_cache_does_not_change_schema_or_bytes(tmp_path):
    path = tmp_path / "recording.sqlite"
    with sqlite3.connect(path) as db:
        db.execute(
            "CREATE TABLE calls (key TEXT PRIMARY KEY, model TEXT, text TEXT, "
            "prompt_tokens INTEGER, completion_tokens INTEGER, latency_ms INTEGER)"
        )
        db.execute("INSERT INTO calls VALUES (?, ?, ?, ?, ?, ?)", ("k", "fake", "{}", 2, 3, 4))
    before = path.read_bytes()
    replay = ReplayStore(path)
    assert replay.get("k").cached and replay.get("absent") is None
    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        replay.db.execute("CREATE TABLE forbidden (id INTEGER)")
    replay.close()
    assert path.read_bytes() == before
    missing = ReplayStore(tmp_path / "absent.sqlite")
    assert missing.get("k") is None
    assert not (tmp_path / "absent.sqlite").exists()


def test_postgres_adapter_only_changes_placeholders_and_binds_values():
    class FakeConnection:
        def execute(self, sql, params):
            assert sql == "SELECT * FROM suppliers WHERE legal_name = %s"
            assert params == ("a?'; DROP TABLE suppliers;--",)
            return None

    repo = Repo(FakeConnection(), postgres=True)
    repo.execute("SELECT * FROM suppliers WHERE legal_name = ?", ("a?'; DROP TABLE suppliers;--",))
    with pytest.raises(ValueError):
        repo.upsert("suppliers; DROP TABLE items", {"id": "x"})
    with pytest.raises(ValueError):
        repo.upsert("suppliers", {"untrusted": "x"})


def test_migration_failure_rolls_back_ddl_and_version(tmp_path, monkeypatch):
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    (migrations / "001_broken.sql").write_text("CREATE TABLE partial (id TEXT); INVALID SQL;")
    monkeypatch.setattr("backroom.db.MIGRATIONS", migrations)
    with connect(tmp_path / "migration.sqlite") as repo:
        with pytest.raises(sqlite3.OperationalError):
            migrate(repo)
        assert not repo.rows("SELECT name FROM sqlite_master WHERE type = ?", ("table",))


def test_failed_run_rolls_back_business_data_and_records_safe_failure(
    tmp_path, monkeypatch, oracle
):
    _, records = oracle
    with connect(tmp_path / "failed.sqlite") as repo:
        migrate(repo)
        monkeypatch.setattr(
            "backroom.pipeline.feeds",
            lambda _: (_ for _ in ()).throw(ValueError("private details")),
        )
        with pytest.raises(ValueError, match="private details"):
            fixed_run(repo, records)
        assert repo.rows("SELECT id FROM documents") == []
        run = repo.rows("SELECT * FROM run_log")[0]
        assert json.loads(run["payload"]) == {"status": "failed", "error_type": "ValueError"}
        assert "private details" not in run["payload"]


def test_late_extraction_replaces_review_issues_and_records_actual_calls(
    tmp_path, monkeypatch, oracle
):
    source, records = oracle
    records = dict(records)
    missing = records["DOC-001"]
    records["DOC-001"] = ExtractionResult(
        prompt_version="fake",
        not_run=True,
        issues=[Issue(code="MISSING", field="record", message="no recording")],
    )
    with connect(tmp_path / "late.sqlite") as repo:
        fixed_run(repo, records)
        assert (
            repo.rows("SELECT status FROM documents WHERE id = ?", ("DOC-001",))[0]["status"]
            == "not_run"
        )
        records["DOC-001"] = missing.model_copy(update={"model_calls": 7})
        summary = fixed_run(repo, records)
        assert summary["documents"] == 1 and summary["model_calls"] == 0
        assert (
            repo.rows("SELECT * FROM extraction_issues WHERE document_id = ?", ("DOC-001",)) == []
        )
        assert not repo.rows("SELECT id FROM review_queue WHERE document_id = ?", ("DOC-001",))
        assert (
            repo.rows("SELECT status FROM documents WHERE id = ?", ("DOC-001",))[0]["status"]
            == "accepted"
        )


def test_pipeline_then_full_eval_offline_from_fake_saved_result(tmp_path, monkeypatch):
    from backroom.models import LedgerLine
    from backroom.synth.render_csv import records_csv
    from eval.run_eval import evaluate
    from tests.test_extract import record
    from tests.test_extract_eval import dataset

    data_root = tmp_path / "data"
    dataset(data_root, held_out=False)
    source = manifest(data_root)
    catalog = manifest(DATA)
    supplier = {
        **catalog["suppliers"][0],
        "legal_name": "Test Orchard Trading L.L.C.",
        "trade_name": "Test Orchard",
        "trn": "123456789012303",
    }
    outlet = {**catalog["outlets"][0], "name": "Central Kitchen"}
    source.update(
        date_from="2026-07-01",
        date_to="2026-07-01",
        suppliers=[supplier],
        outlets=[outlet],
        items=[],
        name_variants=[],
        hard_negatives=[],
        problems=[],
    )
    (data_root / "ground_truth.json").write_text(json.dumps(source))
    ledger_path = data_root / "generated/ledger/ledger.csv"
    ledger_path.parent.mkdir(parents=True)
    bill = LedgerLine(
        date="2026-07-01",
        account="500",
        contact=supplier["legal_name"],
        reference="TEST-1",
        description="Bill",
        debit="105",
        credit="0",
        source="BILL",
    )
    ledger_path.write_bytes(records_csv([bill]))
    (data_root / "out").mkdir()
    extracted = ExtractionResult(
        record=record(),
        status="accepted",
        prompt_version="fake",
        classification="email_invoice_body",
        model_calls=2,
    )
    (data_root / "out/extractions.jsonl").write_text(
        json.dumps({"doc_id": "TEST-001", **extracted.model_dump(mode="json")}) + "\n"
    )
    monkeypatch.setattr(
        "backroom.inputs.extract", lambda *a, **kw: pytest.fail("Saved input must not extract")
    )
    monkeypatch.setattr(
        "eval.run_eval.extract", lambda *a, **kw: pytest.fail("Saved input must not extract")
    )
    with connect(tmp_path / "integration.sqlite") as repo:
        run = run_pipeline(repo=repo, data_root=data_root)
        assert run["documents"] == 1 and run["model_calls"] == 0
        assert not repo.rows("SELECT * FROM exceptions")
        assert not repo.rows("SELECT * FROM einvoice_findings")
        result = evaluate(data_root=data_root, output=tmp_path / "evaluation.json", repo=repo)
        assert result["extraction"]["model_calls"] == 0
        assert result["extraction"]["saved_model_calls"] == 2
        assert result["extraction"]["summary"]["agent"]["dev"]["field_accuracy"] == 1
        assert result["reconciliation"]["totals"] == {
            "found": 0,
            "missed": 0,
            "not_run": 0,
            "false_alarms": 0,
        }
