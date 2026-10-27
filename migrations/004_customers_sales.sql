-- Migration 004: customers, sales, payments

CREATE TABLE IF NOT EXISTS customers (
    id         INTEGER PRIMARY KEY,
    name       TEXT    NOT NULL,
    phone      TEXT,
    address    TEXT,
    notes      TEXT,
    active     INTEGER NOT NULL DEFAULT 1,
    created_at TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS sales (
    id             INTEGER PRIMARY KEY,
    invoice_number TEXT    NOT NULL UNIQUE,
    customer_id    INTEGER REFERENCES customers(id),
    date           TEXT    NOT NULL,
    notes          TEXT,
    voided         INTEGER NOT NULL DEFAULT 0,
    created_at     TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS sale_lines (
    id                INTEGER PRIMARY KEY,
    sale_id           INTEGER NOT NULL REFERENCES sales(id),
    product_id        INTEGER NOT NULL REFERENCES products(id),
    grams             INTEGER NOT NULL,
    price_per_kg_paise INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS payments (
    id          INTEGER PRIMARY KEY,
    customer_id INTEGER REFERENCES customers(id),
    sale_id     INTEGER REFERENCES sales(id),
    paise       INTEGER NOT NULL,
    date        TEXT    NOT NULL,
    method      TEXT    NOT NULL DEFAULT 'cash',
    note        TEXT,
    voided      INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_sale_lines_sale    ON sale_lines(sale_id);
CREATE INDEX IF NOT EXISTS idx_sale_lines_product ON sale_lines(product_id);
CREATE INDEX IF NOT EXISTS idx_payments_customer  ON payments(customer_id);
CREATE INDEX IF NOT EXISTS idx_payments_sale      ON payments(sale_id);
