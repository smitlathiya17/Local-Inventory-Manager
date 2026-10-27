import sys
import socket
import threading
import time
import webbrowser

shutdown_event = threading.Event()
DEFAULT_PORT   = 5050
_FROZEN        = getattr(sys, 'frozen', False)


def find_free_port(preferred: int = DEFAULT_PORT) -> int:
    try:
        with socket.socket() as s:
            s.bind(('127.0.0.1', preferred))
        return preferred
    except OSError:
        with socket.socket() as s:
            s.bind(('', 0))
            return s.getsockname()[1]


def _log(msg: str) -> None:
    if not _FROZEN:
        print(msg)


def main() -> None:
    from app import create_app
    app = create_app()
    app.config['SHUTDOWN_EVENT'] = shutdown_event

    port = find_free_port()
    url  = f'http://127.0.0.1:{port}'

    server = threading.Thread(
        target=lambda: app.run(
            host='127.0.0.1', port=port,
            debug=False, use_reloader=False, threaded=True,
        ),
        daemon=True,
    )
    server.start()

    time.sleep(0.6)
    _log(f'Beads Manager running at {url}')
    _log('Use the Close button in the app to stop.')
    webbrowser.open(url)

    shutdown_event.wait()
    _log('Closed. Safe to remove pendrive.')
    sys.exit(0)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        import traceback
        tb = traceback.format_exc()
        # Show a GUI error dialog on Windows when running as a windowed exe
        if sys.platform == 'win32':
            try:
                import ctypes
                ctypes.windll.user32.MessageBoxW(
                    0,
                    f'Beads Manager failed to start:\n\n{exc}\n\n{tb}',
                    'Beads Manager – Error',
                    0x10,   # MB_ICONERROR
                )
            except Exception:
                pass
        raise
