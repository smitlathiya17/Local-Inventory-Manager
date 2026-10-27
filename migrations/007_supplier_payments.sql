-- Supplier payments ledger (mirrors customer payments)
CREATE TABLE IF NOT EXISTS supplier_payments (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    supplier_id       INTEGER NOT NULL REFERENCES suppliers(id),
    purchase_order_id INTEGER REFERENCES purchase_orders(id),
    paise             INTEGER NOT NULL,
    date              TEXT    NOT NULL,
    method            TEXT    NOT NULL DEFAULT 'cash',
    note              TEXT    NOT NULL DEFAULT ''
);
