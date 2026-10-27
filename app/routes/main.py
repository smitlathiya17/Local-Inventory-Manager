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

        # Sales this month
        month_prefix = today_iso()[:7]  # 'YYYY-MM'
        sales_month = db.execute("""
            SELECT COALESCE(SUM((sl.price_per_kg_paise * sl.grams + 500) / 1000), 0)
            FROM sales s
            JOIN sale_lines sl ON sl.sale_id = s.id
            WHERE s.voided = 0 AND s.date LIKE ?
        """, (month_prefix + '%',)).fetchone()[0]

        # Total customers owe
        invoiced = db.execute("""
            SELECT COALESCE(SUM((sl.price_per_kg_paise * sl.grams + 500) / 1000), 0)
            FROM sales s JOIN sale_lines sl ON sl.sale_id = s.id
            WHERE s.voided = 0 AND s.customer_id IS NOT NULL
        """).fetchone()[0]
        paid_total = db.execute(
            "SELECT COALESCE(SUM(paise), 0) FROM payments WHERE voided = 0"
        ).fetchone()[0]
        customers_owe = max(0, invoiced - paid_total)

        # Top sellers — last 30 days (sale movements are negative grams)
        top_rows = db.execute("""
            SELECT p.id, p.name,
                   ABS(COALESCE(SUM(sm.grams), 0)) AS kg_sold
            FROM stock_movements sm
            JOIN products p ON sm.product_id = p.id
            WHERE sm.type = 'sale'
              AND sm.date >= date('now', '-30 days')
            GROUP BY sm.product_id
            ORDER BY kg_sold DESC
            LIMIT 5
        """).fetchall()
        top_sellers = [
            {'id': r['id'], 'name': r['name'],
             'kg': r['kg_sold'], 'kg_str': grams_to_kg_str(r['kg_sold'])}
            for r in top_rows if r['kg_sold'] > 0
        ]

        # Recent sales
        recent_rows = db.execute("""
            SELECT s.id, s.invoice_number, s.date, s.voided,
                   c.name AS customer_name,
                   COALESCE(SUM((sl.price_per_kg_paise * sl.grams + 500) / 1000), 0) AS total_paise,
                   COALESCE(SUM(sl.grams), 0) AS total_grams,
                   COALESCE(pt.paid, 0) AS paid_paise
            FROM sales s
            LEFT JOIN customers c ON s.customer_id = c.id
            LEFT JOIN sale_lines sl ON sl.sale_id = s.id
            LEFT JOIN (
                SELECT sale_id, SUM(paise) AS paid FROM payments WHERE voided=0 GROUP BY sale_id
            ) pt ON pt.sale_id = s.id
            WHERE s.voided = 0
            GROUP BY s.id
            ORDER BY s.date DESC, s.id DESC
            LIMIT 10
        """).fetchall()
        recent_sales = []
        for r in recent_rows:
            total_p = r['total_paise']
            paid_p  = r['paid_paise']
            if paid_p >= total_p:   status = 'Paid'
            elif paid_p > 0:        status = 'Part paid'
            else:                   status = 'Pending'
            recent_sales.append({
                'id': r['id'],
                'invoice_number': r['invoice_number'],
                'date_display': format_date(r['date']),
                'customer_name': r['customer_name'] or '— Walk-in —',
                'kg_str': grams_to_kg_str(r['total_grams']),
                'total_paise': total_p,
                'status': status,
            })

        return {
            'stock_value_paise':       value_p,
            'total_kg':                grams_to_kg_str(total_g),
            'low_stock_count':         len(low_items),
            'sales_this_month_paise':  sales_month,
            'customers_owe_paise':     customers_owe,
            'top_sellers':             top_sellers,
            'low_stock_items':         low_items,
            'recent_sales':            recent_sales,
        }
    except Exception:
        return {
            'stock_value_paise': 0, 'total_kg': '0.000',
            'low_stock_count': 0, 'sales_this_month_paise': 0,
            'customers_owe_paise': 0,
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
