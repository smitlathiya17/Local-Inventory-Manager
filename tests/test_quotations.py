"""Tests for quotations, advance payments, convert-to-sale, committed stock."""
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


def _add_customer(conn, name='Alice'):
    cur = conn.execute("INSERT INTO customers (name) VALUES (?)", (name,))
    conn.commit()
    return cur.lastrowid


def _add_quotation(conn, customer_id, lines, status='Draft', qnum='Q-0001'):
    """lines: [(product_id, grams, price_paise)]"""
    cur = conn.execute(
        "INSERT INTO quotations (quote_number, customer_id, date, status) VALUES (?,?,?,?)",
        (qnum, customer_id, '2026-10-26', status),
    )
    qid = cur.lastrowid
    for pid, grams, price in lines:
        conn.execute(
            "INSERT INTO quotation_lines (quotation_id, product_id, grams, price_per_kg_paise) VALUES (?,?,?,?)",
            (qid, pid, grams, price),
        )
    conn.commit()
    return qid


def _stock(conn, product_id):
    return conn.execute(
        "SELECT COALESCE(SUM(grams),0) FROM stock_movements WHERE product_id=?",
        (product_id,)
    ).fetchone()[0]


def _committed(conn, product_id):
    return conn.execute("""
        SELECT COALESCE(SUM(ql.grams), 0)
        FROM quotation_lines ql
        JOIN quotations q ON ql.quotation_id = q.id
        WHERE ql.product_id=? AND q.status='Accepted'
    """, (product_id,)).fetchone()[0]


class TestCommittedStock:
    def test_accepted_quotation_shows_committed(self, tmp_path):
        db = _make_db(tmp_path)
        pid = _add_product(db, opening_g=5000)
        cid = _add_customer(db)
        _add_quotation(db, cid, [(pid, 3000, 50000)], status='Accepted')
        assert _committed(db, pid) == 3000

    def test_draft_not_committed(self, tmp_path):
        db = _make_db(tmp_path)
        pid = _add_product(db, opening_g=5000)
        cid = _add_customer(db)
        _add_quotation(db, cid, [(pid, 3000, 50000)], status='Draft')
        assert _committed(db, pid) == 0

    def test_converted_not_committed(self, tmp_path):
        db = _make_db(tmp_path)
        pid = _add_product(db, opening_g=5000)
        cid = _add_customer(db)
        _add_quotation(db, cid, [(pid, 3000, 50000)], status='Converted')
        assert _committed(db, pid) == 0

    def test_rejected_not_committed(self, tmp_path):
        db = _make_db(tmp_path)
        pid = _add_product(db, opening_g=5000)
        cid = _add_customer(db)
        _add_quotation(db, cid, [(pid, 3000, 50000)], status='Rejected')
        assert _committed(db, pid) == 0

    def test_committed_stock_does_not_reduce_actual_stock(self, tmp_path):
        db = _make_db(tmp_path)
        pid = _add_product(db, opening_g=5000)
        cid = _add_customer(db)
        _add_quotation(db, cid, [(pid, 3000, 50000)], status='Accepted')
        # Actual stock unchanged
        assert _stock(db, pid) == 5000
        assert _committed(db, pid) == 3000


class TestConvertToSale:
    def _convert(self, conn, quot_id, product_id, sale_date='2026-10-26', inv='INV-0001'):
        lines = conn.execute(
            "SELECT * FROM quotation_lines WHERE quotation_id=?", (quot_id,)
        ).fetchall()
        quot = conn.execute("SELECT * FROM quotations WHERE id=?", (quot_id,)).fetchone()
        cur = conn.execute(
            "INSERT INTO sales (invoice_number, customer_id, date, notes) VALUES (?,?,?,?)",
            (inv, quot['customer_id'], sale_date, f'Converted from {quot["quote_number"]}'),
        )
        sale_id = cur.lastrowid
        for line in lines:
            conn.execute(
                "INSERT INTO sale_lines (sale_id, product_id, grams, price_per_kg_paise) VALUES (?,?,?,?)",
                (sale_id, line['product_id'], line['grams'], line['price_per_kg_paise']),
            )
            conn.execute(
                "INSERT INTO stock_movements (product_id, type, grams, ref_type, ref_id, date) VALUES (?,?,?,?,?,?)",
                (line['product_id'], 'sale', -line['grams'], 'sale', sale_id, sale_date),
            )
        conn.execute(
            "UPDATE payments SET sale_id=? WHERE quotation_id=? AND voided=0",
            (sale_id, quot_id),
        )
        conn.execute(
            "UPDATE quotations SET status='Converted', converted_sale_id=? WHERE id=?",
            (sale_id, quot_id),
        )
        conn.commit()
        return sale_id

    def test_convert_reduces_stock(self, tmp_path):
        db = _make_db(tmp_path)
        pid = _add_product(db, opening_g=5000)
        cid = _add_customer(db)
        qid = _add_quotation(db, cid, [(pid, 2000, 50000)], status='Accepted')
        self._convert(db, qid, pid)
        assert _stock(db, pid) == 3000

    def test_convert_clears_committed(self, tmp_path):
        db = _make_db(tmp_path)
        pid = _add_product(db, opening_g=5000)
        cid = _add_customer(db)
        qid = _add_quotation(db, cid, [(pid, 2000, 50000)], status='Accepted')
        assert _committed(db, pid) == 2000
        self._convert(db, qid, pid)
        assert _committed(db, pid) == 0  # status='Converted' now

    def test_advance_payment_linked_to_sale(self, tmp_path):
        db = _make_db(tmp_path)
        pid = _add_product(db, opening_g=5000)
        cid = _add_customer(db)
        qid = _add_quotation(db, cid, [(pid, 2000, 50000)], status='Accepted')
        # Record advance
        db.execute(
            "INSERT INTO payments (customer_id, quotation_id, paise, date, method) VALUES (?,?,?,?,?)",
            (cid, qid, 20000, '2026-10-26', 'cash'),
        )
        db.commit()
        sale_id = self._convert(db, qid, pid)
        # Advance should now be linked to the sale
        pay = db.execute(
            "SELECT sale_id FROM payments WHERE quotation_id=? AND voided=0", (qid,)
        ).fetchone()
        assert pay['sale_id'] == sale_id

    def test_convert_marks_quotation_converted(self, tmp_path):
        db = _make_db(tmp_path)
        pid = _add_product(db, opening_g=5000)
        cid = _add_customer(db)
        qid = _add_quotation(db, cid, [(pid, 2000, 50000)], status='Accepted')
        sale_id = self._convert(db, qid, pid)
        quot = db.execute("SELECT status, converted_sale_id FROM quotations WHERE id=?", (qid,)).fetchone()
        assert quot['status'] == 'Converted'
        assert quot['converted_sale_id'] == sale_id


class TestAdvancePayment:
    def test_advance_reduces_outstanding(self, tmp_path):
        db = _make_db(tmp_path)
        pid = _add_product(db, opening_g=5000)
        cid = _add_customer(db)
        qid = _add_quotation(db, cid, [(pid, 2000, 50000)], status='Accepted')
        # Record advance linked to quotation
        db.execute(
            "INSERT INTO payments (customer_id, quotation_id, paise, date, method) VALUES (?,?,?,?,?)",
            (cid, qid, 30000, '2026-10-26', 'cash'),
        )
        db.commit()
        paid = db.execute(
            "SELECT COALESCE(SUM(paise),0) FROM payments WHERE quotation_id=? AND voided=0",
            (qid,)
        ).fetchone()[0]
        assert paid == 30000
