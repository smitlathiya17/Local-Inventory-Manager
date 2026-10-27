-- Migration 005: quotations, orders, advance payments

CREATE TABLE IF NOT EXISTS quotations (
    id                INTEGER PRIMARY KEY,
    quote_number      TEXT    NOT NULL UNIQUE,
    customer_id       INTEGER REFERENCES customers(id),
    date              TEXT    NOT NULL,
    expiry_date       TEXT,
    status            TEXT    NOT NULL DEFAULT 'Draft',
    notes             TEXT,
    converted_sale_id INTEGER REFERENCES sales(id),
    created_at        TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS quotation_lines (
    id                 INTEGER PRIMARY KEY,
    quotation_id       INTEGER NOT NULL REFERENCES quotations(id),
    product_id         INTEGER NOT NULL REFERENCES products(id),
    grams              INTEGER NOT NULL,
    price_per_kg_paise INTEGER NOT NULL DEFAULT 0
);

-- Track advance payments against a quotation/order
ALTER TABLE payments ADD COLUMN quotation_id INTEGER;

CREATE INDEX IF NOT EXISTS idx_quot_lines_quot ON quotation_lines(quotation_id);
CREATE INDEX IF NOT EXISTS idx_quot_lines_prod ON quotation_lines(product_id);
CREATE INDEX IF NOT EXISTS idx_payments_quot   ON payments(quotation_id);
