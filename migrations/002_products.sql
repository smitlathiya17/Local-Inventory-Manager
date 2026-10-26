-- Migration 002: products, managed lists, stock ledger

CREATE TABLE materials (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    name   TEXT NOT NULL UNIQUE,
    active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE shapes (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    name   TEXT NOT NULL UNIQUE,
    active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE colors (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    name   TEXT NOT NULL UNIQUE,
    active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE products (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    sku                 TEXT NOT NULL UNIQUE,
    name                TEXT NOT NULL,
    material_id         INTEGER REFERENCES materials(id),
    shape_id            INTEGER REFERENCES shapes(id),
    size_mm             REAL,
    color_id            INTEGER REFERENCES colors(id),
    barcode             TEXT UNIQUE,
    cost_per_kg_paise   INTEGER NOT NULL DEFAULT 0,
    price_per_kg_paise  INTEGER NOT NULL DEFAULT 0,
    reorder_grams       INTEGER NOT NULL DEFAULT 0,
    active              INTEGER NOT NULL DEFAULT 1,
    notes               TEXT NOT NULL DEFAULT ''
);

CREATE UNIQUE INDEX idx_products_sku     ON products(sku);
CREATE UNIQUE INDEX idx_products_barcode ON products(barcode) WHERE barcode IS NOT NULL;

CREATE TABLE stock_movements (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id  INTEGER NOT NULL REFERENCES products(id),
    type        TEXT NOT NULL CHECK(type IN (
                    'opening','purchase_receipt','sale','sale_void',
                    'adjustment','count_correction')),
    grams       INTEGER NOT NULL,
    ref_type    TEXT,
    ref_id      INTEGER,
    date        TEXT NOT NULL,
    note        TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX idx_movements_product ON stock_movements(product_id);
