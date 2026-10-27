from __future__ import annotations

from flask import (
    Blueprint, render_template, request, redirect,
    url_for, abort, flash,
)
from app.db import get_db, mark_saved
from app.utils import kg_str_to_grams, grams_to_kg_str, today_iso

stock_bp = Blueprint('stock', __name__)

# ── helpers ───────────────────────────────────────────────────────────────────

_GROUP_COLS = {
    'material': ('p.material_id', 'COALESCE(m.name, \'— Unset —\')', 'LEFT JOIN materials m ON p.material_id = m.id'),
    'shape':    ('p.shape_id',    'COALESCE(s.name, \'— Unset —\')', 'LEFT JOIN shapes    s ON p.shape_id    = s.id'),
    'color':    ('p.color_id',    'COALESCE(c.name, \'— Unset —\')', 'LEFT JOIN colors    c ON p.color_id    = c.id'),
    'size':     ('p.size_mm',     'COALESCE(CAST(p.size_mm AS TEXT) || \' mm\', \'— Unset —\')', ''),
}


def _total_stock_rows(db, group_by: list[str]):
    """Return grouped rows for the Total stock screen."""
    if not group_by:
        # Flat list: one row per product
        rows = db.execute("""
            SELECT p.id, p.sku || ' – ' || p.name AS grp, 1 AS products,
                   COALESCE(SUM(sm.grams), 0) AS total_grams,
                   COALESCE(SUM(CAST(sm.grams AS REAL) * p.cost_per_kg_paise / 1000.0), 0) AS value_paise
            FROM products p
            LEFT JOIN stock_movements sm ON sm.product_id = p.id
            WHERE p.active = 1
            GROUP BY p.id
            ORDER BY total_grams DESC
        """).fetchall()
        return [dict(r) for r in rows]

    # Build dynamic group-by query
    select_parts, group_parts, join_parts, label_parts = [], [], [], []
    for key in group_by:
        if key not in _GROUP_COLS:
            continue
        col_id, col_label, join_sql = _GROUP_COLS[key]
        select_parts.append(col_label)
        group_parts.append(col_id)
        if join_sql:
            join_parts.append(join_sql)

    if not select_parts:
        return []

    label_sql   = " || ' · ' || ".join(select_parts)
    group_sql   = ', '.join(group_parts)
    joins_sql   = '\n        '.join(join_parts)

    rows = db.execute(f"""
        SELECT {label_sql} AS grp,
               COUNT(DISTINCT p.id) AS products,
               COALESCE(SUM(sm.grams), 0) AS total_grams,
               COALESCE(SUM(CAST(sm.grams AS REAL) * p.cost_per_kg_paise / 1000.0), 0) AS value_paise
        FROM products p
        LEFT JOIN stock_movements sm ON sm.product_id = p.id
        {joins_sql}
        WHERE p.active = 1
        GROUP BY {group_sql}
        ORDER BY total_grams DESC
    """).fetchall()
    return [dict(r) for r in rows]


# ── total stock ───────────────────────────────────────────────────────────────

@stock_bp.route('/stock')
def total_stock():
    db = get_db()
    group_by = request.args.getlist('group_by')
    rows = _total_stock_rows(db, group_by)

    total_grams  = sum(r['total_grams'] for r in rows)
    total_value  = int(sum(r['value_paise'] for r in rows))
    return render_template('stock/total.html',
        rows=rows, group_by=group_by,
        total_grams=total_grams, total_value=total_value,
    )


# ── stock count list ──────────────────────────────────────────────────────────

@stock_bp.route('/stock/count')
def count_list():
    db = get_db()
    counts = db.execute("""
        SELECT sc.id, sc.name, sc.status, sc.date,
               COUNT(scl.id) AS product_count,
               SUM(CASE WHEN scl.counted_grams IS NOT NULL THEN 1 ELSE 0 END) AS entered_count
        FROM stock_counts sc
        LEFT JOIN stock_count_lines scl ON scl.count_id = sc.id
        GROUP BY sc.id
        ORDER BY sc.created_at DESC
    """).fetchall()
    return render_template('stock/count_list.html', counts=counts)


# ── start new count ───────────────────────────────────────────────────────────

@stock_bp.route('/stock/count/new', methods=['POST'])
def count_new():
    db = get_db()
    name = request.form.get('name', '').strip() or f'Count {today_iso()}'
    date = request.form.get('date', today_iso()).strip()

    # Snapshot current stock for all active products
    products = db.execute("""
        SELECT p.id,
               COALESCE(SUM(sm.grams), 0) AS stock_grams
        FROM products p
        LEFT JOIN stock_movements sm ON sm.product_id = p.id
        WHERE p.active = 1
        GROUP BY p.id
    """).fetchall()

    if not products:
        flash('No active products to count.', 'banner-error')
        return redirect(url_for('stock.count_list'))

    with db:
        cur = db.execute(
            "INSERT INTO stock_counts (name, date) VALUES (?,?)", (name, date)
        )
        count_id = cur.lastrowid
        for p in products:
            db.execute(
                "INSERT INTO stock_count_lines (count_id, product_id, system_grams) VALUES (?,?,?)",
                (count_id, p['id'], p['stock_grams']),
            )
    mark_saved()
    flash(f'Stock count "{name}" started with {len(products)} products.', 'banner-success')
    return redirect(url_for('stock.count_detail', count_id=count_id))


# ── count detail (enter values) ───────────────────────────────────────────────

@stock_bp.route('/stock/count/<int:count_id>', methods=['GET', 'POST'])
def count_detail(count_id: int):
    db = get_db()
    count = db.execute("SELECT * FROM stock_counts WHERE id=?", (count_id,)).fetchone()
    if not count:
        abort(404)

    if request.method == 'POST' and count['status'] == 'Open':
        # Save entered values (without applying yet)
        lines = db.execute(
            "SELECT id FROM stock_count_lines WHERE count_id=?", (count_id,)
        ).fetchall()
        with db:
            for line in lines:
                raw = request.form.get(f'counted_{line["id"]}', '').strip()
                if raw:
                    try:
                        g = kg_str_to_grams(raw)
                        db.execute(
                            "UPDATE stock_count_lines SET counted_grams=? WHERE id=?",
                            (g, line['id']),
                        )
                    except ValueError:
                        pass
                else:
                    db.execute(
                        "UPDATE stock_count_lines SET counted_grams=NULL WHERE id=?",
                        (line['id'],)
                    )
        mark_saved()
        flash('Entries saved.', 'banner-success')
        return redirect(url_for('stock.count_detail', count_id=count_id))

    lines = db.execute("""
        SELECT scl.id, scl.system_grams, scl.counted_grams, scl.diff_grams,
               p.id AS product_id, p.sku, p.name AS product_name
        FROM stock_count_lines scl
        JOIN products p ON scl.product_id = p.id
        WHERE scl.count_id = ?
        ORDER BY p.name
    """, (count_id,)).fetchall()

    return render_template('stock/count_detail.html',
        count=count, lines=lines,
    )


# ── apply count ───────────────────────────────────────────────────────────────

@stock_bp.route('/stock/count/<int:count_id>/apply', methods=['POST'])
def count_apply(count_id: int):
    db = get_db()
    count = db.execute("SELECT * FROM stock_counts WHERE id=?", (count_id,)).fetchone()
    if not count or count['status'] != 'Open':
        flash('This count has already been applied.', 'banner-error')
        return redirect(url_for('stock.count_detail', count_id=count_id))

    lines = db.execute("""
        SELECT scl.id, scl.product_id, scl.system_grams, scl.counted_grams
        FROM stock_count_lines scl
        WHERE scl.count_id = ? AND scl.counted_grams IS NOT NULL
    """, (count_id,)).fetchall()

    if not lines:
        flash('Enter at least one counted quantity before applying.', 'banner-error')
        return redirect(url_for('stock.count_detail', count_id=count_id))

    corrections = 0
    with db:
        for line in lines:
            diff = line['counted_grams'] - line['system_grams']
            db.execute(
                "UPDATE stock_count_lines SET diff_grams=? WHERE id=?",
                (diff, line['id']),
            )
            if diff != 0:
                db.execute(
                    """INSERT INTO stock_movements
                       (product_id, type, grams, ref_type, ref_id, date, note)
                       VALUES (?,?,?,?,?,?,?)""",
                    (line['product_id'], 'count_correction', diff,
                     'stock_count', count_id,
                     count['date'], count['name']),
                )
                corrections += 1
        db.execute("UPDATE stock_counts SET status='Applied' WHERE id=?", (count_id,))
    mark_saved()
    flash(
        f'Count applied. {corrections} product{"s" if corrections != 1 else ""} adjusted.',
        'banner-success',
    )
    return redirect(url_for('stock.count_detail', count_id=count_id))
