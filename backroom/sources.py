"""Persist source text at ingestion so the web function needs no source filesystem."""

from backroom.extract.text import DocumentError, parse_bytes


def store_sources(repo, docs, data_root):
    stored = {r["document_id"] for r in repo.rows("SELECT document_id FROM document_sources")}
    for doc in docs:
        if doc["doc_id"] in stored:
            continue
        path = (data_root / doc["path"]).resolve()
        if not path.is_relative_to(data_root.resolve()):
            raise ValueError("Source path outside data directory")
        try:
            # These are operator-owned offline files, not public request uploads.
            source = parse_bytes(path.read_bytes(), path.suffix)
            text, media_type, error = source.text, source.media_type, None
        except (OSError, DocumentError):
            text, media_type, error = "", "text/plain", "Source text unavailable"
        repo.upsert(
            "document_sources",
            {"document_id": doc["doc_id"], "text": text, "media_type": media_type, "error": error},
            conflict=("document_id",),
        )
