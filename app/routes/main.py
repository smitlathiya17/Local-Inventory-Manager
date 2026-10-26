import threading
from flask import Blueprint, render_template, redirect, url_for, current_app
from app.db import get_data_folder, close_and_release, last_saved_str, get_db
from app.utils import today_iso, format_date, grams_to_kg_str, calc_line_total_paise


def _build_dashboard_stats(db):
    try:
        # Stock value and total kg
        sv = db.execute("""
            SELECT COALESCE(SUM(sm.grams), 0)                           AS total_g,
                   COALESCE(SUM(CAST(sm.grams AS REAL) * p.price_per_kg_paise / 1000.0), 0) AS value_p
            FROM stock_movements sm
            JOIN products p ON sm.product_id = p.id
            WHERE p.active = 1
        """).fetchone()
        total_g   = int(sv['total_g'])
        value_p   = int(sv['value_p'])

        # Low-stock count
        low_rows = db.execute("""
            SELECT p.id, p.name,
                   COALESCE(SUM(sm.grams),0) AS stock_g,
                   p.reorder_grams
            FROM products p
            LEFT JOIN stock_movements sm ON sm.product_id = p.id
            WHERE p.active = 1 AND p.reorder_grams > 0
            GROUP BY p.id
            HAVING stock_g <= p.reorder_grams
            ORDER BY stock_g ASC
        """).fetchall()

        low_items = [
            {
                'name':       r['name'],
                'stock_kg':   grams_to_kg_str(r['stock_g']),
                'reorder_kg': grams_to_kg_str(r['reorder_grams']),
            }
            for r in low_rows
        ]

        return {
            'stock_value_paise':       value_p,
            'total_kg':                grams_to_kg_str(total_g),
            'low_stock_count':         len(low_items),
            'sales_this_month_paise':  0,   # M4
            'customers_owe_paise':     0,   # M4
            'open_orders':             0,   # M5
            'top_sellers':             [],  # M6
            'low_stock_items':         low_items,
            'recent_sales':            [],  # M4
        }
    except Exception:
        return {
            'stock_value_paise': 0, 'total_kg': '0.000',
            'low_stock_count': 0, 'sales_this_month_paise': 0,
            'customers_owe_paise': 0, 'open_orders': 0,
            'top_sellers': [], 'low_stock_items': [], 'recent_sales': [],
        }

main_bp = Blueprint('main', __name__)


@main_bp.route('/dashboard')
def dashboard():
    df = get_data_folder()
    if df is None:
        return redirect(url_for('setup.index'))
    try:
        db = get_db()
    except RuntimeError:
        return redirect(url_for('setup.setup_page'))

    stats = _build_dashboard_stats(db)
    return render_template('dashboard.html', stats=stats, today=format_date(today_iso()))


@main_bp.route('/shutdown', methods=['POST'])
def shutdown():
    df = get_data_folder()
    close_and_release()
    event = current_app.config.get('SHUTDOWN_EVENT')
    if event:
        def _delayed():
            import time
            time.sleep(0.4)
            event.set()
        threading.Thread(target=_delayed, daemon=True).start()
    return render_template('shutdown.html')
