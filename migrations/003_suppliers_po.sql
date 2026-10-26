-- Migration 003: suppliers, purchase orders, PO lines, receipts

CREATE TABLE suppliers (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    name    TEXT NOT NULL,
    contact TEXT NOT NULL DEFAULT '',
    phone   TEXT NOT NULL DEFAULT '',
    address TEXT NOT NULL DEFAULT '',
    notes   TEXT NOT NULL DEFAULT ''
);

CREATE TABLE purchase_orders (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    po_number      TEXT NOT NULL UNIQUE,
    supplier_id    INTEGER REFERENCES suppliers(id),
    date           TEXT NOT NULL,
    expected_date  TEXT,
    status         TEXT NOT NULL DEFAULT 'Draft'
                   CHECK(status IN ('Draft','Ordered','Partly received','Received','Cancelled')),
    notes          TEXT NOT NULL DEFAULT ''
);

CREATE INDEX idx_po_supplier ON purchase_orders(supplier_id);

CREATE TABLE po_lines (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    po_id             INTEGER NOT NULL REFERENCES purchase_orders(id),
    product_id        INTEGER NOT NULL REFERENCES products(id),
    grams             INTEGER NOT NULL,
    cost_per_kg_paise INTEGER NOT NULL DEFAULT 0,
    received_grams    INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX idx_po_lines_po ON po_lines(po_id);

CREATE TABLE receipts (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    po_id      INTEGER NOT NULL REFERENCES purchase_orders(id),
    date       TEXT NOT NULL,
    notes      TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE receipt_lines (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    receipt_id        INTEGER NOT NULL REFERENCES receipts(id),
    po_line_id        INTEGER NOT NULL REFERENCES po_lines(id),
    product_id        INTEGER NOT NULL REFERENCES products(id),
    grams             INTEGER NOT NULL,
    cost_per_kg_paise INTEGER NOT NULL DEFAULT 0
);
