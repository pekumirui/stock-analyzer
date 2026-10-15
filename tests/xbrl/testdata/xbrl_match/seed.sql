-- Minimal xbrl_* schema for tools/xbrl/xbrl_match_rate.py (columns match app.models).
CREATE TABLE xbrl_std_items (
    id INTEGER PRIMARY KEY,
    code TEXT NOT NULL UNIQUE,
    statement TEXT NOT NULL,
    section TEXT,
    label_ja TEXT NOT NULL,
    display_order INTEGER NOT NULL DEFAULT 0,
    sign TEXT NOT NULL DEFAULT 'natural',
    is_required INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE xbrl_filings (
    id INTEGER PRIMARY KEY,
    code TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'tdnet',
    doc_id TEXT NOT NULL,
    doc_type TEXT NOT NULL DEFAULT 'other',
    period_type TEXT,
    fiscal_year_end DATE,
    submitted_at TEXT NOT NULL,
    title TEXT,
    accounting_standard TEXT,
    is_amendment INTEGER NOT NULL DEFAULT 0,
    raw_path TEXT NOT NULL,
    raw_sha256 TEXT,
    parse_status TEXT NOT NULL DEFAULT 'pending',
    parse_error TEXT,
    parsed_at TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE xbrl_financials (
    id INTEGER PRIMARY KEY,
    code TEXT NOT NULL,
    filing_id INTEGER NOT NULL,
    std_item_id INTEGER NOT NULL,
    is_consolidated INTEGER NOT NULL,
    period_type TEXT NOT NULL,
    period_start DATE,
    period_end DATE NOT NULL,
    value NUMERIC NOT NULL,
    unit TEXT NOT NULL DEFAULT 'JPY',
    source_fact_id INTEGER,
    source_element_id INTEGER,
    generated_at TEXT NOT NULL
);

INSERT INTO xbrl_std_items (id, code, statement, label_ja, display_order) VALUES
  (1, 'net_sales', 'PL', '売上高', 100),
  (2, 'operating_income', 'PL', '営業利益', 140),
  (3, 'ordinary_income', 'PL', '経常利益', 150),
  (4, 'net_income', 'PL', '親会社株主に帰属する当期純利益', 170),
  (5, 'total_assets', 'BS', '資産合計', 40),
  (6, 'net_assets', 'BS', '純資産合計', 90),
  (7, 'total_liabilities', 'BS', '負債合計', 70),
  (8, 'operating_cf', 'CF', '営業活動によるCF', 180),
  (9, 'investing_cf', 'CF', '投資活動によるCF', 190);

INSERT INTO xbrl_filings (
    id, code, source, doc_id, doc_type, period_type, submitted_at,
    accounting_standard, raw_path, parse_status, created_at
) VALUES (
    1, '72030', 'tdnet', 'fix1', 'annual', 'FY', '2025-04-25 15:00:00',
    'JPGAAP', '7203/fix.zip', 'parsed', '2026-09-07T00:00:00'
);

-- yen (unit JPY) except where noted. total_assets omitted on purpose (missing_excluded).
-- non-consolidated net_sales is a decoy; match_rate prefers consolidated.
INSERT INTO xbrl_financials (
    code, filing_id, std_item_id, is_consolidated, period_type,
    period_end, value, unit, generated_at
) VALUES
  ('72030', 1, 1, 1, 'FY', '2025-03-31', 100000000, 'JPY', '2026-09-07T00:00:00'),
  ('72030', 1, 1, 0, 'FY', '2025-03-31', 1, 'JPY', '2026-09-07T00:00:00'),
  ('72030', 1, 2, 1, 'FY', '2025-03-31', 50000000, 'JPY', '2026-09-07T00:00:00'),
  ('72030', 1, 3, 1, 'FY', '2025-03-31', 40400000, 'JPY', '2026-09-07T00:00:00'),
  ('72030', 1, 4, 1, 'FY', '2025-03-31', 10010000000, 'JPY', '2026-09-07T00:00:00'),
  ('72030', 1, 6, 1, 'FY', '2025-03-31', 200000000, 'JPY', '2026-09-07T00:00:00'),
  ('72030', 1, 7, 1, 'FY', '2025-03-31', 300000000, 'JPY', '2026-09-07T00:00:00'),
  ('72030', 1, 8, 1, 'FY', '2025-03-31', 10000000, 'JPY', '2026-09-07T00:00:00'),
  ('72030', 1, 9, 1, 'FY', '2025-03-31', -1000000, 'JPY', '2026-09-07T00:00:00');
