"""API contracts over an isolated, real offline pipeline snapshot; no shared operator DB."""

import base64
import hashlib
import hmac
import json
import shutil
import sqlite3
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from backroom import ROOT
from backroom.api import create_app
from backroom.api.security import CSP, client_address
from backroom.db import connect
from backroom.db.repo import dumps
from backroom.inbox import consume_inbox
from backroom.pipeline import run_pipeline
from backroom.settings import CacheMiss


@pytest.fixture(scope="module")
def pipeline_snapshot(tmp_path_factory):
    path = tmp_path_factory.mktemp("codex-api") / "snapshot.sqlite"
    with connect(path) as repo:
        result = run_pipeline(repo=repo, offline=True)
        assert result["days"] == 92 and result["model_calls"] == 0
        assert repo.rows("SELECT COUNT(*) AS n FROM document_sources")[0]["n"] == 234
    return path


@pytest.fixture
def app(tmp_path, pipeline_snapshot):
    path = tmp_path / "api.sqlite"
    shutil.copyfile(pipeline_snapshot, path)
    return create_app(
        {
            "TESTING": True,
            "DATABASE": path,
            "DATABASE_URL": "",
            "APP_ENV": "local",
            "INBOX_WEBHOOK_SECRET": "fixture-only",
            "RATE_LIMIT": 10000,
        }
    )


@pytest.fixture
def client(app):
    return app.test_client()


GETS = [
    "/health",
    "/overview",
    "/documents",
    "/documents/DOC-001",
    "/review",
    "/recon",
    "/exceptions",
    "/names",
    "/einvoice",
    "/ask/recorded",
    "/results",
    "/runs",
    "/drafts",
    "/brief/latest",
]


@pytest.mark.parametrize("path", GETS)
def test_get_contract_and_headers(client, path):
    response = client.get("/api" + path)
    assert response.status_code == 200, response.json
    assert response.is_json
    assert response.headers["Content-Security-Policy"] == CSP
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
    assert "Access-Control-Allow-Origin" not in response.headers


def test_payloads_filters_pagination_and_sources(client):
    first = client.get("/api/documents?page_size=2").json
    second = client.get("/api/documents?page_size=2&page=2").json
    assert first["total"] == 234 and len(first["items"]) == 2
    assert first["items"][0]["id"] != second["items"][0]["id"]
    filtered = client.get("/api/documents?kind=invoice&held_out=true").json
    assert filtered["items"] and all(
        r["kind"] == "invoice" and r["held_out"] for r in filtered["items"]
    )
    document = client.get("/api/documents/DOC-001").json
    assert document["text"] and document["quotes"] and document["extraction"]["record"]
    assert "truth" not in document
    recon = client.get("/api/recon?outlet=OUT-JBR&week=34").json
    assert recon["payouts"] and all(r["outlet"] == "OUT-JBR" for r in recon["payouts"])
    assert all(r["week_start"] == "2026-08-17" for r in recon["payouts"])
    overview = client.get("/api/overview").json
    assert overview["last_run"]["days"] == 92 and overview["brief"]["body"]
    assert "names_holdout" in client.get("/api/results").json
    assert client.get("/api/drafts").json["sent"] is False


@pytest.mark.parametrize(
    "path",
    [
        "/documents?page=0",
        "/documents?page=-1",
        "/documents?page=huge",
        "/documents?page_size=101",
        "/documents?kind=bogus",
        "/documents?status=bogus",
        "/documents?held_out=yes",
        "/recon?outlet=unknown",
        "/recon?week=54",
        "/exceptions?kind=bogus",
        "/review?status=bad",
    ],
)
def test_invalid_filters_are_json_400(client, path):
    response = client.get("/api" + path)
    assert response.status_code == 400 and response.is_json


@pytest.mark.parametrize(
    "method,path",
    [
        ("get", "/api/documents/absent"),
        ("get", "/api/export/unknown.csv"),
        ("get", "/api/absent"),
        ("post", "/api/absent"),
        ("get", "/api"),
        ("put", "/api/check"),
        ("delete", "/api/documents/DOC-001"),
    ],
)
def test_unknown_and_wrong_methods_stay_json(client, method, path):
    response = getattr(client, method)(path)
    assert response.status_code in (404, 405) and response.is_json
    assert response.headers["Content-Security-Policy"] == CSP


def test_check_samples_json_validation_duplicates_and_no_writes(app, client):
    path = Path(app.config["DATABASE"])
    before = hashlib.sha256(path.read_bytes()).digest()
    with connect(path) as repo:
        snapshot = repo.snapshot(include_runs=True)
    sample = client.post("/api/check", json={"doc_id": "DOC-001"})
    assert sample.status_code == 200 and sample.json["writes"] == 0
    assert sample.json["evidence_checked"] and sample.json["duplicates"]
    pasted = client.post(
        "/api/check",
        json={
            "invoice": {
                "supplier_name": "Sample Orchard",
                "net": "100.00",
                "vat": "9.00",
                "total": "109.00",
                "supplier_trn": "bad",
                "lines": [{"line_net": "99.00", "vat_rate": "5"}],
            }
        },
    )
    assert pasted.status_code == 200
    assert {i["code"] for i in pasted.json["issues"]} >= {
        "VAT_WRONG",
        "LINES_NOT_SUM",
        "TRN_INVALID",
    }
    assert pasted.json["findings"] and pasted.json["evidence_checked"] is False
    # Parsed decimal JSON stays exact even beyond binary float precision.
    assert (
        client.post(
            "/api/check", data='{"net":9007199254740993.01}', content_type="application/json"
        ).status_code
        == 200
    )
    for bad in [
        [],
        {"invoice": {"total": "NaN"}},
        {"invoice": {"issue_date": "tomorrow"}},
        {"invoice": {}, "unknown": True},
        {"text": []},
        {"doc_id": "missing"},
    ]:
        assert client.post("/api/check", json=bad).status_code in (400, 404)
    assert (
        client.post("/api/check", data="{broken", content_type="application/json").status_code
        == 400
    )
    assert client.post("/api/check", data="{}").status_code == 400
    with connect(path) as repo:
        assert repo.snapshot(include_runs=True) == snapshot
    assert hashlib.sha256(path.read_bytes()).digest() == before


def test_local_approve_reject_resolve_and_invalid_records(client):
    names = client.get("/api/names").json["items"]
    row = next(r for r in names if r["canonical_id"])
    endpoint = "/api/names/" + row["entity_type"]
    data = {"variant": row["variant"], "canonical_id": row["canonical_id"]}
    assert client.post(endpoint + "/approve", json=data).json["status"] == "approved"
    assert client.post(endpoint + "/reject", json=data).json["status"] == "rejected"
    assert client.post(endpoint + "/approve", json={}).status_code == 400
    assert (
        client.post(endpoint + "/approve", json={**data, "canonical_id": "unknown"}).status_code
        == 400
    )
    assert client.post("/api/names/unknown/approve", json=data).status_code == 400
    review = client.get("/api/review").json["items"][0]
    assert client.post(f"/api/review/{review['id']}/resolve").json["status"] == "resolved"
    assert client.post("/api/review/missing/resolve").status_code == 404


@pytest.mark.parametrize("mode", ["production", "preview"])
def test_hosted_guard_at_http_and_database_level(app, mode):
    hosted = create_app({**app.config, "APP_ENV": mode, "TRUSTED_HOSTS": ["localhost"]})
    client = hosted.test_client()
    for path in ["/names/supplier/approve", "/names/supplier/reject", "/review/any/resolve"]:
        response = client.post("/api" + path, json={})
        assert response.status_code == 403 and "local copy" in response.json["message"]
    response = client.get("/api/names")
    assert response.json["readonly"] is True
    if mode == "production":
        assert "max-age=31536000" in response.headers["Strict-Transport-Security"]
        assert client.get("/api/health", headers={"Host": "attacker.invalid"}).status_code == 400
    with connect(app.config["DATABASE"], readonly=True) as repo:
        with pytest.raises(sqlite3.OperationalError):
            repo.execute("DELETE FROM documents")
    assert client.post("/api/check", json={"doc_id": "DOC-001"}).status_code == 200


def test_production_hosts_fail_closed(monkeypatch):
    monkeypatch.delenv("BACKROOM_TRUSTED_HOSTS", raising=False)
    with pytest.raises(RuntimeError, match="BACKROOM_TRUSTED_HOSTS"):
        create_app({"APP_ENV": "production"})


def signed(raw, timestamp=None, secret="fixture-only"):  # noqa: S107 -- synthetic fixture
    timestamp = str(int(time.time()) if timestamp is None else timestamp)
    digest = hmac.new(secret.encode(), timestamp.encode() + b"." + raw, hashlib.sha256).hexdigest()
    return {"X-Backroom-Timestamp": timestamp, "X-Backroom-Signature": "sha256=" + digest}


def test_webhook_good_exact_bytes_replay_bad_stale_future_type_size(app, client):
    raw = b"From: accounts@example.invalid\r\nSubject: Sample\r\n\r\nInvoice source.\r\n"
    headers = signed(raw)
    good = client.post("/api/hooks/inbox", data=raw, headers=headers, content_type="message/rfc822")
    assert good.status_code == 202 and good.json["status"] == "pending"
    with connect(app.config["DATABASE"]) as repo:
        rows = repo.rows("SELECT * FROM inbox")
        assert len(rows) == 1
        assert base64.b64decode(json.loads(rows[0]["payload"])["raw_base64"]) == raw
        assert len(repo.rows("SELECT * FROM webhook_replays")) == 1
    assert (
        client.post(
            "/api/hooks/inbox", data=raw, headers=headers, content_type="message/rfc822"
        ).status_code
        == 409
    )
    assert (
        client.post(
            "/api/hooks/inbox",
            data=raw + b"changed",
            headers=headers,
            content_type="message/rfc822",
        ).status_code
        == 403
    )
    for timestamp in (int(time.time()) - 301, int(time.time()) + 301):
        assert (
            client.post(
                "/api/hooks/inbox",
                data=raw,
                headers=signed(raw, timestamp),
                content_type="message/rfc822",
            ).status_code
            == 403
        )
    assert (
        client.post(
            "/api/hooks/inbox", data=raw, headers=headers, content_type="text/plain"
        ).status_code
        == 415
    )
    assert (
        client.post("/api/hooks/inbox", data=raw, content_type="message/rfc822").status_code == 400
    )
    assert (
        client.post(
            "/api/hooks/inbox", data=b"x" * (5 * 1024 * 1024 + 1), content_type="message/rfc822"
        ).status_code
        == 413
    )
    assert (
        client.post(
            "/api/check", data=b"x" * (1024 * 1024 + 1), content_type="application/json"
        ).status_code
        == 413
    )


def test_unconfigured_hook_and_bad_timestamp(app, client):
    app.config["INBOX_WEBHOOK_SECRET"] = ""
    assert (
        client.post("/api/hooks/inbox", data=b"mail", content_type="message/rfc822").status_code
        == 503
    )
    app.config["INBOX_WEBHOOK_SECRET"] = "fixture-only"  # noqa: S105 -- synthetic fixture
    headers = {"X-Backroom-Timestamp": "nan", "X-Backroom-Signature": "invalid"}
    assert (
        client.post(
            "/api/hooks/inbox", data=b"mail", headers=headers, content_type="message/rfc822"
        ).status_code
        == 400
    )


def test_rate_limit_and_untrusted_forwarding_headers(app, monkeypatch):
    monkeypatch.delenv("VERCEL", raising=False)
    app.config["RATE_LIMIT"] = 2
    client = app.test_client()
    for i in range(2):
        assert (
            client.post(
                "/api/check", json={}, headers={"X-Vercel-Forwarded-For": f"203.0.113.{i + 1}"}
            ).status_code
            == 200
        )
    limited = client.post("/api/check", json={})
    assert limited.status_code == 429 and limited.is_json and limited.headers["Retry-After"]
    assert client.get("/api/health").status_code == 200
    with app.test_request_context(
        headers={"X-Vercel-Forwarded-For": "203.0.113.5"}, environ_base={"REMOTE_ADDR": "127.0.0.8"}
    ):
        assert client_address() == "127.0.0.8"
        monkeypatch.setenv("VERCEL", "1")
        assert client_address() == "203.0.113.5"
    with app.test_request_context(
        headers={"X-Vercel-Forwarded-For": "invalid"}, environ_base={"REMOTE_ADDR": "127.0.0.8"}
    ):
        assert client_address() == "127.0.0.8"


def test_csv_exports_formula_and_control_prefixes(app, client):
    with connect(app.config["DATABASE"]) as repo:
        for index, value in enumerate(["=1+1", "+1", "-1", "@SUM(A1)", "\t=1", "\r=1"]):
            repo.execute(
                "INSERT INTO review_queue (id, reason, payload) VALUES (?, ?, ?)",
                (f"csv-{index}", value, "{}"),
            )
        repo.commit()
    for name in ("documents", "exceptions", "names", "einvoice", "review"):
        response = client.get(f"/api/export/{name}.csv")
        assert response.status_code == 200 and response.mimetype == "text/csv"
        assert "attachment" in response.headers["Content-Disposition"]
        assert response.headers["Content-Security-Policy"] == CSP
    import csv
    import io

    rows = list(csv.DictReader(io.StringIO(client.get("/api/export/review.csv").text)))
    assert all(r["reason"].startswith("'") for r in rows if r["id"].startswith("csv-"))


def test_source_html_not_executed_cross_origin_and_safe_errors(app, client, monkeypatch, caplog):
    assert (
        client.post(
            "/api/names/supplier/approve", json={}, headers={"Origin": "https://other.invalid"}
        ).status_code
        == 403
    )
    secret_message = "fixture-private-database-password"  # noqa: S105 -- redaction test
    monkeypatch.setattr(
        "backroom.api.routes.artifact",
        lambda _: (_ for _ in ()).throw(RuntimeError(secret_message)),
    )
    response = client.get("/api/results")
    assert response.status_code == 503 and response.is_json
    assert secret_message not in response.text + caplog.text


def test_ask_prefers_stored_answer_over_eval_snapshot(app, client):
    answer = {
        "question": "Stored question",
        "answer": "Recorded answer",
        "refused": False,
        "not_run": False,
        "reason": "",
        "tool_calls": [],
        "numbers_used": [],
    }
    with connect(app.config["DATABASE"]) as repo:
        repo.upsert("ask_log", {"id": "ask-01", "payload": dumps(answer)})
        repo.commit()
    row = client.get("/api/ask/recorded").json["items"][0]
    assert row["result"]["answer"] == "Recorded answer" and row["answer_source"] == "ask_log"
    assert len(client.get("/api/ask/recorded").json["items"]) == 35


def test_ask_stored_answer_matching_evaluation_keeps_its_score(app, client):
    scored = client.get("/api/ask/recorded").json["evaluation"]["questions"][0]
    with connect(app.config["DATABASE"]) as repo:
        payload = {**scored["result"], "question": scored["question"]}
        repo.upsert("ask_log", {"id": scored["id"], "payload": dumps(payload)})
        repo.commit()
    row = client.get("/api/ask/recorded").json["items"][0]
    assert row["answer_source"] == "ask_log"
    assert row["state"] == row["evaluation_state"] == scored["state"]


def test_shell_deep_links_static_assets_and_json_404(app, tmp_path):
    public = tmp_path / "public"
    public.mkdir()
    (public / "index.html").write_text("<!doctype html><h1>App shell</h1>")
    (public / "asset.css").write_text("body { color: grey }")
    app.config["PUBLIC_DIR"] = public
    client = app.test_client()
    for path in ("/", "/inbox/DOC-001", "/missing-page"):
        response = client.get(path)
        assert response.status_code == 200 and "App shell" in response.text
        assert response.headers["Content-Security-Policy"] == CSP
    assert client.get("/asset.css").mimetype == "text/css"
    assert client.get("/api/missing").is_json
    assert "[project]" not in client.get("/../pyproject.toml").text


def test_inbox_consumed_offline_once(app, client, monkeypatch):
    raw = b"From: accounts@example.invalid\r\nSubject: Delivery notice\r\n\r\nDelivery delayed."
    received = client.post(
        "/api/hooks/inbox", data=raw, headers=signed(raw), content_type="message/rfc822"
    )

    class Missing:
        def complete(self, *_a, **_kw):
            raise CacheMiss("fixture offline miss")

    # Use the real mail reader, classifier and extractor, with a strict fake cache miss.
    with connect(app.config["DATABASE"]) as repo:
        fresh = consume_inbox(repo, "2026-09-30", llm=Missing())
        repo.commit()
        assert len(fresh) == 1
        doc = repo.rows("SELECT * FROM documents WHERE id = ?", ("inbox-" + received.json["id"],))
        assert (
            doc
            and "Delivery delayed"
            in repo.rows(
                "SELECT text FROM document_sources WHERE document_id = ?", (doc[0]["id"],)
            )[0]["text"]
        )
        assert consume_inbox(repo, "2026-09-30", llm=Missing()) == {}


def test_postgres_adapter_disables_preparation_and_uses_read_only_transaction(monkeypatch):
    calls = []
    connection = SimpleNamespace(execute=lambda sql: calls.append(sql))
    monkeypatch.setattr("psycopg.connect", lambda url, **kw: calls.append((url, kw)) or connection)
    repo = connect(database_url="postgresql://fixture.invalid/synthetic", readonly=True)
    assert repo.postgres and calls[0][1] == {"prepare_threshold": None}
    assert calls[1] == "SET TRANSACTION READ ONLY"


def test_default_factory_never_migrates_or_runs_pipeline(monkeypatch):
    def forbidden(*_a, **_kw):
        pytest.fail("App startup must not touch the operator database")

    monkeypatch.setattr("backroom.db.migrate", forbidden)
    monkeypatch.setattr("backroom.pipeline.run_pipeline", forbidden)
    instance = create_app(
        {"DATABASE": ROOT / ".local/codex-missing.sqlite", "DATABASE_URL": "", "APP_ENV": "local"}
    )
    assert instance.test_client().get("/api/health").status_code == 503
    assert not (ROOT / ".local/codex-missing.sqlite").exists()
