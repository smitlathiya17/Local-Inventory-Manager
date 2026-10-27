"""Tests for stock count apply logic and total stock calculations."""
import sqlite3
import pytest
from pathlib import Path


def _make_db(tmp_path: Path):
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


def _add_product(conn, sku='P1', opening_g=0, cost_paise=30000):
    cur = conn.execute(
        "INSERT INTO products (sku, name, cost_per_kg_paise, price_per_kg_paise) VALUES (?,?,?,?)",
        (sku, f'Product {sku}', cost_paise, 50000),
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
    return conn.execute(
        "SELECT COALESCE(SUM(grams),0) FROM stock_movements WHERE product_id=?",
        (product_id,)
    ).fetchone()[0]


def _start_count(conn, name='Test Count', date='2026-10-26'):
    products = conn.execute("""
        SELECT p.id, COALESCE(SUM(sm.grams),0) AS stock_grams
        FROM products p
        LEFT JOIN stock_movements sm ON sm.product_id = p.id
        WHERE p.active = 1
        GROUP BY p.id
    """).fetchall()
    cur = conn.execute(
        "INSERT INTO stock_counts (name, date) VALUES (?,?)", (name, date)
    )
    count_id = cur.lastrowid
    for p in products:
        conn.execute(
            "INSERT INTO stock_count_lines (count_id, product_id, system_grams) VALUES (?,?,?)",
            (count_id, p['id'], p['stock_grams']),
        )
    conn.commit()
    return count_id


def _apply_count(conn, count_id):
    """Simulate route logic: write diff_grams, create corrections, mark Applied."""
    lines = conn.execute(
        "SELECT id, product_id, system_grams, counted_grams FROM stock_count_lines WHERE count_id=? AND counted_grams IS NOT NULL",
        (count_id,)
    ).fetchall()
    count = conn.execute("SELECT * FROM stock_counts WHERE id=?", (count_id,)).fetchone()
    corrections = 0
    with conn:
        for line in lines:
            diff = line['counted_grams'] - line['system_grams']
            conn.execute(
                "UPDATE stock_count_lines SET diff_grams=? WHERE id=?",
                (diff, line['id']),
            )
            if diff != 0:
                conn.execute(
                    """INSERT INTO stock_movements (product_id, type, grams, ref_type, ref_id, date, note)
                       VALUES (?,?,?,?,?,?,?)""",
                    (line['product_id'], 'count_correction', diff,
                     'stock_count', count_id, count['date'], count['name']),
                )
                corrections += 1
        conn.execute("UPDATE stock_counts SET status='Applied' WHERE id=?", (count_id,))
    return corrections


class TestCountSnapshot:
    def test_snapshot_captures_current_stock(self, tmp_path):
        db = _make_db(tmp_path)
        pid = _add_product(db, opening_g=5000)
        count_id = _start_count(db)
        line = db.execute(
            "SELECT system_grams FROM stock_count_lines WHERE count_id=? AND product_id=?",
            (count_id, pid),
        ).fetchone()
        assert line['system_grams'] == 5000

    def test_snapshot_all_active_products(self, tmp_path):
        db = _make_db(tmp_path)
        pid1 = _add_product(db, 'P1', opening_g=1000)
        pid2 = _add_product(db, 'P2', opening_g=2000)
        count_id = _start_count(db)
        lines = db.execute(
            "SELECT * FROM stock_count_lines WHERE count_id=?", (count_id,)
        ).fetchall()
        assert len(lines) == 2

    def test_snapshot_inactive_product_excluded(self, tmp_path):
        db = _make_db(tmp_path)
        pid = _add_product(db, 'P1', opening_g=1000)
        db.execute("UPDATE products SET active=0 WHERE id=?", (pid,))
        db.commit()
        count_id = _start_count(db)
        lines = db.execute(
            "SELECT * FROM stock_count_lines WHERE count_id=?", (count_id,)
        ).fetchall()
        assert len(lines) == 0


class TestCountApply:
    def test_apply_positive_diff_increases_stock(self, tmp_path):
        db = _make_db(tmp_path)
        pid = _add_product(db, opening_g=5000)
        count_id = _start_count(db)
        line_id = db.execute(
            "SELECT id FROM stock_count_lines WHERE count_id=? AND product_id=?",
            (count_id, pid),
        ).fetchone()['id']
        db.execute("UPDATE stock_count_lines SET counted_grams=6000 WHERE id=?", (line_id,))
        db.commit()
        _apply_count(db, count_id)
        assert _stock(db, pid) == 6000

    def test_apply_negative_diff_decreases_stock(self, tmp_path):
        db = _make_db(tmp_path)
        pid = _add_product(db, opening_g=5000)
        count_id = _start_count(db)
        line_id = db.execute(
            "SELECT id FROM stock_count_lines WHERE count_id=? AND product_id=?",
            (count_id, pid),
        ).fetchone()['id']
        db.execute("UPDATE stock_count_lines SET counted_grams=3000 WHERE id=?", (line_id,))
        db.commit()
        _apply_count(db, count_id)
        assert _stock(db, pid) == 3000

    def test_apply_zero_diff_no_movement(self, tmp_path):
        db = _make_db(tmp_path)
        pid = _add_product(db, opening_g=5000)
        count_id = _start_count(db)
        line_id = db.execute(
            "SELECT id FROM stock_count_lines WHERE count_id=? AND product_id=?",
            (count_id, pid),
        ).fetchone()['id']
        db.execute("UPDATE stock_count_lines SET counted_grams=5000 WHERE id=?", (line_id,))
        db.commit()
        corrections = _apply_count(db, count_id)
        assert corrections == 0
        movements = db.execute(
            "SELECT * FROM stock_movements WHERE product_id=? AND type='count_correction'", (pid,)
        ).fetchall()
        assert len(movements) == 0

    def test_apply_sets_status_applied(self, tmp_path):
        db = _make_db(tmp_path)
        pid = _add_product(db, opening_g=5000)
        count_id = _start_count(db)
        line_id = db.execute(
            "SELECT id FROM stock_count_lines WHERE count_id=? AND product_id=?",
            (count_id, pid),
        ).fetchone()['id']
        db.execute("UPDATE stock_count_lines SET counted_grams=4000 WHERE id=?", (line_id,))
        db.commit()
        _apply_count(db, count_id)
        status = db.execute(
            "SELECT status FROM stock_counts WHERE id=?", (count_id,)
        ).fetchone()['status']
        assert status == 'Applied'

    def test_apply_stores_diff_grams(self, tmp_path):
        db = _make_db(tmp_path)
        pid = _add_product(db, opening_g=5000)
        count_id = _start_count(db)
        line_id = db.execute(
            "SELECT id FROM stock_count_lines WHERE count_id=? AND product_id=?",
            (count_id, pid),
        ).fetchone()['id']
        db.execute("UPDATE stock_count_lines SET counted_grams=7000 WHERE id=?", (line_id,))
        db.commit()
        _apply_count(db, count_id)
        diff = db.execute(
            "SELECT diff_grams FROM stock_count_lines WHERE id=?", (line_id,)
        ).fetchone()['diff_grams']
        assert diff == 2000

    def test_null_lines_skipped(self, tmp_path):
        db = _make_db(tmp_path)
        pid1 = _add_product(db, 'P1', opening_g=3000)
        pid2 = _add_product(db, 'P2', opening_g=4000)
        count_id = _start_count(db)
        # Only enter count for pid1, leave pid2 as NULL
        line1 = db.execute(
            "SELECT id FROM stock_count_lines WHERE count_id=? AND product_id=?",
            (count_id, pid1),
        ).fetchone()['id']
        db.execute("UPDATE stock_count_lines SET counted_grams=2000 WHERE id=?", (line1,))
        db.commit()
        _apply_count(db, count_id)
        # pid2 stock should be unchanged
        assert _stock(db, pid2) == 4000
        # pid1 adjusted
        assert _stock(db, pid1) == 2000


class TestTotalStock:
    def test_total_stock_sums_movements(self, tmp_path):
        db = _make_db(tmp_path)
        pid = _add_product(db, opening_g=10000)
        # Add a sale movement (negative)
        db.execute(
            "INSERT INTO stock_movements (product_id, type, grams, date) VALUES (?,?,?,?)",
            (pid, 'sale', -3000, '2026-10-26'),
        )
        db.commit()
        total = db.execute(
            "SELECT COALESCE(SUM(grams),0) FROM stock_movements WHERE product_id=?", (pid,)
        ).fetchone()[0]
        assert total == 7000

    def test_stock_value_at_cost(self, tmp_path):
        db = _make_db(tmp_path)
        # cost = 30000 paise/kg, stock = 2000g = 2kg → value = 60000 paise
        pid = _add_product(db, opening_g=2000, cost_paise=30000)
        row = db.execute("""
            SELECT COALESCE(SUM(CAST(sm.grams AS REAL) * p.cost_per_kg_paise / 1000.0), 0) AS value_p
            FROM stock_movements sm
            JOIN products p ON sm.product_id = p.id
            WHERE p.id = ?
        """, (pid,)).fetchone()
        assert int(row['value_p']) == 60000
