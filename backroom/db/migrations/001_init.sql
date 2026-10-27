CREATE TABLE outlets (id TEXT PRIMARY KEY, name TEXT NOT NULL, payload TEXT NOT NULL);
CREATE TABLE suppliers (id TEXT PRIMARY KEY, legal_name TEXT NOT NULL, trade_name TEXT NOT NULL, trn TEXT NOT NULL, payload TEXT NOT NULL);
CREATE TABLE items (id TEXT PRIMARY KEY, name TEXT NOT NULL, payload TEXT NOT NULL);
CREATE TABLE name_map (
  entity_type TEXT NOT NULL CHECK (entity_type IN ('outlet', 'supplier', 'item')),
  variant_key TEXT NOT NULL, variant TEXT NOT NULL, canonical_id TEXT,
  decision TEXT NOT NULL CHECK (decision IN ('auto_match', 'review', 'new')),
  status TEXT NOT NULL CHECK (status IN ('proposed', 'approved', 'rejected')),
  payload TEXT NOT NULL, PRIMARY KEY (entity_type, variant_key)
);
CREATE TABLE documents (
  id TEXT PRIMARY KEY, path TEXT NOT NULL UNIQUE, kind TEXT NOT NULL, layout TEXT NOT NULL,
  business_date TEXT, status TEXT NOT NULL, payload TEXT NOT NULL
);
CREATE TABLE extractions (
  document_id TEXT PRIMARY KEY REFERENCES documents(id), status TEXT NOT NULL,
  prompt_version TEXT NOT NULL, model_calls INTEGER NOT NULL, cache_hits INTEGER NOT NULL,
  not_run INTEGER NOT NULL, payload TEXT NOT NULL
);
CREATE TABLE extraction_issues (
  id TEXT PRIMARY KEY, document_id TEXT NOT NULL REFERENCES documents(id),
  code TEXT NOT NULL, field TEXT NOT NULL, payload TEXT NOT NULL,
  UNIQUE (document_id, code, field)
);
CREATE TABLE review_queue (
  id TEXT PRIMARY KEY, document_id TEXT REFERENCES documents(id), reason TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'open', payload TEXT NOT NULL
);
CREATE TABLE pos_days (
  outlet TEXT NOT NULL REFERENCES outlets(id), business_date TEXT NOT NULL, channel TEXT NOT NULL,
  gross_sales TEXT NOT NULL, net_sales TEXT NOT NULL, refunds TEXT NOT NULL, payload TEXT NOT NULL,
  PRIMARY KEY (outlet, business_date, channel)
);
CREATE TABLE payout_statements (
  id TEXT PRIMARY KEY, platform TEXT NOT NULL, outlet TEXT NOT NULL REFERENCES outlets(id),
  week_start TEXT NOT NULL, week_end TEXT NOT NULL, paid_date TEXT NOT NULL,
  net_payout TEXT NOT NULL, payload TEXT NOT NULL, UNIQUE (platform, outlet, week_start)
);
CREATE TABLE payout_lines (
  statement_id TEXT NOT NULL REFERENCES payout_statements(id), line_no INTEGER NOT NULL,
  business_date TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY (statement_id, line_no)
);
CREATE TABLE ledger_lines (
  id TEXT PRIMARY KEY, date TEXT NOT NULL, source TEXT NOT NULL, contact TEXT NOT NULL,
  reference TEXT NOT NULL, debit TEXT NOT NULL, credit TEXT NOT NULL, payload TEXT NOT NULL
);
CREATE TABLE exceptions (
  id TEXT PRIMARY KEY, kind TEXT NOT NULL, outlet TEXT, date_or_week TEXT,
  refs TEXT NOT NULL, amount TEXT NOT NULL, message TEXT NOT NULL, payload TEXT NOT NULL
);
CREATE TABLE einvoice_findings (
  id TEXT PRIMARY KEY, record_type TEXT NOT NULL, record_id TEXT NOT NULL,
  rule TEXT NOT NULL, message TEXT NOT NULL, payload TEXT NOT NULL,
  UNIQUE (record_type, record_id, rule)
);
CREATE TABLE drafts (id TEXT PRIMARY KEY, payload TEXT NOT NULL);
CREATE TABLE briefs (id TEXT PRIMARY KEY, payload TEXT NOT NULL);
CREATE TABLE ask_log (id TEXT PRIMARY KEY, payload TEXT NOT NULL);
CREATE TABLE inbox (id TEXT PRIMARY KEY, signature_hash TEXT UNIQUE, payload TEXT NOT NULL);
CREATE TABLE run_log (
  id INTEGER PRIMARY KEY, started TEXT NOT NULL, finished TEXT NOT NULL,
  days INTEGER NOT NULL, documents INTEGER NOT NULL, model_calls INTEGER NOT NULL,
  cache_hits INTEGER NOT NULL, exceptions_by_kind TEXT NOT NULL,
  review_items INTEGER NOT NULL, duration_ms INTEGER NOT NULL, watermark TEXT, payload TEXT NOT NULL
);
