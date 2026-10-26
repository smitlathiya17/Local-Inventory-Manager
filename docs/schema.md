# Database schema

All data lives in `beads.db` inside the user-chosen data folder.

## Conventions
- Weights are stored as **integer grams** (kg × 1000, rounded half-up).
- Prices / amounts are stored as **integer paise** (₹ × 100, rounded half-up).
- Dates are stored as ISO text `YYYY-MM-DD`.
- No hard deletes of financial records — void/deactivate instead.

## schema_version
| column  | type    | notes                     |
|---------|---------|---------------------------|
| version | INTEGER | single row, current migration number |

## settings
| column | type | notes            |
|--------|------|------------------|
| key    | TEXT | PRIMARY KEY      |
| value  | TEXT | serialised value |

Default keys: `business_name`, `business_address`, `business_phone`,
`invoice_prefix`, `invoice_next`, `po_prefix`, `po_next`,
`quotation_prefix`, `quotation_next`, `low_stock_default`,
`allow_negative_stock`, `backup_retention`, `label_preset_default`.

---

Tables for products, stock_movements, suppliers, purchase_orders, customers,
sales, quotations, payments, stock_counts, etc. are added in migrations
002–onwards (milestones 2–9).
