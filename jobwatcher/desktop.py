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


# The .exe the Desktop and Start menu shortcuts open has no terminal: stdout and stderr are None.
# On Windows that start hands over to a copy with a hidden console (see hand_over_to_hidden_console),
# which has streams but nobody watching them, so it counts as having no terminal too.
RELAUNCHED = bool(os.environ.get("JOBWATCHER_RELAUNCHED"))
CONSOLE = sys.stdout is not None and not RELAUNCHED
CREATE_NO_WINDOW = 0x08000000
CREATE_NEW_PROCESS_GROUP = 0x00000200


def hand_over_to_hidden_console(argv):
    """Windows, started from the Desktop or Start menu (jobwatcher-app.exe, no terminal): start the
    terminal version (jobwatcher.exe) instead, with its console window hidden, and step aside. Typed
    in a terminal the app opens fine, but without one its window could close before it appeared;
    this way a click runs exactly what the terminal runs. Its errors go to job_watcher_errors.log.
    Returns True when the other copy was started."""
    if sys.platform != "win32" or sys.stdout is not None or RELAUNCHED:
        return False
    scripts = Path(sys.executable).parent
    console = scripts / "jobwatcher.exe"
    if console.exists():
        cmd = [str(console), *argv]
    else:
        python = scripts / "python.exe"
        if not python.exists():
            return False
        cmd = [str(python), str(Path(__file__).resolve().parent.parent / "app.py"), *argv]
    from .base import BASE
    errors = errors_file(redirect=False)
    env = {**os.environ, "JOBWATCHER_RELAUNCHED": "1"}
    try:
        child = subprocess.Popen(cmd, cwd=str(BASE), env=env, stdin=subprocess.DEVNULL,
                                 stdout=subprocess.DEVNULL, stderr=errors,
                                 creationflags=CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP)
    except OSError as exc:
        errors.write(f"Could not start the app with a hidden console ({exc}); opening it directly.\n")
        return False
    errors.write(f"Handed over to the app with a hidden console (process {child.pid}).\n")
    return True


def no_console_streams():
    """Without a terminal, libraries that write to stdout or ask whether it is a terminal crash
    before the app can show anything (uvicorn's logging setup calls sys.stdout.isatty()), so the
    shortcut seemed to do nothing. Point stdout at nowhere and stderr at job_watcher_errors.log."""
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115 (open for the app's lifetime)
    if sys.stderr is None:
        sys.stderr = errors_file()


def errors_file(redirect=True):
    """job_watcher_errors.log in the data folder: what a terminal would have shown. On Windows the
    process's own error handle points there too (unless redirect is False), so a crash of the window
    itself (the .NET runtime prints it to stderr, then the app closes) is written down, not lost."""
    try:
        from .base import BASE
        path = BASE / "job_watcher_errors.log"
        if path.exists() and path.stat().st_size > 1_000_000:
            path.unlink()  # keep it small: only recent starts matter
        stream = open(path, "a", encoding="utf-8", buffering=1)  # noqa: SIM115 (open for the app's lifetime)
        stream.write(f"\n{time.strftime('%Y-%m-%d %H:%M:%S')}  Opened without a terminal (process {os.getpid()}).\n")
        stream.flush()
        if redirect and sys.platform == "win32":
            import ctypes
            import msvcrt
            os.dup2(stream.fileno(), 2)
            ctypes.windll.kernel32.SetStdHandle(-12, msvcrt.get_osfhandle(2))  # STD_ERROR_HANDLE
        return stream
    except Exception:
        return open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115


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
    if hand_over_to_hidden_console(argv):
        return None
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
    no_console_streams()
    try:
        return open_app(argv)
    except Exception as exc:  # without a terminal, an error here would otherwise close it without a word
        logging.getLogger("job_watcher").exception("Job Watcher could not start.")
        return fail(f"Job Watcher could not start: {exc}\n\n"
                    "The details are at the end of job_watcher.log in the Job Watcher folder.")


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
        from .base import BASE, CANCEL, LOG_PATH, guard_logs, log
        from .config import load_env
        from .server import create_app
    except ImportError as exc:
        return fail(f"The app needs a few more packages ({exc.name} is missing). In the Job Watcher folder run:\n"
                    "    pip install -r requirements-app.txt\n"
                    "or double-click \"Install Job Watcher\".")

    handlers = [logging.FileHandler(LOG_PATH, encoding="utf-8")]
    if CONSOLE:  # no terminal behind the Desktop and Start menu shortcuts
        handlers.append(logging.StreamHandler(sys.stdout))
    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s", datefmt="%Y-%m-%d %H:%M",
                        handlers=handlers)
    logging.getLogger("uvicorn.error").setLevel(logging.WARNING)
    guard_logs()

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

    if args.tray and not keep_in_tray():
        return None  # started at sign-in, but you'd rather it only runs while you have it open
    load_env()
    token = secrets.token_urlsafe(24)
    app = create_app(token, dev=args.dev, schedule=not args.dev)
    port = args.port or (DEV_PORT if args.dev else free_port())
    server = uvicorn.Server(server_config(uvicorn, app, port))
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


def server_config(uvicorn, app, port):
    """log_config=None: uvicorn's own console logging setup is skipped (it fails with no terminal),
    and its warnings go to job_watcher.log through ours."""
    return uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", access_log=False,
                          lifespan="on", log_config=None)


def say(text):
    if CONSOLE:
        print(text)


def fail(text):
    """Show a problem: in the terminal, or in a message box when there is no terminal."""
    if CONSOLE:
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

    tray = Tray(app, runs) if Tray.available() and (start_hidden or keep_in_tray()) else None
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
        if tray and not tray.quitting and keep_in_tray():
            window.hide()  # you asked for it to keep running in the tray; scheduled runs carry on
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
    icon = window_icon()
    try:
        # Not private: the window remembers your filters and when you last looked at new matches.
        webview.start(private_mode=False, storage_path=str(base / ".app_window"),
                      icon=str(icon) if icon.exists() else None)
    finally:
        if tray:
            tray.stop()


def window_icon(platform: str = sys.platform) -> Path:
    """The window's icon. Windows needs the .ico: pywebview hands the file to .NET's Icon class,
    which throws "Argument 'picture' must be a picture that can be used as a Icon" for a .png and
    closes the app. The .ico keeps its small sizes as plain bitmaps for the same reason."""
    assets = Path(__file__).resolve().parent / "assets"
    return assets / ("icon.ico" if platform == "win32" else "icon.png")


def keep_in_tray():
    """Settings, Automatic runs, 'keep running after closing'. Off by default: closing the window
    quits Job Watcher."""
    from .config import ConfigError, load_config
    try:
        return bool(load_config()["schedule"].get("keep_in_tray"))
    except ConfigError:
        return False


class Tray:
    """The icon by the clock, only when you turn on 'keep running after closing': Job Watcher then
    keeps running there when you close the window, so scheduled runs happen. Windows only for now."""

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
