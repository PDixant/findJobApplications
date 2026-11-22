"""Job Watcher desktop app.

    python app.py              open the app in its own window
    python app.py --browser    open it in your web browser instead (no pywebview needed)
    python app.py --dev        for changing the interface: serves the API on port 8765 for
                               `npm run dev` in the ui folder (see README, section 14)

The window talks to a small server on your own computer (127.0.0.1) that only answers it.
Runs started here are the same runs as `python job_watcher.py`, and share its lock, so the app,
a terminal and the scheduled task never run at the same time. Your tracker edits still happen
in job_tracker.xlsx for now; the app shows them.
"""
import argparse
import logging
import secrets
import socket
import sys
import threading
import time
import webbrowser

DEV_PORT = 8765


def free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main():
    parser = argparse.ArgumentParser(description="Open the Job Watcher app.")
    parser.add_argument("--browser", action="store_true", help="open in your web browser instead of a window")
    parser.add_argument("--dev", action="store_true", help="API on port 8765 for the Vite dev server")
    parser.add_argument("--port", type=int, help="port for the local server (default: any free port)")
    args = parser.parse_args()

    try:
        import uvicorn
        from jobwatcher.base import BASE, CANCEL, LOG_PATH, log
        from jobwatcher.config import load_env
        from jobwatcher.server import create_app
    except ImportError as exc:
        sys.exit(f"The app needs a few more packages ({exc.name} is missing). Install them with:\n"
                 f"    pip install -r requirements-app.txt")

    handlers = [logging.FileHandler(LOG_PATH, encoding="utf-8")]
    if sys.stdout:  # None when started with pythonw
        handlers.append(logging.StreamHandler(sys.stdout))
    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s", datefmt="%Y-%m-%d %H:%M",
                        handlers=handlers)
    logging.getLogger("uvicorn.error").setLevel(logging.WARNING)
    load_env()

    token = secrets.token_urlsafe(24)
    app = create_app(token, dev=args.dev)
    port = args.port or (DEV_PORT if args.dev else free_port())
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning",
                                           access_log=False, lifespan="on"))
    threading.Thread(target=server.run, name="jobwatcher-server", daemon=True).start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    else:
        sys.exit(f"The app's local server did not start on port {port}. Is another copy open?")

    url = f"http://127.0.0.1:{port}/#token={token}"
    runs = app.state.runs

    def finish_run():
        if runs.active():
            CANCEL.set()
            log.info("Closing: finishing the boards being read now, then saving. This can take a minute.")
            runs.wait()

    try:
        if args.dev:
            print(f"API ready on port {port}. In the ui folder run `npm run dev`, then open:\n"
                  f"    http://localhost:5173/#token={token}\nPress Ctrl+C to stop.")
            wait_forever()
        elif args.browser:
            print(f"Opening Job Watcher in your browser. Keep this window open while you use it.\n"
                  f"    {url}\nPress Ctrl+C to stop.")
            webbrowser.open(url)
            wait_forever()
        else:
            open_window(url, runs, BASE)
    finally:
        finish_run()
        server.should_exit = True


def wait_forever():
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass


def open_window(url, runs, base):
    try:
        import webview
    except ImportError:
        print("pywebview is not installed, so the app opens in your browser instead.\n"
              "For its own window: pip install pywebview")
        webbrowser.open(url)
        wait_forever()
        return

    window = webview.create_window("Job Watcher", url, width=1340, height=880, min_size=(1000, 640),
                                   background_color="#F4F6FA", text_select=True)

    def on_closing():
        if not runs.active():
            return True
        return window.create_confirmation_dialog(
            "A run is going",
            "Close the app and stop the run? The boards being read now finish first, then "
            "everything found so far is saved.")

    window.events.closing += on_closing
    # Not private: the window remembers your filters and when you last looked at new matches.
    webview.start(private_mode=False, storage_path=str(base / ".app_window"))


if __name__ == "__main__":
    main()
