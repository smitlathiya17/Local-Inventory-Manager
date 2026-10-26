import sys
from pathlib import Path
from flask import Flask
from app.db import get_data_folder, last_saved_str


def create_app() -> Flask:
    if getattr(sys, 'frozen', False):
        base = Path(sys.executable).parent
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

    app.register_blueprint(setup_bp)
    app.register_blueprint(main_bp)
    app.register_blueprint(settings_bp)
    app.register_blueprint(products_bp)

    from app.utils import paise_to_rupees_str, format_date, grams_to_kg_str

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

    @app.context_processor
    def _sidebar_vars():
        df = get_data_folder()
        return {
            'data_folder_name': df.name if df else '',
            'last_saved': last_saved_str(),
        }

    return app
