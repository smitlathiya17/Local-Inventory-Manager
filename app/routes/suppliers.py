from flask import Blueprint, render_template, request, redirect, url_for, abort, flash
from app.db import get_data_folder, get_db, mark_saved

suppliers_bp = Blueprint('suppliers', __name__)


def _require_folder():
    if get_data_folder() is None:
        return redirect(url_for('setup.index'))


@suppliers_bp.route('/suppliers')
def supplier_list():
    r = _require_folder()
    if r: return r
    db = get_db()
    rows = db.execute("SELECT * FROM suppliers ORDER BY name").fetchall()
    return render_template('suppliers/list.html', rows=rows)


@suppliers_bp.route('/suppliers/new', methods=['GET', 'POST'])
def supplier_new():
    r = _require_folder()
    if r: return r
    db = get_db()

    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        if not name:
            flash('Supplier name is required.', 'banner-error')
            return render_template('suppliers/form.html', supplier=None, form=request.form)
        db.execute(
            "INSERT INTO suppliers (name, contact, phone, address, notes) VALUES (?,?,?,?,?)",
            (name,
             request.form.get('contact', '').strip(),
             request.form.get('phone', '').strip(),
             request.form.get('address', '').strip(),
             request.form.get('notes', '').strip()),
        )
        db.commit()
        mark_saved()
        flash(f'Supplier "{name}" added.', 'banner-success')
        return redirect(url_for('suppliers.supplier_list'))

    return render_template('suppliers/form.html', supplier=None, form={})


@suppliers_bp.route('/suppliers/<int:supplier_id>/edit', methods=['GET', 'POST'])
def supplier_edit(supplier_id: int):
    r = _require_folder()
    if r: return r
    db = get_db()
    supplier = db.execute("SELECT * FROM suppliers WHERE id=?", (supplier_id,)).fetchone()
    if not supplier:
        abort(404)

    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        if not name:
            flash('Supplier name is required.', 'banner-error')
            return render_template('suppliers/form.html', supplier=supplier, form=request.form)
        db.execute(
            "UPDATE suppliers SET name=?, contact=?, phone=?, address=?, notes=? WHERE id=?",
            (name,
             request.form.get('contact', '').strip(),
             request.form.get('phone', '').strip(),
             request.form.get('address', '').strip(),
             request.form.get('notes', '').strip(),
             supplier_id),
        )
        db.commit()
        mark_saved()
        flash(f'Supplier "{name}" saved.', 'banner-success')
        return redirect(url_for('suppliers.supplier_list'))

    return render_template('suppliers/form.html', supplier=supplier, form={})
