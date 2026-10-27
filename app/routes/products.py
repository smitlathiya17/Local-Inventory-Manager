from __future__ import annotations

from flask import (
    Blueprint, render_template, request, redirect,
    url_for, jsonify, abort, flash,
)
from app.db import get_data_folder, get_db, mark_saved
from app.utils import (
    kg_str_to_grams, grams_to_kg_str,
    rupee_str_to_paise, calc_line_total_paise, today_iso,
)

products_bp = Blueprint('products', __name__)

ADJUSTMENT_REASONS = ['Damage', 'Loss', 'Found', 'Correction', 'Sample', 'Other']

_SORT_MAP = {
    'name':     'p.name',
    'sku':      'p.sku',
    'material': 'm.name',
    'shape':    's.name',
    'size':     'p.size_mm',
    'color':    'c.name',
    'stock':    'stock_grams',
    'price':    'p.price_per_kg_paise',
}


def _managed_lists(db):
    materials = db.execute("SELECT id, name FROM materials WHERE active=1 ORDER BY name").fetchall()
    shapes    = db.execute("SELECT id, name FROM shapes    WHERE active=1 ORDER BY name").fetchall()
    colors    = db.execute("SELECT id, name FROM colors    WHERE active=1 ORDER BY name").fetchall()
    return materials, shapes, colors


def _stock_grams(db, product_id: int) -> int:
    row = db.execute(
        "SELECT COALESCE(SUM(grams),0) AS g FROM stock_movements WHERE product_id=?",
        (product_id,),
    ).fetchone()
    return row['g']


def _require_folder():
    if get_data_folder() is None:
        return redirect(url_for('setup.index'))


# ── list ──────────────────────────────────────────────────────────────────────

@products_bp.route('/products')
def product_list():
    redir = _require_folder()
    if redir:
        return redir
    db = get_db()

    q          = request.args.get('q', '').strip()
    f_material = request.args.get('material', '')
    f_shape    = request.args.get('shape', '')
    f_size     = request.args.get('size', '')
    f_color    = request.args.get('color', '')
    low_only   = request.args.get('low', '') == '1'
    show_all   = request.args.get('all', '') == '1'
    sort       = request.args.get('sort', 'name')
    desc       = request.args.get('desc', '') == '1'

    # Exact barcode/SKU scan: redirect straight to edit page
    if request.args.get('exact') == '1' and q:
        hit = db.execute(
            "SELECT id FROM products WHERE barcode=? OR sku=?", (q, q)
        ).fetchone()
        if hit:
            return redirect(url_for('products.product_edit', product_id=hit['id']))

    where, params = [], []
    if not show_all:
        where.append('p.active = 1')
    if q:
        where.append('(p.name LIKE ? OR p.sku LIKE ? OR p.barcode = ?)')
        params += [f'%{q}%', f'%{q}%', q]
    if f_material:
        where.append('p.material_id = ?');  params.append(f_material)
    if f_shape:
        where.append('p.shape_id = ?');     params.append(f_shape)
    if f_size:
        where.append('p.size_mm = ?');      params.append(float(f_size))
    if f_color:
        where.append('p.color_id = ?');     params.append(f_color)

    where_sql  = ('WHERE ' + ' AND '.join(where)) if where else ''
    order_col  = _SORT_MAP.get(sort, 'p.name')
    order_dir  = 'DESC' if desc else 'ASC'

    rows = db.execute(f"""
        SELECT p.id, p.sku, p.name, p.size_mm, p.barcode, p.active,
               p.cost_per_kg_paise, p.price_per_kg_paise, p.reorder_grams,
               m.name AS material_name, s.name AS shape_name, c.name AS color_name,
               COALESCE(SUM(sm.grams), 0) AS stock_grams,
               COALESCE(cmt.committed_grams, 0) AS committed_grams
        FROM products p
        LEFT JOIN materials m ON p.material_id = m.id
        LEFT JOIN shapes    s ON p.shape_id    = s.id
        LEFT JOIN colors    c ON p.color_id    = c.id
        LEFT JOIN stock_movements sm ON sm.product_id = p.id
        LEFT JOIN (
            SELECT ql.product_id, SUM(ql.grams) AS committed_grams
            FROM quotation_lines ql
            JOIN quotations q ON ql.quotation_id = q.id
            WHERE q.status = 'Accepted'
            GROUP BY ql.product_id
        ) cmt ON cmt.product_id = p.id
        {where_sql}
        GROUP BY p.id
        ORDER BY {order_col} {order_dir}
    """, params).fetchall()

    total_count = len(rows)
    if low_only:
        rows = [r for r in rows if r['reorder_grams'] > 0 and r['stock_grams'] <= r['reorder_grams']]

    total_stock_grams  = sum(r['stock_grams'] for r in rows)
    total_value_paise  = sum(
        calc_line_total_paise(r['price_per_kg_paise'], r['stock_grams']) for r in rows
    )

    materials, shapes, colors = _managed_lists(db)
    sizes = db.execute(
        "SELECT DISTINCT size_mm FROM products WHERE size_mm IS NOT NULL ORDER BY size_mm"
    ).fetchall()

    return render_template('products/list.html',
        rows=rows, total_count=total_count, showing_count=len(rows),
        total_stock_grams=total_stock_grams,
        total_stock_kg=grams_to_kg_str(total_stock_grams),
        total_value_paise=total_value_paise,
        materials=materials, shapes=shapes, colors=colors, sizes=sizes,
        q=q, f_material=f_material, f_shape=f_shape,
        f_size=f_size, f_color=f_color,
        low_only=low_only, show_all=show_all, sort=sort, desc=desc,
    )


# ── add / edit ────────────────────────────────────────────────────────────────

@products_bp.route('/products/new', methods=['GET', 'POST'])
def product_new():
    redir = _require_folder()
    if redir:
        return redir
    db = get_db()
    materials, shapes, colors = _managed_lists(db)

    if request.method == 'POST':
        errors = []
        sku  = request.form.get('sku', '').strip().upper()
        name = request.form.get('name', '').strip()

        if not sku:
            errors.append('SKU is required.')
        if not name:
            errors.append('Name is required.')

        barcode = request.form.get('barcode', '').strip() or sku

        try:
            cost_paise  = rupee_str_to_paise(request.form.get('cost_per_kg', '0') or '0')
            price_paise = rupee_str_to_paise(request.form.get('price_per_kg', '0') or '0')
            reorder_g   = kg_str_to_grams(request.form.get('reorder_kg', '0') or '0')
        except ValueError as e:
            errors.append(str(e))

        size_mm_raw = request.form.get('size_mm', '').strip()
        size_mm = float(size_mm_raw) if size_mm_raw else None

        material_id = request.form.get('material_id') or None
        shape_id    = request.form.get('shape_id')    or None
        color_id    = request.form.get('color_id')    or None
        notes       = request.form.get('notes', '').strip()

        if not errors:
            if db.execute("SELECT 1 FROM products WHERE sku=?", (sku,)).fetchone():
                errors.append(f'SKU "{sku}" already exists.')
            if barcode and db.execute(
                "SELECT 1 FROM products WHERE barcode=?", (barcode,)
            ).fetchone():
                errors.append(f'Barcode "{barcode}" is already used by another product.')

        if errors:
            for e in errors:
                flash(e, 'banner-error')
            return render_template('products/form.html',
                product=None, materials=materials, shapes=shapes, colors=colors,
                form=request.form,
            )

        with db:
            cur = db.execute(
                """INSERT INTO products
                   (sku, name, material_id, shape_id, size_mm, color_id,
                    barcode, cost_per_kg_paise, price_per_kg_paise, reorder_grams, notes)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (sku, name, material_id, shape_id, size_mm, color_id,
                 barcode, cost_paise, price_paise, reorder_g, notes),
            )
        mark_saved()
        flash(f'Product "{name}" added.', 'banner-success')
        return redirect(url_for('products.product_list'))

    return render_template('products/form.html',
        product=None, materials=materials, shapes=shapes, colors=colors, form={},
    )


@products_bp.route('/products/<int:product_id>/edit', methods=['GET', 'POST'])
def product_edit(product_id: int):
    redir = _require_folder()
    if redir:
        return redir
    db = get_db()
    product = db.execute(
        "SELECT * FROM products WHERE id=?", (product_id,)
    ).fetchone()
    if not product:
        abort(404)

    materials, shapes, colors = _managed_lists(db)
    stock_g = _stock_grams(db, product_id)

    if request.method == 'POST':
        errors = []
        name = request.form.get('name', '').strip()
        if not name:
            errors.append('Name is required.')

        barcode = request.form.get('barcode', '').strip() or product['sku']

        try:
            cost_paise  = rupee_str_to_paise(request.form.get('cost_per_kg', '0') or '0')
            price_paise = rupee_str_to_paise(request.form.get('price_per_kg', '0') or '0')
            reorder_g   = kg_str_to_grams(request.form.get('reorder_kg', '0') or '0')
        except ValueError as e:
            errors.append(str(e))

        size_mm_raw = request.form.get('size_mm', '').strip()
        size_mm = float(size_mm_raw) if size_mm_raw else None

        material_id = request.form.get('material_id') or None
        shape_id    = request.form.get('shape_id')    or None
        color_id    = request.form.get('color_id')    or None
        notes       = request.form.get('notes', '').strip()

        if not errors and barcode:
            conflict = db.execute(
                "SELECT 1 FROM products WHERE barcode=? AND id!=?",
                (barcode, product_id),
            ).fetchone()
            if conflict:
                errors.append(f'Barcode "{barcode}" is already used by another product.')

        if errors:
            for e in errors:
                flash(e, 'banner-error')
            return render_template('products/form.html',
                product=product, materials=materials, shapes=shapes, colors=colors,
                stock_grams=stock_g, form=request.form,
            )

        with db:
            db.execute(
                """UPDATE products SET
                   name=?, material_id=?, shape_id=?, size_mm=?, color_id=?,
                   barcode=?, cost_per_kg_paise=?, price_per_kg_paise=?,
                   reorder_grams=?, notes=?
                   WHERE id=?""",
                (name, material_id, shape_id, size_mm, color_id,
                 barcode or None, cost_paise, price_paise, reorder_g, notes,
                 product_id),
            )
        mark_saved()
        flash(f'Product "{name}" saved.', 'banner-success')
        return redirect(url_for('products.product_list'))

    return render_template('products/form.html',
        product=product, materials=materials, shapes=shapes, colors=colors,
        stock_grams=stock_g, form={},
    )


@products_bp.route('/products/<int:product_id>/deactivate', methods=['POST'])
def product_deactivate(product_id: int):
    redir = _require_folder()
    if redir:
        return redir
    db = get_db()
    product = db.execute("SELECT name FROM products WHERE id=?", (product_id,)).fetchone()
    if not product:
        abort(404)
    db.execute("UPDATE products SET active=0 WHERE id=?", (product_id,))
    db.commit()
    mark_saved()
    flash(f'"{product["name"]}" deactivated.', 'banner-success')
    return redirect(url_for('products.product_list'))


# ── adjustments ───────────────────────────────────────────────────────────────

@products_bp.route('/products/<int:product_id>/adjust', methods=['GET', 'POST'])
def product_adjust(product_id: int):
    redir = _require_folder()
    if redir:
        return redir
    db = get_db()
    product = db.execute("SELECT * FROM products WHERE id=?", (product_id,)).fetchone()
    if not product:
        abort(404)

    stock_g = _stock_grams(db, product_id)

    if request.method == 'POST':
        errors = []
        direction = request.form.get('direction', '+')
        reason    = request.form.get('reason', '')
        note      = request.form.get('note', '').strip()
        date      = request.form.get('date', today_iso()).strip()

        try:
            qty_g = kg_str_to_grams(request.form.get('qty_kg', '0') or '0')
        except ValueError as e:
            errors.append(str(e))
            qty_g = 0

        if qty_g == 0:
            errors.append('Adjustment quantity must not be zero.')
        if reason not in ADJUSTMENT_REASONS:
            errors.append('Please select a reason.')

        if errors:
            for e in errors:
                flash(e, 'banner-error')
        else:
            signed_g = qty_g if direction == '+' else -qty_g
            full_note = f'{reason}: {note}' if note else reason
            with db:
                db.execute(
                    """INSERT INTO stock_movements
                       (product_id, type, grams, date, note)
                       VALUES (?,?,?,?,?)""",
                    (product_id, 'adjustment', signed_g, date, full_note),
                )
            mark_saved()
            flash(
                f'Adjusted {product["name"]}: {"+" if signed_g > 0 else ""}'
                f'{grams_to_kg_str(signed_g)} kg ({reason}).',
                'banner-success',
            )
            return redirect(url_for('products.product_list'))

    history = db.execute(
        """SELECT date, grams, note FROM stock_movements
           WHERE product_id=? AND type='adjustment'
           ORDER BY created_at DESC LIMIT 30""",
        (product_id,),
    ).fetchall()

    return render_template('products/adjust.html',
        product=product, stock_grams=stock_g,
        reasons=ADJUSTMENT_REASONS, history=history,
        today=today_iso(),
    )


# ── managed lists API ─────────────────────────────────────────────────────────

_LIST_TABLE = {'materials': 'materials', 'shapes': 'shapes', 'colors': 'colors'}


@products_bp.route('/api/product')
def api_product():
    db = get_db()
    q = request.args.get('q', '').strip()
    if not q:
        return jsonify({'error': 'No query'}), 400
    row = db.execute("""
        SELECT p.id, p.sku, p.name, p.cost_per_kg_paise, p.price_per_kg_paise,
               COALESCE(SUM(sm.grams), 0) AS stock_grams
        FROM products p
        LEFT JOIN stock_movements sm ON sm.product_id = p.id
        WHERE p.active = 1 AND (p.barcode = ? OR p.sku = ?)
        GROUP BY p.id
    """, (q, q.upper())).fetchone()
    if not row:
        return jsonify({'error': f'Not found: {q}'}), 404
    return jsonify(dict(row))


@products_bp.route('/api/managed/<list_name>', methods=['POST'])
def add_managed(list_name: str):
    table = _LIST_TABLE.get(list_name)
    if not table:
        abort(404)
    data = request.get_json(silent=True) or {}
    name = (data.get('name') or '').strip()
    if not name:
        return jsonify({'error': 'Name is required'}), 400
    db = get_db()
    try:
        cur = db.execute(f"INSERT INTO {table} (name) VALUES (?)", (name,))
        db.commit()
        return jsonify({'id': cur.lastrowid, 'name': name})
    except Exception:
        row = db.execute(
            f"SELECT id, name FROM {table} WHERE name=?", (name,)
        ).fetchone()
        if row:
            return jsonify({'id': row['id'], 'name': row['name']})
        return jsonify({'error': 'Could not add item'}), 500
