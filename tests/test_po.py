"""Tests for purchase order receiving and stock movements."""
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


def _add_supplier(conn, name='Test Supplier'):
    cur = conn.execute("INSERT INTO suppliers (name) VALUES (?)", (name,))
    conn.commit()
    return cur.lastrowid


def _add_product(conn, sku='P1', opening_g=0):
    cur = conn.execute(
        "INSERT INTO products (sku, name, cost_per_kg_paise) VALUES (?,?,?)",
        (sku, f'Product {sku}', 30000),
    )
    pid = cur.lastrowid
    if opening_g:
        conn.execute(
            "INSERT INTO stock_movements (product_id, type, grams, date) VALUES (?,?,?,?)",
            (pid, 'opening', opening_g, '2026-10-26'),
        )
    conn.commit()
    return pid


def _add_po(conn, supplier_id, lines):
    cur = conn.execute(
        "INSERT INTO purchase_orders (po_number, supplier_id, date, status) VALUES (?,?,?,?)",
        ('PO-0001', supplier_id, '2026-10-26', 'Ordered'),
    )
    po_id = cur.lastrowid
    for product_id, grams, cost in lines:
        conn.execute(
            "INSERT INTO po_lines (po_id, product_id, grams, cost_per_kg_paise) VALUES (?,?,?,?)",
            (po_id, product_id, grams, cost),
        )
    conn.commit()
    return po_id


def _receive(conn, po_id, recv_map, date='2026-10-26'):
    """recv_map: {po_line_id: grams}. Returns receipt_id."""
    cur = conn.execute(
        "INSERT INTO receipts (po_id, date) VALUES (?,?)", (po_id, date)
    )
    receipt_id = cur.lastrowid
    for line_id, grams in recv_map.items():
        if grams == 0:
            continue
        line = conn.execute("SELECT * FROM po_lines WHERE id=?", (line_id,)).fetchone()
        conn.execute(
            """INSERT INTO receipt_lines (receipt_id, po_line_id, product_id, grams, cost_per_kg_paise)
               VALUES (?,?,?,?,?)""",
            (receipt_id, line_id, line['product_id'], grams, line['cost_per_kg_paise']),
        )
        conn.execute(
            """INSERT INTO stock_movements (product_id, type, grams, ref_type, ref_id, date, note)
               VALUES (?,?,?,?,?,?,?)""",
            (line['product_id'], 'purchase_receipt', grams, 'receipt', receipt_id, date, 'PO-0001'),
        )
        conn.execute(
            "UPDATE po_lines SET received_grams = received_grams + ? WHERE id=?",
            (grams, line_id),
        )
    conn.commit()
    return receipt_id


def _stock(conn, product_id):
    return conn.execute(
        "SELECT COALESCE(SUM(grams),0) AS g FROM stock_movements WHERE product_id=?",
        (product_id,),
    ).fetchone()['g']


class TestPOReceiving:
    def test_full_receive_adds_stock(self, tmp_path):
        db = _make_db(tmp_path)
        sid = _add_supplier(db)
        pid = _add_product(db)
        po_id = _add_po(db, sid, [(pid, 5000, 30000)])
        line = db.execute("SELECT id FROM po_lines WHERE po_id=?", (po_id,)).fetchone()
        _receive(db, po_id, {line['id']: 5000})
        assert _stock(db, pid) == 5000

    def test_partial_receive(self, tmp_path):
        db = _make_db(tmp_path)
        sid = _add_supplier(db)
        pid = _add_product(db)
        po_id = _add_po(db, sid, [(pid, 5000, 30000)])
        line = db.execute("SELECT id FROM po_lines WHERE po_id=?", (po_id,)).fetchone()
        _receive(db, po_id, {line['id']: 2000})
        assert _stock(db, pid) == 2000
        remaining = db.execute(
            "SELECT grams - received_grams AS rem FROM po_lines WHERE id=?", (line['id'],)
        ).fetchone()['rem']
        assert remaining == 3000

    def test_two_partial_receipts_sum(self, tmp_path):
        db = _make_db(tmp_path)
        sid = _add_supplier(db)
        pid = _add_product(db)
        po_id = _add_po(db, sid, [(pid, 5000, 30000)])
        line = db.execute("SELECT id FROM po_lines WHERE po_id=?", (po_id,)).fetchone()
        _receive(db, po_id, {line['id']: 2000})
        _receive(db, po_id, {line['id']: 3000})
        assert _stock(db, pid) == 5000
        received = db.execute(
            "SELECT received_grams FROM po_lines WHERE id=?", (line['id'],)
        ).fetchone()['received_grams']
        assert received == 5000

    def test_multiple_products_in_one_po(self, tmp_path):
        db = _make_db(tmp_path)
        sid = _add_supplier(db)
        pid1 = _add_product(db, 'P1')
        pid2 = _add_product(db, 'P2')
        po_id = _add_po(db, sid, [(pid1, 3000, 30000), (pid2, 4000, 25000)])
        lines = db.execute("SELECT id, product_id FROM po_lines WHERE po_id=?", (po_id,)).fetchall()
        recv_map = {l['id']: l['product_id'] == pid1 and 3000 or 4000 for l in lines}
        _receive(db, po_id, recv_map)
        assert _stock(db, pid1) == 3000
        assert _stock(db, pid2) == 4000

    def test_receipt_adds_to_existing_stock(self, tmp_path):
        db = _make_db(tmp_path)
        sid = _add_supplier(db)
        pid = _add_product(db, opening_g=1000)
        po_id = _add_po(db, sid, [(pid, 2000, 30000)])
        line = db.execute("SELECT id FROM po_lines WHERE po_id=?", (po_id,)).fetchone()
        _receive(db, po_id, {line['id']: 2000})
        assert _stock(db, pid) == 3000

    def test_stock_movement_type_is_purchase_receipt(self, tmp_path):
        db = _make_db(tmp_path)
        sid = _add_supplier(db)
        pid = _add_product(db)
        po_id = _add_po(db, sid, [(pid, 5000, 30000)])
        line = db.execute("SELECT id FROM po_lines WHERE po_id=?", (po_id,)).fetchone()
        _receive(db, po_id, {line['id']: 5000})
        mv = db.execute(
            "SELECT type FROM stock_movements WHERE product_id=? AND type='purchase_receipt'",
            (pid,),
        ).fetchone()
        assert mv is not None

    def test_received_grams_updated_on_po_line(self, tmp_path):
        db = _make_db(tmp_path)
        sid = _add_supplier(db)
        pid = _add_product(db)
        po_id = _add_po(db, sid, [(pid, 5000, 30000)])
        line = db.execute("SELECT id FROM po_lines WHERE po_id=?", (po_id,)).fetchone()
        _receive(db, po_id, {line['id']: 1500})
        updated = db.execute(
            "SELECT received_grams FROM po_lines WHERE id=?", (line['id'],)
        ).fetchone()['received_grams']
        assert updated == 1500
