from __future__ import annotations

from flask import (
    Blueprint, render_template, request, redirect,
    url_for, abort, flash, jsonify,
)
from app.db import get_data_folder, get_db, mark_saved
from app.utils import (
    kg_str_to_grams, grams_to_kg_str,
    rupee_str_to_paise, calc_line_total_paise,
    today_iso, format_date,
)

po_bp = Blueprint('po', __name__)

EDITABLE_STATUSES  = ('Draft',)
RECEIVABLE_STATUSES = ('Ordered', 'Partly received')


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
    return db.execute("""
        SELECT pl.id, pl.grams, pl.cost_per_kg_paise, pl.received_grams,
               p.id AS product_id, p.sku, p.name AS product_name,
               p.cost_per_kg_paise AS product_cost
        FROM po_lines pl
        JOIN products p ON pl.product_id = p.id
        WHERE pl.po_id = ?
        ORDER BY pl.id
    """, (po_id,)).fetchall()


def _update_po_status(db, po_id: int, lines) -> None:
    if not lines:
        return
    total_g    = sum(l['grams'] for l in lines)
    received_g = sum(l['received_grams'] for l in lines)
    if received_g >= total_g:
        new_status = 'Received'
    elif received_g > 0:
        new_status = 'Partly received'
    else:
        return
    db.execute("UPDATE purchase_orders SET status=? WHERE id=?", (new_status, po_id))


# ── list ──────────────────────────────────────────────────────────────────────

@po_bp.route('/purchase-orders')
def po_list():
    r = _require_folder()
    if r: return r
    db = get_db()
    f_status   = request.args.get('status', '')
    f_supplier = request.args.get('supplier', '')

    where, params = [], []
    if f_status:
        where.append('po.status = ?');       params.append(f_status)
    if f_supplier:
        where.append('po.supplier_id = ?');  params.append(f_supplier)

    where_sql = ('WHERE ' + ' AND '.join(where)) if where else ''

    rows = db.execute(f"""
        SELECT po.id, po.po_number, po.date, po.expected_date, po.status,
               s.name AS supplier_name,
               COALESCE(SUM(pl.grams), 0)          AS total_grams,
               COALESCE(SUM(pl.received_grams), 0) AS received_grams
        FROM purchase_orders po
        LEFT JOIN suppliers s  ON po.supplier_id = s.id
        LEFT JOIN po_lines  pl ON pl.po_id       = po.id
        {where_sql}
        GROUP BY po.id
        ORDER BY po.date DESC, po.id DESC
    """, params).fetchall()

    suppliers = db.execute("SELECT id, name FROM suppliers ORDER BY name").fetchall()
    statuses  = ['Draft', 'Ordered', 'Partly received', 'Received', 'Cancelled']
    return render_template('po/list.html',
        rows=rows, suppliers=suppliers, statuses=statuses,
        f_status=f_status, f_supplier=f_supplier,
    )


# ── new / edit ────────────────────────────────────────────────────────────────

@po_bp.route('/purchase-orders/new', methods=['GET', 'POST'])
def po_new():
    r = _require_folder()
    if r: return r
    db = get_db()
    suppliers = db.execute("SELECT id, name FROM suppliers ORDER BY name").fetchall()
    products  = db.execute(
        "SELECT p.id, p.sku, p.name, p.cost_per_kg_paise FROM products p WHERE p.active=1 ORDER BY p.name"
    ).fetchall()
    po_number, nxt = _next_po_number(db)

    if request.method == 'POST':
        errors, lines = _parse_po_form(request.form, products)
        if not lines:
            errors.append('Add at least one product line.')
        if errors:
            for e in errors: flash(e, 'banner-error')
            return render_template('po/form.html',
                po=None, po_number=po_number, suppliers=suppliers,
                products=products, form=request.form, lines=lines,
            )
        with db:
            cur = db.execute(
                """INSERT INTO purchase_orders
                   (po_number, supplier_id, date, expected_date, notes)
                   VALUES (?,?,?,?,?)""",
                (po_number,
                 request.form.get('supplier_id') or None,
                 request.form.get('date', today_iso()),
                 request.form.get('expected_date') or None,
                 request.form.get('notes', '').strip()),
            )
            po_id = cur.lastrowid
            for line in lines:
                db.execute(
                    "INSERT INTO po_lines (po_id, product_id, grams, cost_per_kg_paise) VALUES (?,?,?,?)",
                    (po_id, line['product_id'], line['grams'], line['cost_paise']),
                )
            _bump_po_number(db, nxt)
        mark_saved()
        flash(f'Purchase order {po_number} created.', 'banner-success')
        return redirect(url_for('po.po_detail', po_id=po_id))

    return render_template('po/form.html',
        po=None, po_number=po_number, suppliers=suppliers,
        products=products, form={}, lines=[],
    )


@po_bp.route('/purchase-orders/<int:po_id>/edit', methods=['GET', 'POST'])
def po_edit(po_id: int):
    r = _require_folder()
    if r: return r
    db = get_db()
    po = db.execute("SELECT * FROM purchase_orders WHERE id=?", (po_id,)).fetchone()
    if not po or po['status'] not in EDITABLE_STATUSES:
        flash('Only Draft orders can be edited.', 'banner-error')
        return redirect(url_for('po.po_detail', po_id=po_id))

    suppliers = db.execute("SELECT id, name FROM suppliers ORDER BY name").fetchall()
    products  = db.execute(
        "SELECT p.id, p.sku, p.name, p.cost_per_kg_paise FROM products p WHERE p.active=1 ORDER BY p.name"
    ).fetchall()
    existing_lines = _po_lines(db, po_id)

    if request.method == 'POST':
        errors, lines = _parse_po_form(request.form, products)
        if not lines:
            errors.append('Add at least one product line.')
        if errors:
            for e in errors: flash(e, 'banner-error')
            return render_template('po/form.html',
                po=po, po_number=po['po_number'], suppliers=suppliers,
                products=products, form=request.form, lines=lines,
            )
        with db:
            db.execute(
                """UPDATE purchase_orders SET supplier_id=?, date=?, expected_date=?, notes=?
                   WHERE id=?""",
                (request.form.get('supplier_id') or None,
                 request.form.get('date', today_iso()),
                 request.form.get('expected_date') or None,
                 request.form.get('notes', '').strip(),
                 po_id),
            )
            db.execute("DELETE FROM po_lines WHERE po_id=?", (po_id,))
            for line in lines:
                db.execute(
                    "INSERT INTO po_lines (po_id, product_id, grams, cost_per_kg_paise) VALUES (?,?,?,?)",
                    (po_id, line['product_id'], line['grams'], line['cost_paise']),
                )
        mark_saved()
        flash(f'Order {po["po_number"]} updated.', 'banner-success')
        return redirect(url_for('po.po_detail', po_id=po_id))

    return render_template('po/form.html',
        po=po, po_number=po['po_number'], suppliers=suppliers,
        products=products, form={}, lines=existing_lines,
    )


def _parse_po_form(form, products):
    """Parse indexed line fields from a PO form. Returns (errors, lines)."""
    errors = []
    product_map = {str(p['id']): p for p in products}
    n = int(form.get('line_count', 0))
    lines = []
    for i in range(n):
        pid   = form.get(f'product_id_{i}', '').strip()
        qty   = form.get(f'qty_kg_{i}', '').strip()
        cost  = form.get(f'cost_per_kg_{i}', '').strip()
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
        lines.append({'product_id': int(pid), 'grams': grams, 'cost_paise': cost_paise})
    return errors, lines


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
    total_paise = sum(
        calc_line_total_paise(l['cost_per_kg_paise'], l['grams']) for l in lines
    )
    return render_template('po/detail.html',
        po=po, lines=lines, total_paise=total_paise,
        editable=po['status'] in EDITABLE_STATUSES,
        receivable=po['status'] in RECEIVABLE_STATUSES,
    )


# ── status changes ────────────────────────────────────────────────────────────

@po_bp.route('/purchase-orders/<int:po_id>/mark-ordered', methods=['POST'])
def po_mark_ordered(po_id: int):
    r = _require_folder()
    if r: return r
    db = get_db()
    po = db.execute("SELECT status, po_number FROM purchase_orders WHERE id=?", (po_id,)).fetchone()
    if not po or po['status'] != 'Draft':
        flash('Only Draft orders can be marked as Ordered.', 'banner-error')
        return redirect(url_for('po.po_detail', po_id=po_id))
    db.execute("UPDATE purchase_orders SET status='Ordered' WHERE id=?", (po_id,))
    db.commit()
    mark_saved()
    flash(f'{po["po_number"]} marked as Ordered.', 'banner-success')
    return redirect(url_for('po.po_detail', po_id=po_id))


@po_bp.route('/purchase-orders/<int:po_id>/cancel', methods=['POST'])
def po_cancel(po_id: int):
    r = _require_folder()
    if r: return r
    db = get_db()
    po = db.execute("SELECT status, po_number FROM purchase_orders WHERE id=?", (po_id,)).fetchone()
    if not po or po['status'] == 'Received':
        flash('Received orders cannot be cancelled.', 'banner-error')
        return redirect(url_for('po.po_detail', po_id=po_id))
    db.execute("UPDATE purchase_orders SET status='Cancelled' WHERE id=?", (po_id,))
    db.commit()
    mark_saved()
    flash(f'{po["po_number"]} cancelled.', 'banner-success')
    return redirect(url_for('po.po_list'))


# ── receive ───────────────────────────────────────────────────────────────────

@po_bp.route('/purchase-orders/<int:po_id>/receive', methods=['GET', 'POST'])
def po_receive(po_id: int):
    r = _require_folder()
    if r: return r
    db = get_db()
    po = db.execute("""
        SELECT po.*, s.name AS supplier_name
        FROM purchase_orders po
        LEFT JOIN suppliers s ON po.supplier_id = s.id
        WHERE po.id=?
    """, (po_id,)).fetchone()
    if not po or po['status'] not in RECEIVABLE_STATUSES:
        flash('This order cannot receive stock in its current status.', 'banner-error')
        return redirect(url_for('po.po_detail', po_id=po_id))

    lines = _po_lines(db, po_id)

    if request.method == 'POST':
        recv_date = request.form.get('recv_date', today_iso()).strip()
        recv_notes = request.form.get('recv_notes', '').strip()
        errors = []
        recv_data = []

        for line in lines:
            remaining = line['grams'] - line['received_grams']
            raw = request.form.get(f'recv_{line["id"]}', '').strip()
            if not raw:
                recv_data.append({'line': line, 'grams': 0, 'update_cost': False})
                continue
            try:
                g = kg_str_to_grams(raw)
            except ValueError as e:
                errors.append(f'{line["product_name"]}: {e}')
                g = 0
            if g < 0:
                errors.append(f'{line["product_name"]}: quantity cannot be negative.')
                g = 0
            update_cost = request.form.get(f'update_cost_{line["id"]}') == '1'
            recv_data.append({'line': line, 'grams': g, 'update_cost': update_cost})

        total_recv = sum(d['grams'] for d in recv_data)
        if total_recv == 0:
            errors.append('Enter at least one quantity to receive.')

        if errors:
            for e in errors: flash(e, 'banner-error')
            return render_template('po/receive.html', po=po, lines=lines, today=today_iso())

        with db:
            rcpt = db.execute(
                "INSERT INTO receipts (po_id, date, notes) VALUES (?,?,?)",
                (po_id, recv_date, recv_notes),
            )
            receipt_id = rcpt.lastrowid

            for d in recv_data:
                if d['grams'] == 0:
                    continue
                line = d['line']
                db.execute(
                    """INSERT INTO receipt_lines
                       (receipt_id, po_line_id, product_id, grams, cost_per_kg_paise)
                       VALUES (?,?,?,?,?)""",
                    (receipt_id, line['id'], line['product_id'],
                     d['grams'], line['cost_per_kg_paise']),
                )
                db.execute(
                    """INSERT INTO stock_movements
                       (product_id, type, grams, ref_type, ref_id, date, note)
                       VALUES (?,?,?,?,?,?,?)""",
                    (line['product_id'], 'purchase_receipt', d['grams'],
                     'receipt', receipt_id, recv_date, po['po_number']),
                )
                db.execute(
                    "UPDATE po_lines SET received_grams = received_grams + ? WHERE id=?",
                    (d['grams'], line['id']),
                )
                if d['update_cost']:
                    db.execute(
                        "UPDATE products SET cost_per_kg_paise=? WHERE id=?",
                        (line['cost_per_kg_paise'], line['product_id']),
                    )

            # Refresh lines to compute new status
            updated_lines = _po_lines(db, po_id)
            _update_po_status(db, po_id, updated_lines)

        mark_saved()
        flash(f'Stock received for {po["po_number"]}.', 'banner-success')
        return redirect(url_for('po.po_detail', po_id=po_id))

    return render_template('po/receive.html', po=po, lines=lines, today=today_iso())


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
    total_paise = sum(
        calc_line_total_paise(l['cost_per_kg_paise'], l['grams']) for l in lines
    )
    settings_rows = db.execute("SELECT key, value FROM settings").fetchall()
    settings = {r['key']: r['value'] for r in settings_rows}
    return render_template('po/print.html',
        po=po, lines=lines, total_paise=total_paise, settings=settings,
    )
