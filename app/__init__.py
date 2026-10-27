import sys
from pathlib import Path
from flask import Flask, render_template
from app.db import get_data_folder, last_saved_str


def _bundle_dir() -> Path:
    """PyInstaller 6+ puts bundled files in _internal/ (sys._MEIPASS).
    Older versions and dev mode fall back to the project root."""
    if getattr(sys, 'frozen', False):
        return Path(getattr(sys, '_MEIPASS', Path(sys.executable).parent))
    return Path(__file__).parent.parent


def create_app() -> Flask:
    if getattr(sys, 'frozen', False):
        base = _bundle_dir()
        app = Flask(
            __name__,
            template_folder=str(base / 'app' / 'templates'),
            static_folder=str(base / 'app' / 'static'),
        )
    else:
        app = Flask(__name__)

    app.secret_key = 'bm-dev-secret-change-in-prod'

    from app.routes.setup import setup_bp
    from app.routes.main import main_bp
    from app.routes.settings import settings_bp
    from app.routes.products import products_bp
    from app.routes.suppliers import suppliers_bp
    from app.routes.purchase_orders import po_bp
    from app.routes.customers import customers_bp
    from app.routes.sales import sales_bp
    from app.routes.stock import stock_bp
    from app.routes.excel import excel_bp

    app.register_blueprint(setup_bp)
    app.register_blueprint(main_bp)
    app.register_blueprint(settings_bp)
    app.register_blueprint(products_bp)
    app.register_blueprint(suppliers_bp)
    app.register_blueprint(po_bp)
    app.register_blueprint(customers_bp)
    app.register_blueprint(sales_bp)
    app.register_blueprint(stock_bp)
    app.register_blueprint(excel_bp)

    from app.utils import paise_to_rupees_str, format_date, grams_to_kg_str, calc_line_total_paise, today_iso

    @app.template_filter('format_paise')
    def _fmt_paise(paise):
        return paise_to_rupees_str(int(paise))

    @app.template_filter('format_date')
    def _fmt_date(iso):
        return format_date(iso)

    @app.template_filter('format_grams')
    def _fmt_grams(grams):
        return grams_to_kg_str(int(grams))

    @app.template_filter('format_size')
    def _fmt_size(size_mm):
        if size_mm is None:
            return '—'
        n = float(size_mm)
        return f'{int(n)} mm' if n == int(n) else f'{n} mm'

    app.jinja_env.globals['calc_line_total'] = calc_line_total_paise
    app.jinja_env.globals['today_iso']       = today_iso

    @app.errorhandler(404)
    def _not_found(e):
        return render_template('errors/404.html'), 404

    @app.errorhandler(500)
    def _server_error(e):
        return render_template('errors/500.html'), 500

    @app.context_processor
    def _sidebar_vars():
        df = get_data_folder()
        return {
            'data_folder_name': df.name if df else '',
            'last_saved': last_saved_str(),
        }

    return app
