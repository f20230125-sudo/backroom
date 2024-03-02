"""Only validated schema identifiers are interpolated; all data uses bound parameters."""

import json
import os
import sqlite3
from pathlib import Path

from backroom import ROOT

TABLES = {
    "outlets": ("id", "name", "payload"),
    "suppliers": ("id", "legal_name", "trade_name", "trn", "payload"),
    "items": ("id", "name", "payload"),
    "name_map": (
        "entity_type",
        "variant_key",
        "variant",
        "canonical_id",
        "decision",
        "status",
        "payload",
    ),
    "documents": ("id", "path", "kind", "layout", "business_date", "status", "payload"),
    "extractions": (
        "document_id",
        "status",
        "prompt_version",
        "model_calls",
        "cache_hits",
        "not_run",
        "payload",
    ),
    "extraction_issues": ("id", "document_id", "code", "field", "payload"),
    "review_queue": ("id", "document_id", "reason", "status", "payload"),
    "pos_days": (
        "outlet",
        "business_date",
        "channel",
        "gross_sales",
        "net_sales",
        "refunds",
        "payload",
    ),
    "payout_statements": (
        "id",
        "platform",
        "outlet",
        "week_start",
        "week_end",
        "paid_date",
        "net_payout",
        "payload",
    ),
    "payout_lines": ("statement_id", "line_no", "business_date", "payload"),
    "ledger_lines": ("id", "date", "source", "contact", "reference", "debit", "credit", "payload"),
    "exceptions": ("id", "kind", "outlet", "date_or_week", "refs", "amount", "message", "payload"),
    "einvoice_findings": ("id", "record_type", "record_id", "rule", "message", "payload"),
    "drafts": ("id", "payload"),
    "briefs": ("id", "payload"),
    "ask_log": ("id", "payload"),
    "inbox": ("id", "signature_hash", "payload"),
    "run_log": (
        "id",
        "started",
        "finished",
        "days",
        "documents",
        "model_calls",
        "cache_hits",
        "exceptions_by_kind",
        "review_items",
        "duration_ms",
        "watermark",
        "payload",
    ),
    "schema_migrations": ("version",),
}


def dumps(value) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


class Repo:
    def __init__(self, connection, *, postgres=False):
        self.connection = connection
        self.postgres = postgres

    def execute(self, sql, params=()):
        # Repository statements never contain literal question marks; values may do so safely.
        return self.connection.execute(sql.replace("?", "%s") if self.postgres else sql, params)

    def rows(self, sql, params=()) -> list[dict]:
        cursor = self.execute(sql, params)
        columns = [column[0] for column in cursor.description]
        return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]

    def upsert(self, table, values, *, conflict=("id",), preserve=()):
        if table not in TABLES or not values or not set(values) <= set(TABLES[table]):
            raise ValueError("Unknown table or columns")
        if not set(conflict) <= set(values) or not set(preserve) <= set(values):
            raise ValueError("Invalid upsert keys")
        columns = tuple(values)
        update = [c for c in columns if c not in conflict and c not in preserve]
        action = (
            "DO UPDATE SET " + ", ".join(f"{c} = excluded.{c}" for c in update)
            if update
            else "DO NOTHING"
        )
        self.execute(
            f"INSERT INTO {table} ({', '.join(columns)}) "  # noqa: S608 -- allowlisted identifiers
            f"VALUES ({', '.join('?' for _ in columns)}) "
            f"ON CONFLICT ({', '.join(conflict)}) {action}",
            tuple(values[c] for c in columns),
        )

    def snapshot(self, *, include_runs=False):
        return {
            table: sorted(self.rows(f"SELECT * FROM {table}"), key=dumps)  # noqa: S608 -- static allowlist
            for table in TABLES
            if include_runs or table != "run_log"
        }

    def clear_loaded(self):
        # Name approvals and audit history survive rebuilds.
        for table in (
            "extraction_issues",
            "review_queue",
            "extractions",
            "documents",
            "payout_lines",
            "payout_statements",
            "pos_days",
            "ledger_lines",
            "exceptions",
            "einvoice_findings",
            "drafts",
            "briefs",
        ):
            self.execute(f"DELETE FROM {table}")  # noqa: S608 -- static allowlist

    def commit(self):
        self.connection.commit()

    def rollback(self):
        self.connection.rollback()

    def close(self):
        self.connection.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type:
            self.rollback()
        self.close()


def connect(path=None, *, database_url=None):
    url = (
        database_url
        if database_url is not None
        else (os.environ.get("DATABASE_URL") if path is None else None)
    )
    if url:
        import psycopg

        return Repo(psycopg.connect(url), postgres=True)
    path = path if path is not None else ROOT / ".local/backroom.sqlite"
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(path))
    connection.execute("PRAGMA foreign_keys = ON")
    return Repo(connection)
