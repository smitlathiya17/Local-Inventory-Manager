from __future__ import annotations

from flask import Blueprint, render_template, request, redirect, url_for, abort, flash
from app.db import get_data_folder, get_db, mark_saved
from app.utils import today_iso, calc_line_total_paise, rupee_str_to_paise

suppliers_bp = Blueprint('suppliers', __name__)


def _require_folder():
    if get_data_folder() is None:
        return redirect(url_for('setup.index'))


def _outstanding(db, supplier_id: int) -> int:
    purchased = db.execute("""
        SELECT COALESCE(SUM((pl.cost_per_kg_paise * pl.grams + 500) / 1000), 0)
        FROM purchase_orders po
        JOIN po_lines pl ON pl.po_id = po.id
        WHERE po.supplier_id = ?
    """, (supplier_id,)).fetchone()[0]
    paid = db.execute(
        "SELECT COALESCE(SUM(paise), 0) FROM supplier_payments WHERE supplier_id=?",
        (supplier_id,),
    ).fetchone()[0]
    return purchased - paid


# ── list ──────────────────────────────────────────────────────────────────────

@suppliers_bp.route('/suppliers')
def supplier_list():
    r = _require_folder()
    if r: return r
    db = get_db()
    rows = db.execute("""
        SELECT s.id, s.name, s.contact, s.phone, s.address,
               COALESCE(pt.purchased, 0) - COALESCE(pd.paid, 0) AS outstanding,
               COALESCE(pt.po_count, 0) AS po_count
        FROM suppliers s
        LEFT JOIN (
            SELECT po.supplier_id,
                   SUM((pl.cost_per_kg_paise * pl.grams + 500) / 1000) AS purchased,
                   COUNT(DISTINCT po.id) AS po_count
            FROM purchase_orders po JOIN po_lines pl ON pl.po_id = po.id
            GROUP BY po.supplier_id
        ) pt ON pt.supplier_id = s.id
        LEFT JOIN (
            SELECT supplier_id, SUM(paise) AS paid
            FROM supplier_payments GROUP BY supplier_id
        ) pd ON pd.supplier_id = s.id
        ORDER BY s.name
    """).fetchall()
    return render_template('suppliers/list.html', rows=rows)


# ── new / edit ────────────────────────────────────────────────────────────────

@suppliers_bp.route('/suppliers/new', methods=['GET', 'POST'])
def supplier_new():
    r = _require_folder()
    if r: return r
    db = get_db()
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        if not name:
            flash('Supplier name is required.', 'banner-error')
            return render_template('suppliers/form.html', supplier=None, form=request.form)
        db.execute(
            "INSERT INTO suppliers (name, contact, phone, address, notes) VALUES (?,?,?,?,?)",
            (name,
             request.form.get('contact', '').strip(),
             request.form.get('phone', '').strip(),
             request.form.get('address', '').strip(),
             request.form.get('notes', '').strip()),
        )
        db.commit()
        mark_saved()
        flash(f'Supplier "{name}" added.', 'banner-success')
        return redirect(url_for('suppliers.supplier_list'))
    return render_template('suppliers/form.html', supplier=None, form={})


@suppliers_bp.route('/suppliers/<int:supplier_id>/edit', methods=['GET', 'POST'])
def supplier_edit(supplier_id: int):
    r = _require_folder()
    if r: return r
    db = get_db()
    supplier = db.execute("SELECT * FROM suppliers WHERE id=?", (supplier_id,)).fetchone()
    if not supplier:
        abort(404)
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        if not name:
            flash('Supplier name is required.', 'banner-error')
            return render_template('suppliers/form.html', supplier=supplier, form=request.form)
        db.execute(
            "UPDATE suppliers SET name=?, contact=?, phone=?, address=?, notes=? WHERE id=?",
            (name,
             request.form.get('contact', '').strip(),
             request.form.get('phone', '').strip(),
             request.form.get('address', '').strip(),
             request.form.get('notes', '').strip(),
             supplier_id),
        )
        db.commit()
        mark_saved()
        flash(f'Supplier "{name}" saved.', 'banner-success')
        return redirect(url_for('suppliers.supplier_list'))
    return render_template('suppliers/form.html', supplier=supplier, form={})


# ── detail ────────────────────────────────────────────────────────────────────

@suppliers_bp.route('/suppliers/<int:supplier_id>')
def supplier_detail(supplier_id: int):
    r = _require_folder()
    if r: return r
    db = get_db()
    supplier = db.execute("SELECT * FROM suppliers WHERE id=?", (supplier_id,)).fetchone()
    if not supplier:
        abort(404)

    outstanding = _outstanding(db, supplier_id)

    # Purchase history
    purchases = db.execute("""
        SELECT po.id, po.po_number, po.date,
               COALESCE(SUM((pl.cost_per_kg_paise * pl.grams + 500) / 1000), 0) AS total_paise
        FROM purchase_orders po
        LEFT JOIN po_lines pl ON pl.po_id = po.id
        WHERE po.supplier_id = ?
        GROUP BY po.id
        ORDER BY po.date ASC, po.id ASC
    """, (supplier_id,)).fetchall()

    # Payment history
    payments = db.execute(
        "SELECT * FROM supplier_payments WHERE supplier_id=? ORDER BY date ASC, id ASC",
        (supplier_id,),
    ).fetchall()

    # Build running ledger (purchases = debit, payments = credit)
    events = []
    for p in purchases:
        events.append({'date': p['date'], 'type': 'purchase',
                       'paise': p['total_paise'], 'ref': p['po_number'], 'link_id': p['id']})
    for pay in payments:
        events.append({'date': pay['date'], 'type': 'payment',
                       'paise': pay['paise'], 'method': pay['method'],
                       'note': pay['note'], 'pay_id': pay['id']})
    events.sort(key=lambda e: (e['date'], e.get('link_id', 0)))

    running = 0
    entries = []
    for ev in events:
        if ev['type'] == 'purchase':
            running += ev['paise']
            entries.append({**ev, 'debit': ev['paise'], 'credit': 0, 'running': running})
        else:
            running -= ev['paise']
            entries.append({**ev, 'debit': 0, 'credit': ev['paise'], 'running': running})
    entries.reverse()

    return render_template('suppliers/detail.html',
        supplier=supplier,
        outstanding=outstanding,
        entries=entries,
        today=today_iso(),
    )


# ── payments ──────────────────────────────────────────────────────────────────

@suppliers_bp.route('/suppliers/<int:supplier_id>/payment', methods=['POST'])
def supplier_payment(supplier_id: int):
    r = _require_folder()
    if r: return r
    db = get_db()
    if not db.execute("SELECT id FROM suppliers WHERE id=?", (supplier_id,)).fetchone():
        abort(404)

    amount_raw = request.form.get('amount', '').strip()
    try:
        paise = rupee_str_to_paise(amount_raw)
    except ValueError:
        flash('Invalid amount.', 'banner-error')
        return redirect(url_for('suppliers.supplier_detail', supplier_id=supplier_id))
    if paise <= 0:
        flash('Amount must be greater than zero.', 'banner-error')
        return redirect(url_for('suppliers.supplier_detail', supplier_id=supplier_id))

    with db:
        db.execute(
            """INSERT INTO supplier_payments
               (supplier_id, purchase_order_id, paise, date, method, note)
               VALUES (?,?,?,?,?,?)""",
            (supplier_id,
             request.form.get('purchase_order_id') or None,
             paise,
             request.form.get('date', today_iso()),
             request.form.get('method', 'cash'),
             request.form.get('note', '').strip()),
        )
    mark_saved()
    flash('Payment recorded.', 'banner-success')
    return redirect(url_for('suppliers.supplier_detail', supplier_id=supplier_id))


@suppliers_bp.route('/suppliers/<int:supplier_id>/payment/<int:payment_id>/delete', methods=['POST'])
def supplier_payment_delete(supplier_id: int, payment_id: int):
    r = _require_folder()
    if r: return r
    db = get_db()
    with db:
        db.execute("DELETE FROM supplier_payments WHERE id=? AND supplier_id=?",
                   (payment_id, supplier_id))
    mark_saved()
    flash('Payment deleted.', 'banner-success')
    return redirect(url_for('suppliers.supplier_detail', supplier_id=supplier_id))
