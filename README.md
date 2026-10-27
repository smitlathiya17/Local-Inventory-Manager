# Beads Manager

Offline inventory and sales manager for a beads business. Runs as a local web app; all data is stored in a single SQLite file that lives on a USB pendrive.

## Features

- **Products** — SKU, material, shape, size, colour, cost & sell price, reorder alerts
- **Record Purchase (Challan)** — receive stock, update costs, print challan
- **Sales & invoices** — line items, customer ledger, payment tracking, printable invoice
- **Customers** — outstanding balance, payment history, printable statement
- **Suppliers** — contact details
- **Total stock** — grouped by material / shape / size / colour
- **Stock count** — physical count with automatic adjustments
- **Excel import/export** — bulk import products, purchases, sales; export reports
- **Barcode scanner** — USB scanners work out of the box (keyboard-wedge mode)
- **Backups** — auto-backup on every startup, restore from Settings

---

## Running in development (Mac / Linux / Windows)

**Requirements:** Python 3.9+

```bash
# 1. Create and activate a virtual environment
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Start the app
python run.py
```

The browser opens automatically at `http://127.0.0.1:5050`.  
On first run, choose a data folder (any folder, including a pendrive path).

---

## Running on Windows (packaged .exe)

1. Download `BeadsManager-windows.zip` from the [latest release](../../releases/latest)
2. Extract the zip to your USB pendrive (or any folder)
3. Double-click `Start.bat` — or run `BeadsManager.exe` directly

The app opens in your default browser. Use **Close & safe remove** in the sidebar before unplugging the pendrive.

---

## Building the Windows exe yourself

```bash
pip install -r requirements.txt
pyinstaller beads_manager.spec
# Output: dist/BeadsManager/
```

---

## Running tests

```bash
pytest tests/ -q
```

---

## Data folder layout

```
<your-data-folder>/
├── beads.db          ← all data (SQLite)
├── beads.lock        ← prevents two instances opening the same folder
├── backups/          ← automatic backups (beads_YYYYMMDD_HHMMss_auto.db)
├── exports/          ← Excel exports land here
└── logs/
```

---

## Releasing a new version

Push a tag in `vX.Y.Z` format — GitHub Actions builds the exe and creates a release automatically:

```bash
git tag v1.2.0
git push origin v1.2.0
```
