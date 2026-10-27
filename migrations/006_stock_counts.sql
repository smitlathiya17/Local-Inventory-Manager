-- Migration 006: stock counts

CREATE TABLE IF NOT EXISTS stock_counts (
    id         INTEGER PRIMARY KEY,
    name       TEXT    NOT NULL DEFAULT '',
    status     TEXT    NOT NULL DEFAULT 'Open',
    date       TEXT    NOT NULL,
    created_at TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS stock_count_lines (
    id            INTEGER PRIMARY KEY,
    count_id      INTEGER NOT NULL REFERENCES stock_counts(id),
    product_id    INTEGER NOT NULL REFERENCES products(id),
    system_grams  INTEGER NOT NULL DEFAULT 0,
    counted_grams INTEGER,
    diff_grams    INTEGER
);

CREATE INDEX IF NOT EXISTS idx_scl_count   ON stock_count_lines(count_id);
CREATE INDEX IF NOT EXISTS idx_scl_product ON stock_count_lines(product_id);
