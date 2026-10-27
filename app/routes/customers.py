from __future__ import annotations

from flask import (
    Blueprint, render_template, request, redirect,
    url_for, abort, flash,
)
from app.db import get_db, mark_saved
from app.utils import today_iso, calc_line_total_paise

customers_bp = Blueprint('customers', __name__)


def _outstanding(db, customer_id: int) -> int:
    """Return outstanding balance in paise for a customer."""
    invoiced = db.execute("""
        SELECT COALESCE(SUM((sl.price_per_kg_paise * sl.grams + 500) / 1000), 0)
        FROM sales s
        JOIN sale_lines sl ON sl.sale_id = s.id
        WHERE s.customer_id = ? AND s.voided = 0
    """, (customer_id,)).fetchone()[0]
    paid = db.execute("""
        SELECT COALESCE(SUM(paise), 0) FROM payments
        WHERE customer_id = ? AND voided = 0
    """, (customer_id,)).fetchone()[0]
    return invoiced - paid


# ── list ──────────────────────────────────────────────────────────────────────

@customers_bp.route('/customers')
def customer_list():
    db = get_db()
    rows = db.execute("""
        SELECT c.id, c.name, c.phone, c.active,
               COALESCE(st.invoiced, 0) - COALESCE(pt.paid, 0) AS outstanding,
               COALESCE(st.sale_count, 0) AS sale_count
        FROM customers c
        LEFT JOIN (
            SELECT s.customer_id,
                   SUM((sl.price_per_kg_paise * sl.grams + 500) / 1000) AS invoiced,
                   COUNT(DISTINCT s.id) AS sale_count
            FROM sales s JOIN sale_lines sl ON sl.sale_id = s.id
            WHERE s.voided = 0
            GROUP BY s.customer_id
        ) st ON st.customer_id = c.id
        LEFT JOIN (
            SELECT customer_id, SUM(paise) AS paid
            FROM payments WHERE voided = 0
            GROUP BY customer_id
        ) pt ON pt.customer_id = c.id
        WHERE c.active = 1
        ORDER BY c.name
    """).fetchall()
    return render_template('customers/list.html', rows=rows)


# ── new / edit ────────────────────────────────────────────────────────────────

@customers_bp.route('/customers/new', methods=['GET', 'POST'])
def customer_new():
    db = get_db()
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        if not name:
            flash('Name is required.', 'banner-error')
            return render_template('customers/form.html', customer=None, form=request.form)
        if db.execute("SELECT id FROM customers WHERE name=?", (name,)).fetchone():
            flash(f'A customer named "{name}" already exists.', 'banner-error')
            return render_template('customers/form.html', customer=None, form=request.form)
        with db:
            cur = db.execute(
                "INSERT INTO customers (name, phone, address, notes) VALUES (?,?,?,?)",
                (name,
                 request.form.get('phone', '').strip(),
                 request.form.get('address', '').strip(),
                 request.form.get('notes', '').strip()),
            )
            cid = cur.lastrowid
        mark_saved()
        flash(f'Customer {name} added.', 'banner-success')
        return redirect(url_for('customers.customer_detail', customer_id=cid))
    return render_template('customers/form.html', customer=None, form={})


@customers_bp.route('/customers/<int:customer_id>/edit', methods=['GET', 'POST'])
def customer_edit(customer_id: int):
    db = get_db()
    customer = db.execute("SELECT * FROM customers WHERE id=?", (customer_id,)).fetchone()
    if not customer:
        abort(404)
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        if not name:
            flash('Name is required.', 'banner-error')
            return render_template('customers/form.html', customer=customer, form=request.form)
        clash = db.execute(
            "SELECT id FROM customers WHERE name=? AND id!=?", (name, customer_id)
        ).fetchone()
        if clash:
            flash(f'A customer named "{name}" already exists.', 'banner-error')
            return render_template('customers/form.html', customer=customer, form=request.form)
        with db:
            db.execute(
                "UPDATE customers SET name=?, phone=?, address=?, notes=? WHERE id=?",
                (name,
                 request.form.get('phone', '').strip(),
                 request.form.get('address', '').strip(),
                 request.form.get('notes', '').strip(),
                 customer_id),
            )
        mark_saved()
        flash('Customer updated.', 'banner-success')
        return redirect(url_for('customers.customer_detail', customer_id=customer_id))
    return render_template('customers/form.html', customer=customer, form={})


# ── detail / ledger ───────────────────────────────────────────────────────────

@customers_bp.route('/customers/<int:customer_id>')
def customer_detail(customer_id: int):
    db = get_db()
    customer = db.execute("SELECT * FROM customers WHERE id=?", (customer_id,)).fetchone()
    if not customer:
        abort(404)

    sales_rows = db.execute("""
        SELECT s.id, s.invoice_number, s.date, s.voided,
               COALESCE(SUM((sl.price_per_kg_paise * sl.grams + 500) / 1000), 0) AS total_paise
        FROM sales s
        LEFT JOIN sale_lines sl ON sl.sale_id = s.id
        WHERE s.customer_id = ?
        GROUP BY s.id
        ORDER BY s.date, s.id
    """, (customer_id,)).fetchall()

    payment_rows = db.execute("""
        SELECT id, date, paise, method, note, sale_id, voided
        FROM payments
        WHERE customer_id = ?
        ORDER BY date, id
    """, (customer_id,)).fetchall()

    # Build merged ledger
    entries = []
    for s in sales_rows:
        entries.append({
            'date': s['date'], 'sort_key': (s['date'], 'inv', s['id']),
            'type': 'invoice', 'ref': s['invoice_number'],
            'debit': s['total_paise'], 'credit': 0,
            'voided': bool(s['voided']), 'link_id': s['id'],
        })
    for p in payment_rows:
        entries.append({
            'date': p['date'], 'sort_key': (p['date'], 'pmt', p['id']),
            'type': 'payment', 'ref': p['method'].capitalize(),
            'note': p['note'], 'debit': 0, 'credit': p['paise'],
            'voided': bool(p['voided']), 'link_id': p['id'],
            'sale_id': p['sale_id'],
        })

    entries.sort(key=lambda e: e['sort_key'])

    balance = 0
    for e in entries:
        if not e['voided']:
            balance += e['debit'] - e['credit']
        e['running'] = balance

    outstanding = balance
    return render_template('customers/detail.html',
        customer=customer, entries=entries, outstanding=outstanding,
        today=today_iso(),
    )


# ── record payment ────────────────────────────────────────────────────────────

@customers_bp.route('/customers/<int:customer_id>/payment', methods=['POST'])
def customer_payment(customer_id: int):
    db = get_db()
    customer = db.execute("SELECT * FROM customers WHERE id=?", (customer_id,)).fetchone()
    if not customer:
        abort(404)

    from app.utils import rupee_str_to_paise
    errors = []
    raw_amount = request.form.get('amount', '').strip()
    date = request.form.get('date', today_iso()).strip()
    method = request.form.get('method', 'cash').strip()
    note = request.form.get('note', '').strip()
    sale_id_raw = request.form.get('sale_id', '').strip()

    try:
        paise = rupee_str_to_paise(raw_amount)
    except ValueError:
        errors.append('Invalid amount.')
        paise = 0

    if paise <= 0:
        errors.append('Amount must be greater than zero.')

    sale_id = int(sale_id_raw) if sale_id_raw.isdigit() else None

    if errors:
        for e in errors:
            flash(e, 'banner-error')
        return redirect(url_for('customers.customer_detail', customer_id=customer_id))

    with db:
        db.execute(
            "INSERT INTO payments (customer_id, sale_id, paise, date, method, note) VALUES (?,?,?,?,?,?)",
            (customer_id, sale_id, paise, date, method, note),
        )
    mark_saved()
    flash('Payment recorded.', 'banner-success')
    return redirect(url_for('customers.customer_detail', customer_id=customer_id))


# ── printable statement ───────────────────────────────────────────────────────

@customers_bp.route('/customers/<int:customer_id>/statement')
def customer_statement(customer_id: int):
    db = get_db()
    customer = db.execute("SELECT * FROM customers WHERE id=?", (customer_id,)).fetchone()
    if not customer:
        abort(404)

    sales_rows = db.execute("""
        SELECT s.id, s.invoice_number, s.date, s.voided,
               COALESCE(SUM((sl.price_per_kg_paise * sl.grams + 500) / 1000), 0) AS total_paise
        FROM sales s
        LEFT JOIN sale_lines sl ON sl.sale_id = s.id
        WHERE s.customer_id = ?
        GROUP BY s.id
        ORDER BY s.date, s.id
    """, (customer_id,)).fetchall()

    payment_rows = db.execute("""
        SELECT id, date, paise, method, note, sale_id, voided
        FROM payments WHERE customer_id = ? ORDER BY date, id
    """, (customer_id,)).fetchall()

    entries = []
    for s in sales_rows:
        entries.append({
            'date': s['date'], 'sort_key': (s['date'], 0, s['id']),
            'type': 'invoice', 'ref': s['invoice_number'],
            'debit': s['total_paise'], 'credit': 0, 'voided': bool(s['voided']),
        })
    for p in payment_rows:
        entries.append({
            'date': p['date'], 'sort_key': (p['date'], 1, p['id']),
            'type': 'payment', 'ref': p['method'].capitalize(),
            'note': p['note'], 'debit': 0, 'credit': p['paise'],
            'voided': bool(p['voided']),
        })
    entries.sort(key=lambda e: e['sort_key'])

    balance = 0
    for e in entries:
        if not e['voided']:
            balance += e['debit'] - e['credit']
        e['running'] = balance

    settings_rows = db.execute("SELECT key, value FROM settings").fetchall()
    settings = {r['key']: r['value'] for r in settings_rows}
    return render_template('customers/statement.html',
        customer=customer, entries=entries, outstanding=balance, settings=settings,
        today=today_iso(),
    )
