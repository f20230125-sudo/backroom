"""Extract the manifest, replaying recorded responses unless --record is explicit."""

import argparse
import json
from collections import Counter
from pathlib import Path

from backroom import DATA, settings
from backroom.extract.agent import extract
from backroom.models import Document


def documents(data_root: Path = DATA) -> list[Document]:
    manifest = json.loads((data_root / "ground_truth.json").read_text(encoding="utf-8"))
    return sorted(
        (Document.model_validate(doc) for doc in manifest["documents"]), key=lambda doc: doc.doc_id
    )


def run(*, record=False, limit=None, doc_id=None, data_root=DATA, llm=None):
    if limit is not None and limit < 1:
        raise ValueError("Limit must be positive")
    selected = [doc for doc in documents(data_root) if doc_id is None or doc.doc_id == doc_id]
    if doc_id and not selected:
        raise ValueError(f"Unknown document: {doc_id}")
    if limit is not None:
        selected = selected[:limit]
    llm = llm if llm is not None else settings.build_llm(offline=not record)
    counts = Counter(
        {
            key: 0
            for key in (
                "accepted",
                "review",
                "document_problem",
                "cache_hits",
                "model_calls",
                "not_run",
            )
        }
    )
    output = data_root / "out/extractions.jsonl"
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="\n") as handle:
        for doc in selected:
            result = extract(doc, llm=llm, data_root=data_root)
            handle.write(
                json.dumps(
                    {"doc_id": doc.doc_id, **result.model_dump(mode="json")},
                    sort_keys=True,
                    ensure_ascii=False,
                )
                + "\n"
            )
            counts[result.status] += 1
            counts["cache_hits"] += result.cache_hits
            counts["model_calls"] += result.model_calls
            counts["not_run"] += int(result.not_run)
            if result.not_run:
                print(f"{doc.doc_id}: NOT RUN (missing recording)")
    print(" / ".join(f"{key}: {value}" for key, value in counts.items()))
    return dict(counts)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--record", action="store_true")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--doc")
    args = parser.parse_args()
    try:
        run(record=args.record, limit=args.limit, doc_id=args.doc)
    except ValueError as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
