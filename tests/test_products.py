"""Tests for stock ledger, adjustments and product operations."""
import sqlite3
import tempfile
import os
import pytest
from pathlib import Path


def _make_db(tmp_path: Path) -> sqlite3.Connection:
    """Create an in-memory-equivalent test DB with migrations applied."""
    db_path = tmp_path / 'test.db'
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")

    migrations_dir = Path(__file__).parent.parent / 'migrations'
    conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)")
    conn.commit()
    for mf in sorted(migrations_dir.glob('*.sql')):
        conn.executescript(mf.read_text(encoding='utf-8'))
    conn.execute("INSERT OR IGNORE INTO schema_version (version) VALUES (999)")
    conn.commit()
    return conn


def _add_product(conn, sku='TEST-001', opening_g=0):
    cur = conn.execute(
        "INSERT INTO products (sku, name, price_per_kg_paise) VALUES (?,?,?)",
        (sku, f'Test {sku}', 55000),
    )
    pid = cur.lastrowid
    if opening_g:
        conn.execute(
            "INSERT INTO stock_movements (product_id, type, grams, date) VALUES (?,?,?,?)",
            (pid, 'opening', opening_g, '2026-10-26'),
        )
    conn.commit()
    return pid


def _stock(conn, product_id):
    row = conn.execute(
        "SELECT COALESCE(SUM(grams),0) AS g FROM stock_movements WHERE product_id=?",
        (product_id,),
    ).fetchone()
    return row['g']


class TestStockLedger:
    def test_new_product_zero_stock(self, tmp_path):
        db = _make_db(tmp_path)
        pid = _add_product(db)
        assert _stock(db, pid) == 0

    def test_opening_stock(self, tmp_path):
        db = _make_db(tmp_path)
        pid = _add_product(db, opening_g=2500)
        assert _stock(db, pid) == 2500

    def test_adjustment_adds(self, tmp_path):
        db = _make_db(tmp_path)
        pid = _add_product(db, opening_g=1000)
        db.execute(
            "INSERT INTO stock_movements (product_id, type, grams, date) VALUES (?,?,?,?)",
            (pid, 'adjustment', 500, '2026-10-26'),
        )
        db.commit()
        assert _stock(db, pid) == 1500

    def test_adjustment_removes(self, tmp_path):
        db = _make_db(tmp_path)
        pid = _add_product(db, opening_g=2000)
        db.execute(
            "INSERT INTO stock_movements (product_id, type, grams, date) VALUES (?,?,?,?)",
            (pid, 'adjustment', -500, '2026-10-26'),
        )
        db.commit()
        assert _stock(db, pid) == 1500

    def test_multiple_movements_sum(self, tmp_path):
        db = _make_db(tmp_path)
        pid = _add_product(db, opening_g=1000)
        for g in (500, 300, -200, -100):
            db.execute(
                "INSERT INTO stock_movements (product_id, type, grams, date) VALUES (?,?,?,?)",
                (pid, 'adjustment', g, '2026-10-26'),
            )
        db.commit()
        assert _stock(db, pid) == 1500   # 1000+500+300-200-100

    def test_stock_can_go_negative(self, tmp_path):
        db = _make_db(tmp_path)
        pid = _add_product(db, opening_g=100)
        db.execute(
            "INSERT INTO stock_movements (product_id, type, grams, date) VALUES (?,?,?,?)",
            (pid, 'adjustment', -500, '2026-10-26'),
        )
        db.commit()
        assert _stock(db, pid) == -400

    def test_sale_movement(self, tmp_path):
        db = _make_db(tmp_path)
        pid = _add_product(db, opening_g=5000)
        db.execute(
            "INSERT INTO stock_movements (product_id, type, grams, date) VALUES (?,?,?,?)",
            (pid, 'sale', -2000, '2026-10-26'),
        )
        db.commit()
        assert _stock(db, pid) == 3000

    def test_sale_void_reversal(self, tmp_path):
        db = _make_db(tmp_path)
        pid = _add_product(db, opening_g=5000)
        db.execute(
            "INSERT INTO stock_movements (product_id, type, grams, date) VALUES (?,?,?,?)",
            (pid, 'sale', -2000, '2026-10-26'),
        )
        db.execute(
            "INSERT INTO stock_movements (product_id, type, grams, date) VALUES (?,?,?,?)",
            (pid, 'sale_void', 2000, '2026-10-26'),
        )
        db.commit()
        assert _stock(db, pid) == 5000

    def test_ledger_isolates_products(self, tmp_path):
        db = _make_db(tmp_path)
        pid1 = _add_product(db, 'P1', opening_g=1000)
        pid2 = _add_product(db, 'P2', opening_g=2000)
        db.execute(
            "INSERT INTO stock_movements (product_id, type, grams, date) VALUES (?,?,?,?)",
            (pid1, 'adjustment', 500, '2026-10-26'),
        )
        db.commit()
        assert _stock(db, pid1) == 1500
        assert _stock(db, pid2) == 2000


class TestLowStock:
    def test_low_stock_detected(self, tmp_path):
        db = _make_db(tmp_path)
        pid = _add_product(db, opening_g=300)
        db.execute("UPDATE products SET reorder_grams=500 WHERE id=?", (pid,))
        db.commit()
        stock = _stock(db, pid)
        reorder = db.execute("SELECT reorder_grams FROM products WHERE id=?", (pid,)).fetchone()['reorder_grams']
        assert stock <= reorder

    def test_not_low_when_adequate(self, tmp_path):
        db = _make_db(tmp_path)
        pid = _add_product(db, opening_g=1000)
        db.execute("UPDATE products SET reorder_grams=500 WHERE id=?", (pid,))
        db.commit()
        stock = _stock(db, pid)
        reorder = db.execute("SELECT reorder_grams FROM products WHERE id=?", (pid,)).fetchone()['reorder_grams']
        assert stock > reorder
