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

quotations_bp = Blueprint('quotations', __name__)

EDITABLE_STATUSES   = ('Draft',)
SENDABLE_STATUSES   = ('Draft',)
ACCEPTABLE_STATUSES = ('Sent', 'Draft')
ADVANCEABLE_STATUSES = ('Accepted',)
CONVERTIBLE_STATUSES = ('Accepted',)


def _next_quote_number(db):
    prefix = db.execute("SELECT value FROM settings WHERE key='quotation_prefix'").fetchone()['value']
    nxt    = int(db.execute("SELECT value FROM settings WHERE key='quotation_next'").fetchone()['value'])
    return f"{prefix}{nxt:04d}", nxt


def _bump_quote_number(db, nxt: int) -> None:
    db.execute("UPDATE settings SET value=? WHERE key='quotation_next'", (str(nxt + 1),))


def _quot_lines(db, quotation_id: int):
    rows = db.execute("""
        SELECT ql.id, ql.grams, ql.price_per_kg_paise,
               p.id AS product_id, p.sku, p.name AS product_name
        FROM quotation_lines ql
        JOIN products p ON ql.product_id = p.id
        WHERE ql.quotation_id = ?
        ORDER BY ql.id
    """, (quotation_id,)).fetchall()
    return [dict(r) for r in rows]


def _products_for_form(db):
    rows = db.execute(
        "SELECT id, sku, name, price_per_kg_paise FROM products WHERE active=1 ORDER BY name"
    ).fetchall()
    return [dict(r) for r in rows]


def _parse_form(form, products):
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
            grams       = kg_str_to_grams(qty or '0')
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

@quotations_bp.route('/quotations')
def quotation_list():
    db = get_db()
    f_status   = request.args.get('status', '')
    f_customer = request.args.get('customer', '')

    where, params = [], []
    if f_status:
        where.append('q.status = ?');         params.append(f_status)
    if f_customer:
        where.append('q.customer_id = ?');    params.append(f_customer)
    where_sql = ('WHERE ' + ' AND '.join(where)) if where else ''

    rows = db.execute(f"""
        SELECT q.id, q.quote_number, q.date, q.expiry_date, q.status, q.customer_id,
               c.name AS customer_name,
               COALESCE(SUM((ql.price_per_kg_paise * ql.grams + 500) / 1000), 0) AS total_paise,
               COALESCE(SUM(ql.grams), 0) AS total_grams
        FROM quotations q
        LEFT JOIN customers c ON q.customer_id = c.id
        LEFT JOIN quotation_lines ql ON ql.quotation_id = q.id
        {where_sql}
        GROUP BY q.id
        ORDER BY q.date DESC, q.id DESC
    """, params).fetchall()

    customers = db.execute("SELECT id, name FROM customers ORDER BY name").fetchall()
    statuses  = ['Draft', 'Sent', 'Accepted', 'Rejected', 'Expired', 'Converted']
    return render_template('quotations/list.html',
        rows=rows, customers=customers, statuses=statuses,
        f_status=f_status, f_customer=f_customer,
    )


# ── new / edit ────────────────────────────────────────────────────────────────

@quotations_bp.route('/quotations/new', methods=['GET', 'POST'])
def quotation_new():
    db = get_db()
    customers = db.execute("SELECT id, name FROM customers WHERE active=1 ORDER BY name").fetchall()
    products  = _products_for_form(db)
    q_number, nxt = _next_quote_number(db)

    if request.method == 'POST':
        quote_number = request.form.get('quote_number', '').strip()
        errors, lines = _parse_form(request.form, products)
        if not quote_number:
            errors.append('Quote number is required.')
        elif db.execute("SELECT id FROM quotations WHERE quote_number=?", (quote_number,)).fetchone():
            errors.append(f'Quote number "{quote_number}" already exists.')
        if not lines:
            errors.append('Add at least one product line.')
        if errors:
            for e in errors: flash(e, 'banner-error')
            return render_template('quotations/form.html',
                quot=None, q_number=q_number, customers=customers,
                products=products, form=request.form, lines=lines,
            )
        with db:
            cur = db.execute(
                """INSERT INTO quotations
                   (quote_number, customer_id, date, expiry_date, notes)
                   VALUES (?,?,?,?,?)""",
                (quote_number,
                 request.form.get('customer_id') or None,
                 request.form.get('date', today_iso()),
                 request.form.get('expiry_date') or None,
                 request.form.get('notes', '').strip()),
            )
            qid = cur.lastrowid
            for line in lines:
                db.execute(
                    "INSERT INTO quotation_lines (quotation_id, product_id, grams, price_per_kg_paise) VALUES (?,?,?,?)",
                    (qid, line['product_id'], line['grams'], line['price_paise']),
                )
            _bump_quote_number(db, nxt)
        mark_saved()
        flash(f'Quotation {quote_number} created.', 'banner-success')
        return redirect(url_for('quotations.quotation_detail', quot_id=qid))

    return render_template('quotations/form.html',
        quot=None, q_number=q_number, customers=customers,
        products=products, form={}, lines=[],
    )


@quotations_bp.route('/quotations/<int:quot_id>/edit', methods=['GET', 'POST'])
def quotation_edit(quot_id: int):
    db = get_db()
    quot = db.execute("SELECT * FROM quotations WHERE id=?", (quot_id,)).fetchone()
    if not quot or quot['status'] not in EDITABLE_STATUSES:
        flash('Only Draft quotations can be edited.', 'banner-error')
        return redirect(url_for('quotations.quotation_detail', quot_id=quot_id))

    customers = db.execute("SELECT id, name FROM customers WHERE active=1 ORDER BY name").fetchall()
    products  = _products_for_form(db)
    existing  = _quot_lines(db, quot_id)

    if request.method == 'POST':
        quote_number = request.form.get('quote_number', '').strip()
        errors, lines = _parse_form(request.form, products)
        if not quote_number:
            errors.append('Quote number is required.')
        elif db.execute(
            "SELECT id FROM quotations WHERE quote_number=? AND id!=?", (quote_number, quot_id)
        ).fetchone():
            errors.append(f'Quote number "{quote_number}" already exists.')
        if not lines:
            errors.append('Add at least one product line.')
        if errors:
            for e in errors: flash(e, 'banner-error')
            return render_template('quotations/form.html',
                quot=quot, q_number=quot['quote_number'], customers=customers,
                products=products, form=request.form, lines=lines,
            )
        with db:
            db.execute(
                """UPDATE quotations SET quote_number=?, customer_id=?, date=?, expiry_date=?, notes=?
                   WHERE id=?""",
                (quote_number,
                 request.form.get('customer_id') or None,
                 request.form.get('date', today_iso()),
                 request.form.get('expiry_date') or None,
                 request.form.get('notes', '').strip(),
                 quot_id),
            )
            db.execute("DELETE FROM quotation_lines WHERE quotation_id=?", (quot_id,))
            for line in lines:
                db.execute(
                    "INSERT INTO quotation_lines (quotation_id, product_id, grams, price_per_kg_paise) VALUES (?,?,?,?)",
                    (quot_id, line['product_id'], line['grams'], line['price_paise']),
                )
        mark_saved()
        flash(f'Quotation {quote_number} updated.', 'banner-success')
        return redirect(url_for('quotations.quotation_detail', quot_id=quot_id))

    return render_template('quotations/form.html',
        quot=quot, q_number=quot['quote_number'], customers=customers,
        products=products, form={}, lines=existing,
    )


# ── detail ────────────────────────────────────────────────────────────────────

@quotations_bp.route('/quotations/<int:quot_id>')
def quotation_detail(quot_id: int):
    db = get_db()
    quot = db.execute("""
        SELECT q.*, c.name AS customer_name
        FROM quotations q LEFT JOIN customers c ON q.customer_id = c.id
        WHERE q.id=?
    """, (quot_id,)).fetchone()
    if not quot:
        abort(404)

    lines = _quot_lines(db, quot_id)
    total_paise = sum(calc_line_total_paise(l['price_per_kg_paise'], l['grams']) for l in lines)
    advances = db.execute(
        "SELECT * FROM payments WHERE quotation_id=? ORDER BY date, id", (quot_id,)
    ).fetchall()
    advance_total = sum(p['paise'] for p in advances if not p['voided'])

    return render_template('quotations/detail.html',
        quot=quot, lines=lines, total_paise=total_paise,
        advances=advances, advance_total=advance_total,
        today=today_iso(),
    )


# ── status transitions ────────────────────────────────────────────────────────

@quotations_bp.route('/quotations/<int:quot_id>/send', methods=['POST'])
def quotation_send(quot_id: int):
    db = get_db()
    quot = db.execute("SELECT status, quote_number FROM quotations WHERE id=?", (quot_id,)).fetchone()
    if not quot or quot['status'] not in SENDABLE_STATUSES:
        flash('Cannot mark this quotation as Sent.', 'banner-error')
        return redirect(url_for('quotations.quotation_detail', quot_id=quot_id))
    db.execute("UPDATE quotations SET status='Sent' WHERE id=?", (quot_id,))
    db.commit()
    mark_saved()
    flash(f'Quotation {quot["quote_number"]} marked as Sent.', 'banner-success')
    return redirect(url_for('quotations.quotation_detail', quot_id=quot_id))


@quotations_bp.route('/quotations/<int:quot_id>/accept', methods=['POST'])
def quotation_accept(quot_id: int):
    db = get_db()
    quot = db.execute("SELECT status, quote_number FROM quotations WHERE id=?", (quot_id,)).fetchone()
    if not quot or quot['status'] not in ACCEPTABLE_STATUSES:
        flash('Cannot accept this quotation.', 'banner-error')
        return redirect(url_for('quotations.quotation_detail', quot_id=quot_id))
    db.execute("UPDATE quotations SET status='Accepted' WHERE id=?", (quot_id,))
    db.commit()
    mark_saved()
    flash(f'Quotation {quot["quote_number"]} accepted — now an open order.', 'banner-success')
    return redirect(url_for('quotations.quotation_detail', quot_id=quot_id))


@quotations_bp.route('/quotations/<int:quot_id>/reject', methods=['POST'])
def quotation_reject(quot_id: int):
    db = get_db()
    quot = db.execute("SELECT status, quote_number FROM quotations WHERE id=?", (quot_id,)).fetchone()
    if not quot or quot['status'] in ('Converted', 'Rejected'):
        flash('Cannot reject this quotation.', 'banner-error')
        return redirect(url_for('quotations.quotation_detail', quot_id=quot_id))
    db.execute("UPDATE quotations SET status='Rejected' WHERE id=?", (quot_id,))
    db.commit()
    mark_saved()
    flash(f'Quotation {quot["quote_number"]} marked as Rejected.', 'banner-success')
    return redirect(url_for('quotations.quotation_detail', quot_id=quot_id))


@quotations_bp.route('/quotations/<int:quot_id>/expire', methods=['POST'])
def quotation_expire(quot_id: int):
    db = get_db()
    quot = db.execute("SELECT status, quote_number FROM quotations WHERE id=?", (quot_id,)).fetchone()
    if not quot or quot['status'] in ('Converted', 'Expired'):
        flash('Cannot expire this quotation.', 'banner-error')
        return redirect(url_for('quotations.quotation_detail', quot_id=quot_id))
    db.execute("UPDATE quotations SET status='Expired' WHERE id=?", (quot_id,))
    db.commit()
    mark_saved()
    flash(f'Quotation {quot["quote_number"]} marked as Expired.', 'banner-success')
    return redirect(url_for('quotations.quotation_detail', quot_id=quot_id))


# ── advance payment ───────────────────────────────────────────────────────────

@quotations_bp.route('/quotations/<int:quot_id>/advance', methods=['POST'])
def quotation_advance(quot_id: int):
    db = get_db()
    quot = db.execute("SELECT * FROM quotations WHERE id=?", (quot_id,)).fetchone()
    if not quot or quot['status'] not in ADVANCEABLE_STATUSES:
        flash('Advance payments can only be recorded on accepted orders.', 'banner-error')
        return redirect(url_for('quotations.quotation_detail', quot_id=quot_id))

    raw_amount = request.form.get('amount', '').strip()
    date       = request.form.get('date', today_iso()).strip()
    method     = request.form.get('method', 'cash').strip()
    note       = request.form.get('note', '').strip()

    try:
        paise = rupee_str_to_paise(raw_amount)
    except ValueError:
        flash('Invalid amount.', 'banner-error')
        return redirect(url_for('quotations.quotation_detail', quot_id=quot_id))

    if paise <= 0:
        flash('Amount must be greater than zero.', 'banner-error')
        return redirect(url_for('quotations.quotation_detail', quot_id=quot_id))

    customer_id = quot['customer_id']
    if not customer_id:
        flash('Cannot record advance: quotation has no customer.', 'banner-error')
        return redirect(url_for('quotations.quotation_detail', quot_id=quot_id))

    with db:
        db.execute(
            "INSERT INTO payments (customer_id, quotation_id, paise, date, method, note) VALUES (?,?,?,?,?,?)",
            (customer_id, quot_id, paise, date, method, note),
        )
    mark_saved()
    flash('Advance payment recorded.', 'banner-success')
    return redirect(url_for('quotations.quotation_detail', quot_id=quot_id))


# ── convert to sale ───────────────────────────────────────────────────────────

@quotations_bp.route('/quotations/<int:quot_id>/convert', methods=['GET', 'POST'])
def quotation_convert(quot_id: int):
    db = get_db()
    quot = db.execute("""
        SELECT q.*, c.name AS customer_name
        FROM quotations q LEFT JOIN customers c ON q.customer_id = c.id
        WHERE q.id=?
    """, (quot_id,)).fetchone()
    if not quot or quot['status'] not in CONVERTIBLE_STATUSES:
        flash('Only accepted orders can be converted to a sale.', 'banner-error')
        return redirect(url_for('quotations.quotation_detail', quot_id=quot_id))

    lines = _quot_lines(db, quot_id)
    total_paise = sum(calc_line_total_paise(l['price_per_kg_paise'], l['grams']) for l in lines)
    advance_total = db.execute(
        "SELECT COALESCE(SUM(paise),0) FROM payments WHERE quotation_id=? AND voided=0",
        (quot_id,)
    ).fetchone()[0]

    inv_number, nxt = _next_invoice_number(db)
    allow_neg = db.execute(
        "SELECT value FROM settings WHERE key='allow_negative_stock'"
    ).fetchone()['value'] == '1'

    if request.method == 'POST':
        invoice_number = request.form.get('invoice_number', '').strip()
        sale_date      = request.form.get('date', today_iso()).strip()
        errors = []

        if not invoice_number:
            errors.append('Invoice number is required.')
        elif db.execute("SELECT id FROM sales WHERE invoice_number=?", (invoice_number,)).fetchone():
            errors.append(f'Invoice number "{invoice_number}" already exists.')

        if not allow_neg:
            for line in lines:
                stock = db.execute(
                    "SELECT COALESCE(SUM(grams),0) FROM stock_movements WHERE product_id=?",
                    (line['product_id'],)
                ).fetchone()[0]
                if stock - line['grams'] < 0:
                    errors.append(
                        f'{line["sku"]} – {line["product_name"]}: '
                        f'only {grams_to_kg_str(stock)} kg in stock, '
                        f'need {grams_to_kg_str(line["grams"])} kg.'
                    )

        if errors:
            for e in errors: flash(e, 'banner-error')
            return render_template('quotations/convert.html',
                quot=quot, lines=lines, total_paise=total_paise,
                advance_total=advance_total, inv_number=inv_number,
                form=request.form,
            )

        with db:
            cur = db.execute(
                "INSERT INTO sales (invoice_number, customer_id, date, notes) VALUES (?,?,?,?)",
                (invoice_number, quot['customer_id'], sale_date,
                 f'Converted from {quot["quote_number"]}'),
            )
            sale_id = cur.lastrowid

            for line in lines:
                db.execute(
                    "INSERT INTO sale_lines (sale_id, product_id, grams, price_per_kg_paise) VALUES (?,?,?,?)",
                    (sale_id, line['product_id'], line['grams'], line['price_per_kg_paise']),
                )
                db.execute(
                    """INSERT INTO stock_movements
                       (product_id, type, grams, ref_type, ref_id, date, note)
                       VALUES (?,?,?,?,?,?,?)""",
                    (line['product_id'], 'sale', -line['grams'],
                     'sale', sale_id, sale_date, invoice_number),
                )

            # Link advance payments to new sale
            db.execute(
                "UPDATE payments SET sale_id=? WHERE quotation_id=? AND voided=0",
                (sale_id, quot_id),
            )

            db.execute(
                "UPDATE quotations SET status='Converted', converted_sale_id=? WHERE id=?",
                (sale_id, quot_id),
            )
            _bump_invoice_number(db, nxt)

        mark_saved()
        flash(
            f'Order {quot["quote_number"]} converted to invoice {invoice_number}. '
            f'Advance of {_fmt_paise(advance_total)} carried over.',
            'banner-success',
        )
        return redirect(url_for('sales.sale_detail', sale_id=sale_id))

    return render_template('quotations/convert.html',
        quot=quot, lines=lines, total_paise=total_paise,
        advance_total=advance_total, inv_number=inv_number, form={},
    )


def _next_invoice_number(db):
    from app.routes.sales import _next_invoice_number as _nxt
    return _nxt(db)


def _fmt_paise(paise: int) -> str:
    from app.utils import paise_to_rupees_str
    return paise_to_rupees_str(paise)


# ── print ─────────────────────────────────────────────────────────────────────

@quotations_bp.route('/quotations/<int:quot_id>/print')
def quotation_print(quot_id: int):
    db = get_db()
    quot = db.execute("""
        SELECT q.*, c.name AS customer_name, c.phone AS customer_phone,
               c.address AS customer_address
        FROM quotations q LEFT JOIN customers c ON q.customer_id = c.id
        WHERE q.id=?
    """, (quot_id,)).fetchone()
    if not quot:
        abort(404)

    lines = _quot_lines(db, quot_id)
    total_paise = sum(calc_line_total_paise(l['price_per_kg_paise'], l['grams']) for l in lines)
    settings_rows = db.execute("SELECT key, value FROM settings").fetchall()
    settings = {r['key']: r['value'] for r in settings_rows}
    return render_template('quotations/print.html',
        quot=quot, lines=lines, total_paise=total_paise, settings=settings,
    )
