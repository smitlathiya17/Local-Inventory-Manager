"""Tests for sales, payments, customer ledger."""
import pytest
from pathlib import Path


def _make_db(tmp_path: Path):
    import sqlite3
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


def _add_product(conn, sku='P1', opening_g=0, price_paise=50000):
    cur = conn.execute(
        "INSERT INTO products (sku, name, cost_per_kg_paise, price_per_kg_paise) VALUES (?,?,?,?)",
        (sku, f'Product {sku}', 30000, price_paise),
    )
    pid = cur.lastrowid
    if opening_g:
        conn.execute(
            "INSERT INTO stock_movements (product_id, type, grams, date) VALUES (?,?,?,?)",
            (pid, 'opening', opening_g, '2026-10-26'),
        )
    conn.commit()
    return pid


def _add_customer(conn, name='Test Customer'):
    cur = conn.execute("INSERT INTO customers (name) VALUES (?)", (name,))
    conn.commit()
    return cur.lastrowid


def _add_sale(conn, customer_id, lines, invoice='INV-0001', date='2026-10-26'):
    """lines: [(product_id, grams, price_paise)]"""
    cur = conn.execute(
        "INSERT INTO sales (invoice_number, customer_id, date) VALUES (?,?,?)",
        (invoice, customer_id, date),
    )
    sale_id = cur.lastrowid
    for pid, grams, price in lines:
        conn.execute(
            "INSERT INTO sale_lines (sale_id, product_id, grams, price_per_kg_paise) VALUES (?,?,?,?)",
            (sale_id, pid, grams, price),
        )
        conn.execute(
            "INSERT INTO stock_movements (product_id, type, grams, ref_type, ref_id, date) VALUES (?,?,?,?,?,?)",
            (pid, 'sale', -grams, 'sale', sale_id, date),
        )
    conn.commit()
    return sale_id


def _stock(conn, product_id):
    return conn.execute(
        "SELECT COALESCE(SUM(grams),0) FROM stock_movements WHERE product_id=?",
        (product_id,)
    ).fetchone()[0]


def _sale_total(conn, sale_id):
    return conn.execute(
        "SELECT COALESCE(SUM((price_per_kg_paise * grams + 500) / 1000), 0) FROM sale_lines WHERE sale_id=?",
        (sale_id,)
    ).fetchone()[0]


class TestSaleCreation:
    def test_sale_reduces_stock(self, tmp_path):
        db = _make_db(tmp_path)
        pid = _add_product(db, opening_g=5000)
        cid = _add_customer(db)
        _add_sale(db, cid, [(pid, 2000, 50000)])
        assert _stock(db, pid) == 3000

    def test_sale_line_total_rounds_half_up(self, tmp_path):
        db = _make_db(tmp_path)
        pid = _add_product(db, opening_g=5000)
        cid = _add_customer(db)
        # price=33333 paise/kg * 1500g = 33333*1500/1000 = 49999.5 → rounds to 50000
        sale_id = _add_sale(db, cid, [(pid, 1500, 33333)])
        total = _sale_total(db, sale_id)
        assert total == 50000

    def test_multiple_lines_total(self, tmp_path):
        db = _make_db(tmp_path)
        pid1 = _add_product(db, 'P1', opening_g=5000, price_paise=100000)
        pid2 = _add_product(db, 'P2', opening_g=5000, price_paise=200000)
        cid  = _add_customer(db)
        sale_id = _add_sale(db, cid, [(pid1, 1000, 100000), (pid2, 500, 200000)])
        # P1: 100000*1000/1000 = 100000 paise; P2: 200000*500/1000 = 100000 paise
        assert _sale_total(db, sale_id) == 200000


class TestSaleVoid:
    def test_void_reverses_stock(self, tmp_path):
        db = _make_db(tmp_path)
        pid = _add_product(db, opening_g=5000)
        cid = _add_customer(db)
        sale_id = _add_sale(db, cid, [(pid, 2000, 50000)])
        assert _stock(db, pid) == 3000

        # Void: insert sale_void movement, mark voided
        db.execute(
            "INSERT INTO stock_movements (product_id, type, grams, ref_type, ref_id, date) VALUES (?,?,?,?,?,?)",
            (pid, 'sale_void', 2000, 'sale', sale_id, '2026-10-26'),
        )
        db.execute("UPDATE sales SET voided=1 WHERE id=?", (sale_id,))
        db.commit()
        assert _stock(db, pid) == 5000

    def test_voided_sale_excluded_from_outstanding(self, tmp_path):
        db = _make_db(tmp_path)
        pid = _add_product(db, opening_g=5000)
        cid = _add_customer(db)
        sale_id = _add_sale(db, cid, [(pid, 2000, 50000)])
        db.execute("UPDATE sales SET voided=1 WHERE id=?", (sale_id,))
        db.commit()

        outstanding = db.execute("""
            SELECT COALESCE(SUM((sl.price_per_kg_paise * sl.grams + 500) / 1000), 0)
            FROM sales s JOIN sale_lines sl ON sl.sale_id = s.id
            WHERE s.customer_id=? AND s.voided=0
        """, (cid,)).fetchone()[0]
        assert outstanding == 0


class TestPayments:
    def test_payment_reduces_outstanding(self, tmp_path):
        db = _make_db(tmp_path)
        pid = _add_product(db, opening_g=5000)
        cid = _add_customer(db)
        sale_id = _add_sale(db, cid, [(pid, 2000, 50000)])  # total=100000 paise
        # Record partial payment
        db.execute(
            "INSERT INTO payments (customer_id, sale_id, paise, date, method) VALUES (?,?,?,?,?)",
            (cid, sale_id, 40000, '2026-10-26', 'cash'),
        )
        db.commit()

        invoiced = db.execute("""
            SELECT COALESCE(SUM((sl.price_per_kg_paise * sl.grams + 500) / 1000), 0)
            FROM sales s JOIN sale_lines sl ON sl.sale_id = s.id
            WHERE s.customer_id=? AND s.voided=0
        """, (cid,)).fetchone()[0]
        paid = db.execute(
            "SELECT COALESCE(SUM(paise),0) FROM payments WHERE customer_id=? AND voided=0", (cid,)
        ).fetchone()[0]
        assert invoiced - paid == 60000

    def test_full_payment_zeroes_balance(self, tmp_path):
        db = _make_db(tmp_path)
        pid = _add_product(db, opening_g=5000)
        cid = _add_customer(db)
        sale_id = _add_sale(db, cid, [(pid, 1000, 50000)])  # total=50000 paise
        db.execute(
            "INSERT INTO payments (customer_id, sale_id, paise, date, method) VALUES (?,?,?,?,?)",
            (cid, sale_id, 50000, '2026-10-26', 'cash'),
        )
        db.commit()
        invoiced = db.execute("""
            SELECT COALESCE(SUM((sl.price_per_kg_paise * sl.grams + 500) / 1000), 0)
            FROM sales s JOIN sale_lines sl ON sl.sale_id = s.id
            WHERE s.customer_id=? AND s.voided=0
        """, (cid,)).fetchone()[0]
        paid = db.execute(
            "SELECT COALESCE(SUM(paise),0) FROM payments WHERE customer_id=? AND voided=0", (cid,)
        ).fetchone()[0]
        assert invoiced - paid == 0

    def test_voided_payment_excluded(self, tmp_path):
        db = _make_db(tmp_path)
        pid = _add_product(db, opening_g=5000)
        cid = _add_customer(db)
        sale_id = _add_sale(db, cid, [(pid, 1000, 50000)])
        db.execute(
            "INSERT INTO payments (customer_id, sale_id, paise, date, method, voided) VALUES (?,?,?,?,?,1)",
            (cid, sale_id, 50000, '2026-10-26', 'cash'),
        )
        db.commit()
        paid = db.execute(
            "SELECT COALESCE(SUM(paise),0) FROM payments WHERE customer_id=? AND voided=0", (cid,)
        ).fetchone()[0]
        assert paid == 0

    def test_multiple_customers_isolated(self, tmp_path):
        db = _make_db(tmp_path)
        pid = _add_product(db, opening_g=10000)
        cid1 = _add_customer(db, 'Alice')
        cid2 = _add_customer(db, 'Bob')
        sale_id1 = _add_sale(db, cid1, [(pid, 1000, 100000)], 'INV-001')
        sale_id2 = _add_sale(db, cid2, [(pid, 2000, 100000)], 'INV-002')

        def outstanding(cid):
            inv = db.execute("""
                SELECT COALESCE(SUM((sl.price_per_kg_paise * sl.grams + 500) / 1000), 0)
                FROM sales s JOIN sale_lines sl ON sl.sale_id = s.id
                WHERE s.customer_id=? AND s.voided=0
            """, (cid,)).fetchone()[0]
            paid = db.execute(
                "SELECT COALESCE(SUM(paise),0) FROM payments WHERE customer_id=? AND voided=0", (cid,)
            ).fetchone()[0]
            return inv - paid

        assert outstanding(cid1) == 100000
        assert outstanding(cid2) == 200000
