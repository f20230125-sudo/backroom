"""Read-only source ingestion shared by the pipeline and evaluation.

A live recorder may leave a partial final JSONL row. Its complete prefix is a snapshot;
missing rows stay not-run and are retried on later pipeline runs.
"""

import csv
import json
import sqlite3
from pathlib import Path

from backroom import CACHE_DB, settings
from backroom.extract.agent import extract
from backroom.extract.payouts_csv import read_csv
from backroom.extract.types import ExtractionResult
from backroom.llm.store import Completion
from backroom.models import LedgerLine, POSRow


class ReplayStore:
    """Unlike Store's initializer, opening this cache cannot change the recorder's DB."""

    def __init__(self, path=CACHE_DB):
        self.db = (
            sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)
            if Path(path).exists()
            else None
        )

    def get(self, key):
        if self.db is None:
            return None
        row = self.db.execute(
            "SELECT model, text, prompt_tokens, completion_tokens, latency_ms "
            "FROM calls WHERE key = ?",
            (key,),
        ).fetchone()
        return (
            Completion(
                text=row[1],
                prompt_tokens=row[2],
                completion_tokens=row[3],
                model=row[0],
                cached=True,
                latency_ms=row[4],
            )
            if row
            else None
        )

    def close(self):
        if self.db:
            self.db.close()


def offline_llm(model: str | None = None):
    env = {"GEMINI_MODEL": model} if model else None
    return settings.build_llm(env, offline=True, store=ReplayStore())


def manifest(data_root):
    return json.loads((data_root / "ground_truth.json").read_text(encoding="utf-8"))


def recorded_results(data_root) -> dict[str, ExtractionResult] | None:
    path = data_root / "out/extractions.jsonl"
    if not path.exists():
        return None
    out = {}
    for index, line in enumerate(path.read_bytes().splitlines(keepends=True), 1):
        if not line.endswith(b"\n"):
            break
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
            doc_id = raw.pop("doc_id")
            out[doc_id] = ExtractionResult.model_validate(raw)
        except (ValueError, KeyError) as exc:
            raise ValueError(f"Invalid extraction snapshot row {index}") from exc
    return out


def missing_result():
    return ExtractionResult(prompt_version="not_run", not_run=True, status="review")


def load_results(data_root, docs, *, results=None, record=False, llm=None):
    if not docs:
        return {}
    snapshot = results if results is not None else recorded_results(data_root)
    if snapshot is not None:
        return {d["doc_id"]: snapshot.get(d["doc_id"], missing_result()) for d in docs}
    owned = llm is None and not record
    llm = (
        llm if llm is not None else (settings.build_llm(offline=False) if record else offline_llm())
    )
    try:
        return {
            d["doc_id"]: extract(data_root / d["path"], llm=llm, offline=not record) for d in docs
        }
    finally:
        if owned:
            llm.store.close()


def csv_records(path, model):
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return [model.model_validate(row) for row in csv.DictReader(handle)]


def feeds(data_root):
    pos = [
        row
        for path in sorted((data_root / "generated/pos").rglob("*.csv"))
        for row in csv_records(path, POSRow)
    ]
    ledger = csv_records(data_root / "generated/ledger/ledger.csv", LedgerLine)
    return pos, ledger


def csv_payouts(data_root, docs):
    return [read_csv(data_root / d["path"]) for d in docs if d["layout"] in {"CSV_A", "CSV_B"}]
