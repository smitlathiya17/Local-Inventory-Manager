"""Tests for Excel import parsing logic."""
import sqlite3
import pytest
from pathlib import Path
from unittest.mock import MagicMock


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


def _make_cell(value):
    c = MagicMock()
    c.value = value
    return c


def _make_row(*values):
    return [_make_cell(v) for v in values]


def _parse(db, rows_data):
    """Call _parse_products with synthetic row data."""
    from app.routes.excel import _parse_products as _parse_rows

    class FakeWS:
        def iter_rows(self, min_row=2):
            return iter(rows_data)

    return _parse_rows(db, FakeWS())


class TestParseRows:
    def test_valid_row_parsed(self, tmp_path):
        db = _make_db(tmp_path)
        rows = _parse(db, [
            _make_row('BG-001', 'Glass Round', 'Glass', 'Round', 6, 'Blue', '', 250, 550),
        ])
        assert len(rows) == 1
        r = rows[0]
        assert r['sku']          == 'BG-001'
        assert r['name']         == 'Glass Round'
        assert r['material_str'] == 'Glass'
        assert r['cost_paise']   == 25000
        assert r['price_paise']  == 55000
        assert r['errors']       == []

    def test_missing_sku_is_error(self, tmp_path):
        db = _make_db(tmp_path)
        rows = _parse(db, [_make_row('', 'Some Name', '', '', None, '', '', 0, 0)])
        assert any('SKU' in e for e in rows[0]['errors'])

    def test_missing_name_is_error(self, tmp_path):
        db = _make_db(tmp_path)
        rows = _parse(db, [_make_row('SKU-1', '', '', '', None, '', '', 0, 0)])
        assert any('Name' in e for e in rows[0]['errors'])

    def test_duplicate_sku_in_file(self, tmp_path):
        db = _make_db(tmp_path)
        rows = _parse(db, [
            _make_row('BG-001', 'Product A', '', '', None, '', '', 0, 0),
            _make_row('BG-001', 'Product B', '', '', None, '', '', 0, 0),
        ])
        dup_errors = [r for r in rows if any('Duplicate' in e for e in r['errors'])]
        assert len(dup_errors) == 1

    def test_existing_db_sku_flagged(self, tmp_path):
        db = _make_db(tmp_path)
        db.execute("INSERT INTO products (sku, name, cost_per_kg_paise, price_per_kg_paise) VALUES ('BG-001','Existing',0,0)")
        db.commit()
        rows = _parse(db, [_make_row('BG-001', 'New Product', '', '', None, '', '', 0, 0)])
        assert any('already exists' in e for e in rows[0]['errors'])

    def test_sku_uppercased(self, tmp_path):
        db = _make_db(tmp_path)
        rows = _parse(db, [_make_row('bg-001', 'Product', '', '', None, '', '', 0, 0)])
        assert rows[0]['sku'] == 'BG-001'

    def test_blank_rows_skipped(self, tmp_path):
        db = _make_db(tmp_path)
        rows = _parse(db, [
            _make_row(None, None, None, None, None, None, None, None, None),
        ])
        assert len(rows) == 0

    def test_barcode_defaults_to_sku(self, tmp_path):
        db = _make_db(tmp_path)
        rows = _parse(db, [_make_row('BG-001', 'Product', '', '', None, '', '', 0, 0)])
        assert rows[0]['barcode'] == 'BG-001'


class TestGetOrCreateLabel:
    def test_creates_new_label(self, tmp_path):
        db = _make_db(tmp_path)
        from app.routes.excel import _get_or_create_label
        with db:
            lid = _get_or_create_label(db, 'materials', 'Crystal')
        assert lid is not None
        name = db.execute("SELECT name FROM materials WHERE id=?", (lid,)).fetchone()['name']
        assert name == 'Crystal'

    def test_returns_existing_label(self, tmp_path):
        db = _make_db(tmp_path)
        from app.routes.excel import _get_or_create_label
        with db:
            id1 = _get_or_create_label(db, 'materials', 'Glass')
            id2 = _get_or_create_label(db, 'materials', 'Glass')
        assert id1 == id2

    def test_empty_name_returns_none(self, tmp_path):
        db = _make_db(tmp_path)
        from app.routes.excel import _get_or_create_label
        assert _get_or_create_label(db, 'materials', '') is None
