CREATE TABLE document_sources (
  document_id TEXT PRIMARY KEY REFERENCES documents(id),
  text TEXT NOT NULL, media_type TEXT NOT NULL, error TEXT
);
CREATE TABLE webhook_replays (signature_hash TEXT PRIMARY KEY, received_at TEXT NOT NULL);
