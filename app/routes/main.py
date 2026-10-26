import threading
from flask import Blueprint, render_template, redirect, url_for, current_app
from app.db import get_data_folder, close_and_release, last_saved_str, get_db
from app.utils import today_iso, format_date, grams_to_kg_str

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

    # M1 placeholder — real queries added in later milestones
    stats = {
        'stock_value_paise': 0,
        'total_kg': '0.000',
        'low_stock_count': 0,
        'sales_this_month_paise': 0,
        'customers_owe_paise': 0,
        'open_orders': 0,
        'top_sellers': [],
        'low_stock_items': [],
        'recent_sales': [],
    }
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
