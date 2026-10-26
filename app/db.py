from __future__ import annotations

import os
import sys
import json
import sqlite3
import threading
from pathlib import Path
from datetime import datetime
from typing import Optional, Tuple

_lock = threading.Lock()
_conn: Optional[sqlite3.Connection] = None
_data_folder: Optional[Path] = None
_last_saved: Optional[datetime] = None


def get_base_dir() -> Path:
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).parent
    return Path(__file__).parent.parent


def get_config_path() -> Path:
    return get_base_dir() / 'config.json'


def _load_config() -> dict:
    p = get_config_path()
    try:
        return json.loads(p.read_text(encoding='utf-8')) if p.exists() else {}
    except Exception:
        return {}


def _save_config(data: dict) -> None:
    get_config_path().write_text(json.dumps(data, indent=2), encoding='utf-8')


def get_data_folder() -> Optional[Path]:
    global _data_folder
    if _data_folder is not None:
        return _data_folder
    folder = _load_config().get('data_folder')
    if folder and Path(folder).is_dir():
        _data_folder = Path(folder)
    return _data_folder


def open_data_folder(path) -> Tuple[str, Optional[int]]:
    """
    Prepare and lock a data folder.
    Returns ('ok', None), ('stale', old_pid), or ('live', running_pid).
    On 'ok' or after force_open_stale(), config is updated.
    """
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    for sub in ('backups', 'exports', 'logs'):
        (path / sub).mkdir(exist_ok=True)

    status, pid = _acquire_lock(path)
    if status == 'ok':
        _persist_data_folder(path)
    return status, pid


def force_open_stale(path) -> None:
    path = Path(path)
    _force_lock(path)
    _persist_data_folder(path)


def _persist_data_folder(path: Path) -> None:  # type: ignore[arg-type]
    global _data_folder
    cfg = _load_config()
    cfg['data_folder'] = str(path)
    _save_config(cfg)
    _data_folder = path


def close_and_release() -> None:
    global _conn, _data_folder, _last_saved
    with _lock:
        if _conn:
            try:
                _conn.commit()
                _conn.close()
            except Exception:
                pass
            _conn = None
        if _data_folder:
            _release_lock(_data_folder)
        _data_folder = None
        _last_saved = None


def forget_data_folder() -> None:
    cfg = _load_config()
    cfg.pop('data_folder', None)
    _save_config(cfg)
    global _data_folder
    _data_folder = None


def get_db() -> sqlite3.Connection:  # type: ignore[return]
    global _conn
    if _conn is None:
        with _lock:
            if _conn is None:
                df = get_data_folder()
                if df is None:
                    raise RuntimeError("No data folder configured")
                # Auto-open: acquire lock (force stale, raise on live)
                if not (df / 'beads.lock').exists():
                    _force_lock(df)
                else:
                    status, pid = _acquire_lock(df)
                    if status == 'live':
                        raise RuntimeError(f"Already open in another window (PID {pid})")
                    if status == 'stale':
                        _force_lock(df)

                db_path = df / 'beads.db'
                c = sqlite3.connect(str(db_path), check_same_thread=False)
                c.row_factory = sqlite3.Row
                c.execute("PRAGMA journal_mode=DELETE")
                c.execute("PRAGMA synchronous=FULL")
                c.execute("PRAGMA foreign_keys=ON")
                _run_migrations(c)
                _conn = c
    return _conn


def mark_saved() -> None:
    global _last_saved
    _last_saved = datetime.now()


def last_saved_str() -> str:
    return _last_saved.strftime('%H:%M') if _last_saved else ''


# ── migrations ────────────────────────────────────────────────────────────────

def _run_migrations(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)"
    )
    conn.commit()
    row = conn.execute("SELECT version FROM schema_version").fetchone()
    current = row['version'] if row else 0

    mdir = get_base_dir() / 'migrations'
    for mf in sorted(mdir.glob('*.sql')):
        try:
            num = int(mf.stem[:3])
        except ValueError:
            continue
        if num <= current:
            continue
        conn.executescript(mf.read_text(encoding='utf-8'))
        if current == 0:
            conn.execute("INSERT INTO schema_version (version) VALUES (?)", (num,))
        else:
            conn.execute("UPDATE schema_version SET version = ?", (num,))
        conn.commit()
        current = num


# ── lock file ─────────────────────────────────────────────────────────────────

def _acquire_lock(path: Path) -> Tuple[str, Optional[int]]:
    lock = path / 'beads.lock'
    if lock.exists():
        try:
            pid = int(lock.read_text().strip())
            if _pid_alive(pid):
                return 'live', pid
            return 'stale', pid
        except (ValueError, OSError):
            pass
    lock.write_text(str(os.getpid()), encoding='utf-8')
    return 'ok', None


def _force_lock(path: Path) -> None:
    (path / 'beads.lock').write_text(str(os.getpid()), encoding='utf-8')


def _release_lock(path: Path) -> None:
    try:
        (path / 'beads.lock').unlink(missing_ok=True)
    except OSError:
        pass


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False
