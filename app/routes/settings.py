from flask import Blueprint, render_template, request, redirect, url_for, flash
from app.db import (
    get_data_folder, get_db, mark_saved,
    create_backup, list_backups, restore_backup,
)

settings_bp = Blueprint('settings', __name__)

EDITABLE_SETTINGS = [
    'business_name', 'business_address', 'business_phone',
    'invoice_prefix', 'invoice_next',
    'po_prefix', 'po_next',
    'low_stock_default', 'allow_negative_stock', 'backup_retention',
]


@settings_bp.route('/settings', methods=['GET', 'POST'])
def settings():
    df = get_data_folder()
    if df is None:
        return redirect(url_for('setup.index'))

    db = get_db()
    if request.method == 'POST':
        for key in EDITABLE_SETTINGS:
            val = request.form.get(key)
            if val is not None:
                db.execute(
                    "UPDATE settings SET value = ? WHERE key = ?",
                    (val.strip(), key),
                )
        db.commit()
        mark_saved()
        flash('Settings saved.', 'banner-success')
        return redirect(url_for('settings.settings'))

    rows = db.execute("SELECT key, value FROM settings").fetchall()
    s = {r['key']: r['value'] for r in rows}
    return render_template(
        'settings.html',
        settings=s,
        data_folder_path=str(df),
        backups=list_backups(),
    )


@settings_bp.route('/settings/backup', methods=['POST'])
def backup_now():
    dst = create_backup('manual')
    if dst:
        flash(f'Backup created: {dst.name}', 'banner-success')
    else:
        flash('Backup failed — no data folder set.', 'banner-error')
    return redirect(url_for('settings.settings'))


@settings_bp.route('/settings/restore', methods=['POST'])
def restore():
    name = request.form.get('backup_name', '').strip()
    if not name:
        flash('No backup selected.', 'banner-error')
        return redirect(url_for('settings.settings'))
    if restore_backup(name):
        flash(f'Restored from {name}. Database has been replaced.', 'banner-success')
    else:
        flash('Restore failed — backup file not found.', 'banner-error')
    return redirect(url_for('settings.settings'))
