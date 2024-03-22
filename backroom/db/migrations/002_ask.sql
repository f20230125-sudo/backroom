ALTER TABLE pos_days ADD COLUMN net_cents BIGINT;
ALTER TABLE pos_days ADD COLUMN gross_cents BIGINT;
ALTER TABLE pos_days ADD COLUMN orders INTEGER;
ALTER TABLE ledger_lines ADD COLUMN receipt_cents BIGINT;
ALTER TABLE payout_statements ADD COLUMN net_cents BIGINT;
ALTER TABLE exceptions ADD COLUMN date_start TEXT;
ALTER TABLE exceptions ADD COLUMN date_end TEXT;
ALTER TABLE exceptions ADD COLUMN amount_cents BIGINT;
CREATE TABLE invoice_facts (
  document_id TEXT PRIMARY KEY REFERENCES documents(id), supplier TEXT REFERENCES suppliers(id),
  outlet TEXT REFERENCES outlets(id), issue_date TEXT NOT NULL, invoice_number TEXT NOT NULL,
  supplier_name TEXT NOT NULL, total_cents BIGINT, duplicate_of TEXT
);
CREATE TABLE invoice_lines (
  document_id TEXT NOT NULL REFERENCES invoice_facts(document_id), line_no INTEGER NOT NULL,
  description TEXT NOT NULL, item_id TEXT, category TEXT, unit TEXT, quantity TEXT,
  net_cents BIGINT NOT NULL, PRIMARY KEY (document_id, line_no)
);
