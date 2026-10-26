import sys
import socket
import threading
import time
import webbrowser

shutdown_event = threading.Event()


def find_free_port() -> int:
    with socket.socket() as s:
        s.bind(('', 0))
        return s.getsockname()[1]


def main() -> None:
    from app import create_app
    app = create_app()
    app.config['SHUTDOWN_EVENT'] = shutdown_event

    port = find_free_port()
    url = f'http://127.0.0.1:{port}'

    server = threading.Thread(
        target=lambda: app.run(
            host='127.0.0.1', port=port,
            debug=False, use_reloader=False, threaded=True,
        ),
        daemon=True,
    )
    server.start()

    time.sleep(0.6)
    print(f"Beads Manager is running at {url}")
    print("Use the Close button in the app to stop.")
    webbrowser.open(url)

    shutdown_event.wait()
    print("Beads Manager closed. Safe to remove pendrive.")
    sys.exit(0)


if __name__ == '__main__':
    main()
