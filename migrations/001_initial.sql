-- Migration 001: initial schema (settings only; tables for each module added in later migrations)

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL DEFAULT ''
);

INSERT OR IGNORE INTO settings (key, value) VALUES
    ('business_name',        'My Beads Business'),
    ('business_address',     ''),
    ('business_phone',       ''),
    ('invoice_prefix',       'INV-'),
    ('invoice_next',         '1'),
    ('po_prefix',            'PO-'),
    ('po_next',              '1'),
    ('quotation_prefix',     'Q-'),
    ('quotation_next',       '1'),
    ('low_stock_default',    '5'),
    ('allow_negative_stock', '0'),
    ('backup_retention',     '30'),
    ('label_preset_default', '50x25');
