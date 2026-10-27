from __future__ import annotations

from flask import (
    Blueprint, render_template, request, redirect,
    url_for, abort, flash,
)
from app.db import get_data_folder, get_db, mark_saved
from app.utils import (
    kg_str_to_grams,
    rupee_str_to_paise, calc_line_total_paise,
    today_iso,
)

po_bp = Blueprint('po', __name__)


def _require_folder():
    if get_data_folder() is None:
        return redirect(url_for('setup.index'))


def _next_po_number(db) -> tuple[str, int]:
    prefix = db.execute("SELECT value FROM settings WHERE key='po_prefix'").fetchone()['value']
    nxt    = int(db.execute("SELECT value FROM settings WHERE key='po_next'").fetchone()['value'])
    return f"{prefix}{nxt:04d}", nxt


def _bump_po_number(db, nxt: int) -> None:
    db.execute("UPDATE settings SET value=? WHERE key='po_next'", (str(nxt + 1),))


def _po_lines(db, po_id: int):
    rows = db.execute("""
        SELECT pl.id, pl.grams, pl.cost_per_kg_paise,
               p.id AS product_id, p.sku, p.name AS product_name
        FROM po_lines pl
        JOIN products p ON pl.product_id = p.id
        WHERE pl.po_id = ?
        ORDER BY pl.id
    """, (po_id,)).fetchall()
    return [dict(r) for r in rows]


def _products_for_form(db):
    rows = db.execute(
        "SELECT id, sku, name, cost_per_kg_paise FROM products WHERE active=1 ORDER BY name"
    ).fetchall()
    return [dict(r) for r in rows]


def _parse_po_form(form, products):
    """Parse indexed line fields. Each returned line includes form_idx for update_cost lookup."""
    errors = []
    product_map = {str(p['id']): p for p in products}
    n = int(form.get('line_count', 0))
    lines = []
    for i in range(n):
        pid  = form.get(f'product_id_{i}', '').strip()
        qty  = form.get(f'qty_kg_{i}', '').strip()
        cost = form.get(f'cost_per_kg_{i}', '').strip()
        if not pid and not qty:
            continue
        if pid not in product_map:
            errors.append(f'Line {i+1}: unknown product.')
            continue
        try:
            grams      = kg_str_to_grams(qty or '0')
            cost_paise = rupee_str_to_paise(cost or '0')
        except ValueError as e:
            errors.append(f'Line {i+1}: {e}')
            continue
        if grams <= 0:
            errors.append(f'Line {i+1}: quantity must be greater than zero.')
            continue
        lines.append({
            'form_idx':   i,
            'product_id': int(pid),
            'grams':      grams,
            'cost_paise': cost_paise,
        })
    return errors, lines


def _delete_po_movements(db, po_id: int) -> None:
    """Delete all receipts, receipt_lines, and stock_movements for a PO."""
    for r in db.execute("SELECT id FROM receipts WHERE po_id=?", (po_id,)).fetchall():
        db.execute("DELETE FROM stock_movements WHERE ref_type='receipt' AND ref_id=?", (r['id'],))
        db.execute("DELETE FROM receipt_lines WHERE receipt_id=?", (r['id'],))
    db.execute("DELETE FROM receipts WHERE po_id=?", (po_id,))


def _insert_po_lines_and_receipt(db, po_id: int, challan_number: str,
                                  challan_date: str, notes: str,
                                  lines: list, form) -> None:
    """Insert po_lines, one receipt, receipt_lines, stock_movements, optional cost updates."""
    rcpt = db.execute(
        "INSERT INTO receipts (po_id, date, notes) VALUES (?,?,?)",
        (po_id, challan_date, notes),
    )
    receipt_id = rcpt.lastrowid
    for line in lines:
        cur = db.execute(
            """INSERT INTO po_lines
               (po_id, product_id, grams, cost_per_kg_paise, received_grams)
               VALUES (?,?,?,?,?)""",
            (po_id, line['product_id'], line['grams'], line['cost_paise'], line['grams']),
        )
        db.execute(
            """INSERT INTO receipt_lines
               (receipt_id, po_line_id, product_id, grams, cost_per_kg_paise)
               VALUES (?,?,?,?,?)""",
            (receipt_id, cur.lastrowid, line['product_id'], line['grams'], line['cost_paise']),
        )
        db.execute(
            """INSERT INTO stock_movements
               (product_id, type, grams, ref_type, ref_id, date, note)
               VALUES (?,?,?,?,?,?,?)""",
            (line['product_id'], 'purchase_receipt', line['grams'],
             'receipt', receipt_id, challan_date, challan_number),
        )
        if form.get(f'update_cost_{line["form_idx"]}') == '1':
            db.execute(
                "UPDATE products SET cost_per_kg_paise=? WHERE id=?",
                (line['cost_paise'], line['product_id']),
            )


# ── list ──────────────────────────────────────────────────────────────────────

@po_bp.route('/purchase-orders')
def po_list():
    r = _require_folder()
    if r: return r
    db = get_db()
    f_supplier = request.args.get('supplier', '')

    where, params = [], []
    if f_supplier:
        where.append('po.supplier_id = ?')
        params.append(f_supplier)
    where_sql = ('WHERE ' + ' AND '.join(where)) if where else ''

    rows = db.execute(f"""
        SELECT po.id, po.po_number, po.date,
               s.name AS supplier_name,
               COALESCE(SUM(pl.grams), 0) AS total_grams,
               COALESCE(SUM((pl.cost_per_kg_paise * pl.grams + 500) / 1000), 0) AS total_paise
        FROM purchase_orders po
        LEFT JOIN suppliers s  ON po.supplier_id = s.id
        LEFT JOIN po_lines  pl ON pl.po_id = po.id
        {where_sql}
        GROUP BY po.id
        ORDER BY po.date DESC, po.id DESC
    """, params).fetchall()

    suppliers = db.execute("SELECT id, name FROM suppliers ORDER BY name").fetchall()
    return render_template('po/list.html',
        rows=rows, suppliers=suppliers, f_supplier=f_supplier,
    )


# ── new ───────────────────────────────────────────────────────────────────────

@po_bp.route('/purchase-orders/new', methods=['GET', 'POST'])
def po_new():
    r = _require_folder()
    if r: return r
    db = get_db()
    suppliers = db.execute("SELECT id, name FROM suppliers ORDER BY name").fetchall()
    products  = _products_for_form(db)
    po_number, nxt = _next_po_number(db)

    if request.method == 'POST':
        challan_number = request.form.get('challan_number', '').strip()
        errors, lines  = _parse_po_form(request.form, products)
        if not challan_number:
            errors.append('Challan number is required.')
        elif db.execute("SELECT id FROM purchase_orders WHERE po_number=?", (challan_number,)).fetchone():
            errors.append(f'Challan number "{challan_number}" already exists.')
        if not lines:
            errors.append('Add at least one product line.')
        if errors:
            for e in errors: flash(e, 'banner-error')
            return render_template('po/form.html',
                po=None, po_number=po_number, suppliers=suppliers,
                products=products, form=request.form, lines=lines,
            )

        challan_date = request.form.get('date', today_iso())
        supplier_id  = request.form.get('supplier_id') or None
        notes        = request.form.get('notes', '').strip()

        with db:
            cur = db.execute(
                "INSERT INTO purchase_orders (po_number, supplier_id, date, notes, status) VALUES (?,?,?,?,?)",
                (challan_number, supplier_id, challan_date, notes, 'Received'),
            )
            po_id = cur.lastrowid
            _insert_po_lines_and_receipt(db, po_id, challan_number,
                                         challan_date, notes, lines, request.form)
            _bump_po_number(db, nxt)

        mark_saved()
        flash(f'Challan {challan_number} recorded.', 'banner-success')
        return redirect(url_for('po.po_detail', po_id=po_id))

    return render_template('po/form.html',
        po=None, po_number=po_number, suppliers=suppliers,
        products=products, form={}, lines=[],
    )


# ── edit ──────────────────────────────────────────────────────────────────────

@po_bp.route('/purchase-orders/<int:po_id>/edit', methods=['GET', 'POST'])
def po_edit(po_id: int):
    r = _require_folder()
    if r: return r
    db = get_db()
    po = db.execute("SELECT * FROM purchase_orders WHERE id=?", (po_id,)).fetchone()
    if not po:
        abort(404)

    suppliers      = db.execute("SELECT id, name FROM suppliers ORDER BY name").fetchall()
    products       = _products_for_form(db)
    existing_lines = _po_lines(db, po_id)

    if request.method == 'POST':
        challan_number = request.form.get('challan_number', '').strip()
        errors, lines  = _parse_po_form(request.form, products)
        if not challan_number:
            errors.append('Challan number is required.')
        elif db.execute(
            "SELECT id FROM purchase_orders WHERE po_number=? AND id!=?", (challan_number, po_id)
        ).fetchone():
            errors.append(f'Challan number "{challan_number}" already exists.')
        if not lines:
            errors.append('Add at least one product line.')
        if errors:
            for e in errors: flash(e, 'banner-error')
            return render_template('po/form.html',
                po=po, po_number=po['po_number'], suppliers=suppliers,
                products=products, form=request.form, lines=lines,
            )

        challan_date = request.form.get('date', today_iso())
        supplier_id  = request.form.get('supplier_id') or None
        notes        = request.form.get('notes', '').strip()

        with db:
            # Reverse old stock movements and receipts
            _delete_po_movements(db, po_id)
            db.execute("DELETE FROM po_lines WHERE po_id=?", (po_id,))

            db.execute(
                "UPDATE purchase_orders SET po_number=?, supplier_id=?, date=?, notes=? WHERE id=?",
                (challan_number, supplier_id, challan_date, notes, po_id),
            )
            _insert_po_lines_and_receipt(db, po_id, challan_number,
                                         challan_date, notes, lines, request.form)

        mark_saved()
        flash(f'Challan {challan_number} updated. Stock levels adjusted.', 'banner-success')
        return redirect(url_for('po.po_detail', po_id=po_id))

    return render_template('po/form.html',
        po=po, po_number=po['po_number'], suppliers=suppliers,
        products=products, form={}, lines=existing_lines,
    )


# ── delete ────────────────────────────────────────────────────────────────────

@po_bp.route('/purchase-orders/<int:po_id>/delete', methods=['POST'])
def po_delete(po_id: int):
    r = _require_folder()
    if r: return r
    db = get_db()
    po = db.execute("SELECT po_number FROM purchase_orders WHERE id=?", (po_id,)).fetchone()
    if not po:
        abort(404)
    with db:
        _delete_po_movements(db, po_id)
        db.execute("DELETE FROM po_lines WHERE po_id=?", (po_id,))
        db.execute("DELETE FROM supplier_payments WHERE purchase_order_id=?", (po_id,))
        db.execute("DELETE FROM purchase_orders WHERE id=?", (po_id,))
    mark_saved()
    flash(f'Challan {po["po_number"]} deleted. Stock reversed.', 'banner-success')
    return redirect(url_for('po.po_list'))


# ── detail ────────────────────────────────────────────────────────────────────

@po_bp.route('/purchase-orders/<int:po_id>')
def po_detail(po_id: int):
    r = _require_folder()
    if r: return r
    db = get_db()
    po = db.execute("""
        SELECT po.*, s.name AS supplier_name
        FROM purchase_orders po
        LEFT JOIN suppliers s ON po.supplier_id = s.id
        WHERE po.id=?
    """, (po_id,)).fetchone()
    if not po:
        abort(404)

    lines = _po_lines(db, po_id)
    total_paise = sum(calc_line_total_paise(l['cost_per_kg_paise'], l['grams']) for l in lines)
    return render_template('po/detail.html', po=po, lines=lines, total_paise=total_paise)


# ── print ─────────────────────────────────────────────────────────────────────

@po_bp.route('/purchase-orders/<int:po_id>/print')
def po_print(po_id: int):
    r = _require_folder()
    if r: return r
    db = get_db()
    po = db.execute("""
        SELECT po.*, s.name AS supplier_name, s.address AS supplier_address,
               s.phone AS supplier_phone
        FROM purchase_orders po
        LEFT JOIN suppliers s ON po.supplier_id = s.id
        WHERE po.id=?
    """, (po_id,)).fetchone()
    if not po:
        abort(404)

    lines = _po_lines(db, po_id)
    total_paise = sum(calc_line_total_paise(l['cost_per_kg_paise'], l['grams']) for l in lines)
    settings_rows = db.execute("SELECT key, value FROM settings").fetchall()
    settings = {r['key']: r['value'] for r in settings_rows}
    return render_template('po/print.html', po=po, lines=lines,
                           total_paise=total_paise, settings=settings)
