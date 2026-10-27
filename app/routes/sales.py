from __future__ import annotations

from flask import (
    Blueprint, render_template, request, redirect,
    url_for, abort, flash,
)
from app.db import get_db, mark_saved
from app.utils import (
    kg_str_to_grams, grams_to_kg_str,
    rupee_str_to_paise, calc_line_total_paise,
    today_iso,
)

sales_bp = Blueprint('sales', __name__)

VOID_EDITABLE = ()  # statuses that block edit — we check voided flag directly


def _next_invoice_number(db):
    prefix = db.execute("SELECT value FROM settings WHERE key='invoice_prefix'").fetchone()['value']
    nxt    = int(db.execute("SELECT value FROM settings WHERE key='invoice_next'").fetchone()['value'])
    return f"{prefix}{nxt:04d}", nxt


def _bump_invoice_number(db, nxt: int) -> None:
    db.execute("UPDATE settings SET value=? WHERE key='invoice_next'", (str(nxt + 1),))


def _sale_lines(db, sale_id: int):
    rows = db.execute("""
        SELECT sl.id, sl.grams, sl.price_per_kg_paise,
               p.id AS product_id, p.sku, p.name AS product_name
        FROM sale_lines sl
        JOIN products p ON sl.product_id = p.id
        WHERE sl.sale_id = ?
        ORDER BY sl.id
    """, (sale_id,)).fetchall()
    return [dict(r) for r in rows]


def _products_for_form(db):
    rows = db.execute(
        "SELECT id, sku, name, price_per_kg_paise FROM products WHERE active=1 ORDER BY name"
    ).fetchall()
    return [dict(r) for r in rows]


def _current_stock(db, product_id: int) -> int:
    return db.execute(
        "SELECT COALESCE(SUM(grams),0) FROM stock_movements WHERE product_id=?",
        (product_id,)
    ).fetchone()[0]


def _sale_total(db, sale_id: int) -> int:
    return db.execute("""
        SELECT COALESCE(SUM((price_per_kg_paise * grams + 500) / 1000), 0)
        FROM sale_lines WHERE sale_id=?
    """, (sale_id,)).fetchone()[0]


def _paid_paise(db, sale_id: int) -> int:
    return db.execute(
        "SELECT COALESCE(SUM(paise),0) FROM payments WHERE sale_id=? AND voided=0",
        (sale_id,)
    ).fetchone()[0]


def _sale_status(voided: bool, total_paise: int, paid_paise: int) -> str:
    if voided:
        return 'Void'
    if paid_paise >= total_paise:
        return 'Paid'
    if paid_paise > 0:
        return 'Part paid'
    return 'Pending'


def _parse_sale_form(form, products):
    errors = []
    product_map = {str(p['id']): p for p in products}
    n = int(form.get('line_count', 0))
    lines = []
    for i in range(n):
        pid   = form.get(f'product_id_{i}', '').strip()
        qty   = form.get(f'qty_kg_{i}', '').strip()
        price = form.get(f'price_per_kg_{i}', '').strip()
        if not pid and not qty:
            continue
        if pid not in product_map:
            errors.append(f'Line {i+1}: unknown product.')
            continue
        try:
            grams      = kg_str_to_grams(qty or '0')
            price_paise = rupee_str_to_paise(price or '0')
        except ValueError as e:
            errors.append(f'Line {i+1}: {e}')
            continue
        if grams <= 0:
            errors.append(f'Line {i+1}: quantity must be greater than zero.')
            continue
        lines.append({'product_id': int(pid), 'grams': grams, 'price_paise': price_paise})
    return errors, lines


# ── list ──────────────────────────────────────────────────────────────────────

@sales_bp.route('/sales')
def sale_list():
    db = get_db()
    f_status   = request.args.get('status', '')
    f_customer = request.args.get('customer', '')

    where, params = [], []
    if f_customer:
        where.append('s.customer_id = ?')
        params.append(f_customer)
    where_sql = ('WHERE ' + ' AND '.join(where)) if where else ''

    rows = db.execute(f"""
        SELECT s.id, s.invoice_number, s.date, s.voided, s.customer_id,
               c.name AS customer_name,
               COALESCE(SUM((sl.price_per_kg_paise * sl.grams + 500) / 1000), 0) AS total_paise,
               COALESCE(pt.paid, 0) AS paid_paise
        FROM sales s
        LEFT JOIN customers c ON s.customer_id = c.id
        LEFT JOIN sale_lines sl ON sl.sale_id = s.id
        LEFT JOIN (
            SELECT sale_id, SUM(paise) AS paid FROM payments WHERE voided=0 GROUP BY sale_id
        ) pt ON pt.sale_id = s.id
        {where_sql}
        GROUP BY s.id
        ORDER BY s.date DESC, s.id DESC
    """, params).fetchall()

    customers = db.execute("SELECT id, name FROM customers ORDER BY name").fetchall()
    statuses  = ['Paid', 'Part paid', 'Pending', 'Void']

    result = []
    for r in rows:
        status = _sale_status(bool(r['voided']), r['total_paise'], r['paid_paise'])
        if f_status and status != f_status:
            continue
        result.append(dict(r) | {'status': status})

    return render_template('sales/list.html',
        rows=result, customers=customers, statuses=statuses,
        f_status=f_status, f_customer=f_customer,
    )


# ── new ───────────────────────────────────────────────────────────────────────

@sales_bp.route('/sales/new', methods=['GET', 'POST'])
def sale_new():
    db = get_db()
    customers = db.execute("SELECT id, name FROM customers WHERE active=1 ORDER BY name").fetchall()
    products  = _products_for_form(db)
    inv_number, nxt = _next_invoice_number(db)
    allow_neg = db.execute(
        "SELECT value FROM settings WHERE key='allow_negative_stock'"
    ).fetchone()['value'] == '1'

    if request.method == 'POST':
        invoice_number = request.form.get('invoice_number', '').strip()
        errors, lines = _parse_sale_form(request.form, products)

        if not invoice_number:
            errors.append('Challan number is required.')
        elif db.execute("SELECT id FROM sales WHERE invoice_number=?", (invoice_number,)).fetchone():
            errors.append(f'Invoice number "{invoice_number}" already exists.')

        if not lines:
            errors.append('Add at least one product line.')

        # Stock check
        if not allow_neg:
            for line in lines:
                stock = _current_stock(db, line['product_id'])
                if stock - line['grams'] < 0:
                    p = next(p for p in products if p['id'] == line['product_id'])
                    errors.append(
                        f'{p["sku"]} – {p["name"]}: only {grams_to_kg_str(stock)} kg in stock, '
                        f'tried to sell {grams_to_kg_str(line["grams"])} kg.'
                    )

        if errors:
            for e in errors: flash(e, 'banner-error')
            return render_template('sales/form.html',
                sale=None, inv_number=inv_number, customers=customers,
                products=products, form=request.form, lines=lines,
            )

        # Parse optional payment now
        pay_raw = request.form.get('pay_amount', '').strip()
        pay_paise = 0
        pay_method = request.form.get('pay_method', 'cash')
        pay_note   = request.form.get('pay_note', '').strip()
        if pay_raw:
            try:
                pay_paise = rupee_str_to_paise(pay_raw)
            except ValueError:
                pay_paise = 0

        customer_id = request.form.get('customer_id') or None
        if customer_id:
            customer_id = int(customer_id)

        with db:
            cur = db.execute(
                "INSERT INTO sales (invoice_number, customer_id, date, notes) VALUES (?,?,?,?)",
                (invoice_number, customer_id,
                 request.form.get('date', today_iso()),
                 request.form.get('notes', '').strip()),
            )
            sale_id = cur.lastrowid

            for line in lines:
                db.execute(
                    "INSERT INTO sale_lines (sale_id, product_id, grams, price_per_kg_paise) VALUES (?,?,?,?)",
                    (sale_id, line['product_id'], line['grams'], line['price_paise']),
                )
                db.execute(
                    """INSERT INTO stock_movements
                       (product_id, type, grams, ref_type, ref_id, date, note)
                       VALUES (?,?,?,?,?,?,?)""",
                    (line['product_id'], 'sale', -line['grams'],
                     'sale', sale_id,
                     request.form.get('date', today_iso()),
                     invoice_number),
                )

            if pay_paise > 0 and customer_id:
                db.execute(
                    "INSERT INTO payments (customer_id, sale_id, paise, date, method, note) VALUES (?,?,?,?,?,?)",
                    (customer_id, sale_id, pay_paise,
                     request.form.get('date', today_iso()),
                     pay_method, pay_note),
                )

            _bump_invoice_number(db, nxt)

        mark_saved()
        flash(f'Challan {invoice_number} recorded.', 'banner-success')
        return redirect(url_for('sales.sale_detail', sale_id=sale_id))

    return render_template('sales/form.html',
        sale=None, inv_number=inv_number, customers=customers,
        products=products, form={}, lines=[],
    )


# ── edit ──────────────────────────────────────────────────────────────────────

@sales_bp.route('/sales/<int:sale_id>/edit', methods=['GET', 'POST'])
def sale_edit(sale_id: int):
    db = get_db()
    sale = db.execute("SELECT * FROM sales WHERE id=?", (sale_id,)).fetchone()
    if not sale:
        abort(404)
    if sale['voided']:
        flash('Voided challans cannot be edited.', 'banner-error')
        return redirect(url_for('sales.sale_detail', sale_id=sale_id))

    has_payments = db.execute(
        "SELECT 1 FROM payments WHERE sale_id=? AND voided=0 LIMIT 1", (sale_id,)
    ).fetchone()
    if has_payments:
        flash('Challans with payments cannot be edited. Void the challan if needed.', 'banner-error')
        return redirect(url_for('sales.sale_detail', sale_id=sale_id))

    customers = db.execute("SELECT id, name FROM customers WHERE active=1 ORDER BY name").fetchall()
    products  = _products_for_form(db)
    existing_lines = _sale_lines(db, sale_id)
    allow_neg = db.execute(
        "SELECT value FROM settings WHERE key='allow_negative_stock'"
    ).fetchone()['value'] == '1'

    if request.method == 'POST':
        invoice_number = request.form.get('invoice_number', '').strip()
        errors, lines = _parse_sale_form(request.form, products)

        if not invoice_number:
            errors.append('Challan number is required.')
        elif db.execute(
            "SELECT id FROM sales WHERE invoice_number=? AND id!=?", (invoice_number, sale_id)
        ).fetchone():
            errors.append(f'Invoice number "{invoice_number}" already exists.')

        if not lines:
            errors.append('Add at least one product line.')

        if not allow_neg:
            for line in lines:
                # Stock including what was previously sold on this invoice
                old_grams = sum(
                    l['grams'] for l in existing_lines
                    if l['product_id'] == line['product_id']
                )
                stock = _current_stock(db, line['product_id']) + old_grams
                if stock - line['grams'] < 0:
                    p = next(p for p in products if p['id'] == line['product_id'])
                    errors.append(
                        f'{p["sku"]} – {p["name"]}: only {grams_to_kg_str(stock)} kg available.'
                    )

        if errors:
            for e in errors: flash(e, 'banner-error')
            return render_template('sales/form.html',
                sale=sale, inv_number=sale['invoice_number'], customers=customers,
                products=products, form=request.form, lines=lines,
            )

        customer_id = request.form.get('customer_id') or None
        if customer_id:
            customer_id = int(customer_id)
        sale_date = request.form.get('date', today_iso())

        with db:
            # Reverse old stock movements
            for old in existing_lines:
                db.execute(
                    """INSERT INTO stock_movements
                       (product_id, type, grams, ref_type, ref_id, date, note)
                       VALUES (?,?,?,?,?,?,?)""",
                    (old['product_id'], 'sale_void', old['grams'],
                     'sale', sale_id, sale_date, invoice_number),
                )

            db.execute(
                "UPDATE sales SET invoice_number=?, customer_id=?, date=?, notes=? WHERE id=?",
                (invoice_number, customer_id, sale_date,
                 request.form.get('notes', '').strip(), sale_id),
            )
            db.execute("DELETE FROM sale_lines WHERE sale_id=?", (sale_id,))
            db.execute(
                "DELETE FROM stock_movements WHERE ref_type='sale' AND ref_id=? AND type='sale'",
                (sale_id,),
            )

            for line in lines:
                db.execute(
                    "INSERT INTO sale_lines (sale_id, product_id, grams, price_per_kg_paise) VALUES (?,?,?,?)",
                    (sale_id, line['product_id'], line['grams'], line['price_paise']),
                )
                db.execute(
                    """INSERT INTO stock_movements
                       (product_id, type, grams, ref_type, ref_id, date, note)
                       VALUES (?,?,?,?,?,?,?)""",
                    (line['product_id'], 'sale', -line['grams'],
                     'sale', sale_id, sale_date, invoice_number),
                )

        mark_saved()
        flash(f'Challan {invoice_number} updated.', 'banner-success')
        return redirect(url_for('sales.sale_detail', sale_id=sale_id))

    return render_template('sales/form.html',
        sale=sale, inv_number=sale['invoice_number'], customers=customers,
        products=products, form={}, lines=existing_lines,
    )


# ── detail ────────────────────────────────────────────────────────────────────

@sales_bp.route('/sales/<int:sale_id>')
def sale_detail(sale_id: int):
    db = get_db()
    sale = db.execute("""
        SELECT s.*, c.name AS customer_name, c.id AS cid
        FROM sales s LEFT JOIN customers c ON s.customer_id = c.id
        WHERE s.id=?
    """, (sale_id,)).fetchone()
    if not sale:
        abort(404)

    lines = _sale_lines(db, sale_id)
    total_paise = sum(calc_line_total_paise(l['price_per_kg_paise'], l['grams']) for l in lines)
    paid = _paid_paise(db, sale_id)
    status = _sale_status(bool(sale['voided']), total_paise, paid)
    payments = db.execute(
        "SELECT * FROM payments WHERE sale_id=? ORDER BY date, id", (sale_id,)
    ).fetchall()

    return render_template('sales/detail.html',
        sale=sale, lines=lines,
        total_paise=total_paise, paid_paise=paid,
        status=status, payments=payments,
        can_edit=not sale['voided'] and not payments,
    )


# ── void ──────────────────────────────────────────────────────────────────────

@sales_bp.route('/sales/<int:sale_id>/void', methods=['POST'])
def sale_void(sale_id: int):
    db = get_db()
    sale = db.execute("SELECT * FROM sales WHERE id=?", (sale_id,)).fetchone()
    if not sale or sale['voided']:
        flash('Challan is already voided.', 'banner-error')
        return redirect(url_for('sales.sale_detail', sale_id=sale_id))

    lines = _sale_lines(db, sale_id)
    with db:
        for line in lines:
            db.execute(
                """INSERT INTO stock_movements
                   (product_id, type, grams, ref_type, ref_id, date, note)
                   VALUES (?,?,?,?,?,?,?)""",
                (line['product_id'], 'sale_void', line['grams'],
                 'sale', sale_id, sale['date'], sale['invoice_number']),
            )
        db.execute("UPDATE sales SET voided=1 WHERE id=?", (sale_id,))
    mark_saved()
    flash(f'Challan {sale["invoice_number"]} voided. Stock reversed.', 'banner-success')
    return redirect(url_for('sales.sale_detail', sale_id=sale_id))


# ── delete ────────────────────────────────────────────────────────────────────

@sales_bp.route('/sales/<int:sale_id>/delete', methods=['POST'])
def sale_delete(sale_id: int):
    db = get_db()
    sale = db.execute("SELECT invoice_number FROM sales WHERE id=?", (sale_id,)).fetchone()
    if not sale:
        abort(404)
    with db:
        db.execute("DELETE FROM stock_movements WHERE ref_type='sale' AND ref_id=?", (sale_id,))
        db.execute("DELETE FROM sale_lines WHERE sale_id=?", (sale_id,))
        db.execute("DELETE FROM payments WHERE sale_id=?", (sale_id,))
        db.execute("DELETE FROM sales WHERE id=?", (sale_id,))
    mark_saved()
    flash(f'Challan {sale["invoice_number"]} deleted. Stock reversed.', 'banner-success')
    return redirect(url_for('sales.sale_list'))


# ── print ─────────────────────────────────────────────────────────────────────

@sales_bp.route('/sales/<int:sale_id>/print')
def sale_print(sale_id: int):
    db = get_db()
    sale = db.execute("""
        SELECT s.*, c.name AS customer_name, c.phone AS customer_phone,
               c.address AS customer_address
        FROM sales s LEFT JOIN customers c ON s.customer_id = c.id
        WHERE s.id=?
    """, (sale_id,)).fetchone()
    if not sale:
        abort(404)

    lines = _sale_lines(db, sale_id)
    total_paise = sum(calc_line_total_paise(l['price_per_kg_paise'], l['grams']) for l in lines)
    paid = _paid_paise(db, sale_id)
    status = _sale_status(bool(sale['voided']), total_paise, paid)
    settings_rows = db.execute("SELECT key, value FROM settings").fetchall()
    settings = {r['key']: r['value'] for r in settings_rows}
    return render_template('sales/print.html',
        sale=sale, lines=lines, total_paise=total_paise,
        paid_paise=paid, status=status, settings=settings,
    )
