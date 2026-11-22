"""Opens Job Watcher, and the `jobwatcher` command that works from any folder.

    jobwatcher                      open the app (a second launch brings the open window forward)
    jobwatcher --browser            open it in your web browser instead of its own window
    jobwatcher run [options]        a run, the same as `python job_watcher.py [options]`
    jobwatcher outreach [options]   the same as `python outreach.py [options]`
    jobwatcher folder               show where your settings and data are, and open the folder

The window talks to a small server on your own computer (127.0.0.1) that only answers it.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import secrets
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

DEV_PORT = 8765


def free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def instance_file():
    from .base import BASE
    return BASE / ".app_window" / "instance.json"


def _call(port, token, path, method="GET"):
    req = urllib.request.Request(f"http://127.0.0.1:{port}/api/{path}", method=method,
                                 headers={"X-Token": token})
    with urllib.request.urlopen(req, timeout=2) as r:
        return json.loads(r.read() or b"null")


def running_instance():
    """The app already open on this computer, if any: (port, token)."""
    try:
        info = json.loads(instance_file().read_text(encoding="utf-8"))
        port, token = int(info["port"]), str(info["token"])
        if (_call(port, token, "ping") or {}).get("app") == "jobwatcher":
            return port, token
    except Exception:
        pass
    return None


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "run":
        from .cli import main as run_main
        sys.argv = ["jobwatcher run", *argv[1:]]
        return run_main()
    if argv and argv[0] == "outreach":
        import outreach
        sys.argv = ["jobwatcher outreach", *argv[1:]]
        return outreach.main()
    if argv and argv[0] == "folder":
        from .base import BASE
        print(BASE)
        open_folder(BASE)
        return None
    return open_app(argv)


def open_folder(path):
    try:
        if sys.platform == "win32":
            os.startfile(path)  # noqa: S606
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        elif os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"):
            subprocess.Popen(["xdg-open", str(path)])
    except OSError:
        pass


def open_app(argv):
    parser = argparse.ArgumentParser(
        prog="jobwatcher", description="Open the Job Watcher app.",
        epilog="Also: jobwatcher run [options], jobwatcher outreach [options], jobwatcher folder")
    parser.add_argument("--browser", action="store_true", help="open in your web browser instead of a window")
    parser.add_argument("--dev", action="store_true", help="API on port 8765 for the interface's dev server")
    parser.add_argument("--port", type=int, help="port for the local server (default: any free port)")
    parser.add_argument("--new", action="store_true", help="open another copy even if one is open")
    parser.add_argument("--tray", action="store_true", help="start in the tray without opening the window")
    args = parser.parse_args(argv)

    try:
        import uvicorn
        from .base import BASE, CANCEL, LOG_PATH, log
        from .config import load_env
        from .server import create_app
    except ImportError as exc:
        return fail(f"The app needs a few more packages ({exc.name} is missing). In the Job Watcher folder run:\n"
                    "    pip install -r requirements-app.txt\n"
                    "or double-click \"Install Job Watcher\".")

    handlers = [logging.FileHandler(LOG_PATH, encoding="utf-8")]
    if sys.stdout:  # None when started without a console (the Start menu shortcut)
        handlers.append(logging.StreamHandler(sys.stdout))
    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s", datefmt="%Y-%m-%d %H:%M",
                        handlers=handlers)
    logging.getLogger("uvicorn.error").setLevel(logging.WARNING)

    if not (args.new or args.dev):
        open_one = running_instance()
        if open_one:
            port, token = open_one
            if args.browser:
                webbrowser.open(f"http://127.0.0.1:{port}/#token={token}")
            else:
                _call(port, token, "window/focus", "POST")
            say("Job Watcher is already open; brought it to the front.")
            return None

    load_env()
    token = secrets.token_urlsafe(24)
    app = create_app(token, dev=args.dev, schedule=not args.dev)
    port = args.port or (DEV_PORT if args.dev else free_port())
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning",
                                           access_log=False, lifespan="on"))
    threading.Thread(target=server.run, name="jobwatcher-server", daemon=True).start()
    for _ in range(200):
        if server.started:
            break
        time.sleep(0.05)
    else:
        return fail(f"The app's local server did not start on port {port}.")

    marker = instance_file()
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps({"port": port, "token": token, "pid": os.getpid()}), encoding="utf-8")
    url = f"http://127.0.0.1:{port}/#token={token}"
    runs = app.state.runs
    log.info("Job Watcher app opened (data folder: %s).", BASE)

    try:
        if args.dev:
            say(f"API ready on port {port}. In the ui folder run `npm run dev`, then open:\n"
                f"    http://localhost:5173/#token={token}\nPress Ctrl+C to stop.")
            wait_forever()
        elif args.browser:
            say(f"Opening Job Watcher in your browser. Keep this window open while you use it.\n    {url}\n"
                "Press Ctrl+C to stop.")
            webbrowser.open(url)
            wait_forever()
        else:
            open_window(url, app, runs, BASE, start_hidden=args.tray)
    finally:
        app.state.scheduler.stop.set()
        if runs.active():
            CANCEL.set()
            log.info("Closing: finishing the boards being read now, then saving. This can take a minute.")
            runs.wait()
        server.should_exit = True
        try:
            if json.loads(marker.read_text(encoding="utf-8")).get("pid") == os.getpid():
                marker.unlink()
        except (OSError, ValueError):
            pass
    return None


def say(text):
    if sys.stdout:
        print(text)


def fail(text):
    """Show a problem: in the terminal, or in a message box when there is no terminal."""
    if sys.stdout:
        print(text)
    elif sys.platform == "win32":
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, text, "Job Watcher", 0x10)
    sys.exit(1)


def wait_forever():
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass


def open_window(url, app, runs, base, start_hidden=False):
    try:
        import webview
    except ImportError:
        say("pywebview is not installed, so the app opens in your browser instead.\n"
            "For its own window: pip install pywebview")
        webbrowser.open(url)
        wait_forever()
        return

    tray = Tray(app, runs) if Tray.available() else None
    window = webview.create_window("Job Watcher", url, width=1340, height=880, min_size=(1000, 640),
                                   background_color="#F4F6FA", text_select=True,
                                   hidden=bool(start_hidden and tray))

    def bring_forward():
        try:
            window.show()
            window.restore()
            window.on_top = True  # a quick on-top flip is the dependable way to come to the front
            time.sleep(0.3)
            window.on_top = False
        except Exception:
            pass

    app.state.focus = bring_forward

    def on_closing():
        if tray and not tray.quitting:
            window.hide()  # keep running in the tray; scheduled runs carry on
            tray.hidden_once()
            return False
        if not runs.active():
            return True
        return window.create_confirmation_dialog(
            "A run is going",
            "Quit Job Watcher and stop the run? The boards being read now finish first, then "
            "everything found so far is saved.")

    window.events.closing += on_closing
    if tray:
        tray.start(bring_forward, window)
    icon = Path(__file__).resolve().parent / "assets" / "icon.png"
    try:
        # Not private: the window remembers your filters and when you last looked at new matches.
        webview.start(private_mode=False, storage_path=str(base / ".app_window"),
                      icon=str(icon) if icon.exists() else None)
    finally:
        if tray:
            tray.stop()


class Tray:
    """The icon by the clock: Job Watcher keeps running there when you close the window, so
    scheduled runs happen. Windows only for now."""

    @staticmethod
    def available():
        if sys.platform != "win32":
            return False
        try:
            import pystray  # noqa: F401
            from PIL import Image  # noqa: F401
            return True
        except ImportError:
            return False

    def __init__(self, app, runs):
        self.app, self.runs = app, runs
        self.quitting = False
        self.icon = None
        self.told = False

    def start(self, show, window):
        import pystray
        from PIL import Image
        from .config import ConfigError, load_config

        image = Image.open(Path(__file__).resolve().parent / "assets" / "icon.png")
        scheduler = self.app.state.scheduler

        def paused(_item=None):
            try:
                return scheduler.paused(load_config())
            except ConfigError:
                return False

        def toggle_pause(icon, item):
            scheduler.set_paused(load_config(), not paused())

        def run_now(icon, item):
            try:
                self.runs.start("full", source="tray")
            except Exception as exc:  # already running, or another run elsewhere
                self.notify("Job Watcher", getattr(exc, "detail", str(exc)))

        def quit_app(icon, item):
            self.quitting = True
            window.destroy()

        menu = pystray.Menu(
            pystray.MenuItem("Open Job Watcher", lambda icon, item: show(), default=True),
            pystray.MenuItem("Run now", run_now),
            pystray.MenuItem("Pause automatic runs", toggle_pause, checked=paused),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", quit_app))
        self.icon = pystray.Icon("jobwatcher", image, "Job Watcher", menu)
        threading.Thread(target=self.icon.run, name="jobwatcher-tray", daemon=True).start()
        self.app.state.tray = True
        self.runs.notify = self.notify

    def notify(self, title, message=""):
        try:
            if self.icon:
                self.icon.notify(message or title, title)
        except Exception:
            pass

    def hidden_once(self):
        if not self.told:
            self.told = True
            self.notify("Job Watcher is still running",
                        "It keeps checking on schedule. Right-click the icon by the clock to open or quit it.")

    def stop(self):
        try:
            if self.icon:
                self.icon.stop()
        except Exception:
            pass


if __name__ == "__main__":
    main()
