import sys
from pathlib import Path
from flask import Blueprint, render_template, request, jsonify, redirect, url_for
from app.db import (
    get_data_folder, open_data_folder, force_open_stale,
    close_and_release, forget_data_folder,
)

setup_bp = Blueprint('setup', __name__)


@setup_bp.route('/')
def index():
    if get_data_folder() is not None:
        return redirect(url_for('main.dashboard'))
    return redirect(url_for('setup.setup_page'))


@setup_bp.route('/setup')
def setup_page():
    # On Windows, start at the drives list so pendrive is immediately visible
    start = '' if sys.platform == 'win32' else str(Path.home())
    return render_template('setup.html', start_path=start)


@setup_bp.route('/api/browse')
def browse():
    path_str = request.args.get('path', '')
    if not path_str:
        if sys.platform == 'win32':
            drives = []
            for letter in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ':
                d = Path(f'{letter}:/')
                if d.exists():
                    drives.append({'name': f'{letter}:', 'path': str(d), 'type': 'drive'})
            return jsonify({'path': '', 'parent': None, 'entries': drives, 'name': 'Drives'})
        path_str = str(Path.home())

    path = Path(path_str).resolve()
    if not path.is_dir():
        return jsonify({'error': 'Not a directory'}), 400

    entries = []
    try:
        for item in sorted(path.iterdir(), key=lambda p: p.name.lower()):
            if item.is_dir() and not item.name.startswith('.'):
                entries.append({'name': item.name, 'path': str(item), 'type': 'dir'})
    except PermissionError:
        pass

    parent_path = path.parent
    parent = str(parent_path) if parent_path != path else None

    return jsonify({
        'path': str(path),
        'parent': parent,
        'entries': entries,
        'name': path.name or str(path),
    })


@setup_bp.route('/api/create-folder', methods=['POST'])
def create_folder():
    data = request.get_json(silent=True) or {}
    parent = data.get('parent', '')
    name = (data.get('name') or '').strip()
    if not name or '/' in name or '\\' in name or name in ('..', '.'):
        return jsonify({'error': 'Invalid folder name'}), 400
    new_path = Path(parent) / name
    try:
        new_path.mkdir(parents=True, exist_ok=True)
        return jsonify({'path': str(new_path)})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@setup_bp.route('/api/select-folder', methods=['POST'])
def select_folder():
    data = request.get_json(silent=True) or {}
    path = (data.get('path') or '').strip()
    if not path:
        return jsonify({'error': 'No path provided'}), 400

    status, pid = open_data_folder(path)
    if status == 'ok':
        return jsonify({'status': 'ok', 'redirect': url_for('main.dashboard')})
    elif status == 'stale':
        return jsonify({'status': 'stale', 'pid': pid, 'path': path})
    else:
        return jsonify({'status': 'live', 'pid': pid})


@setup_bp.route('/api/force-open', methods=['POST'])
def force_open():
    data = request.get_json(silent=True) or {}
    path = (data.get('path') or '').strip()
    if not path:
        return jsonify({'error': 'No path'}), 400
    force_open_stale(path)
    return jsonify({'status': 'ok', 'redirect': url_for('main.dashboard')})


@setup_bp.route('/open-another', methods=['POST'])
def open_another():
    close_and_release()
    forget_data_folder()
    return redirect(url_for('setup.setup_page'))
