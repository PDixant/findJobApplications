"""Starting with Windows, and finding the Task Scheduler task the app replaces."""
from __future__ import annotations

import subprocess

from .base import *  # noqa: F401,F403

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE = "Job Watcher"


def launch_command(tray=True):
    """How to start the app without a terminal: the installed program, the shortcut's program
    from the installer's .venv, or pythonw with app.py."""
    extra = ["--tray"] if tray else []
    if getattr(sys, "frozen", False):
        return [sys.executable, *extra]
    scripts = Path(sys.executable).parent
    for name in ("jobwatcher-app.exe", "jobwatcher-app"):
        if (scripts / name).exists():
            return [str(scripts / name), *extra]
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    return [str(pythonw if pythonw.exists() else Path(sys.executable)), str(SOURCE_DIR / "app.py"), *extra]


def _quote(cmd):
    return " ".join(f'"{c}"' if " " in c else c for c in cmd)


def startup_supported():
    return sys.platform == "win32" or sys.platform.startswith("linux")


def _autostart_file():
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "autostart" / "jobwatcher.desktop"


def startup_enabled():
    if sys.platform == "win32":
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
                winreg.QueryValueEx(key, VALUE)
                return True
        except OSError:
            return False
    if sys.platform.startswith("linux"):
        return _autostart_file().exists()
    return False


def set_startup(on):
    """Open Job Watcher in the tray when you sign in."""
    if sys.platform == "win32":
        import winreg
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            if on:
                winreg.SetValueEx(key, VALUE, 0, winreg.REG_SZ, _quote(launch_command()))
            else:
                try:
                    winreg.DeleteValue(key, VALUE)
                except OSError:
                    pass
    elif sys.platform.startswith("linux"):
        path = _autostart_file()
        if on:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"[Desktop Entry]\nType=Application\nName=Job Watcher\nExec={_quote(launch_command())}\n")
        else:
            path.unlink(missing_ok=True)
    else:
        raise RuntimeError("Starting with your computer is not available here yet.")
    return startup_enabled()


def _powershell(script):
    return subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
                          capture_output=True, text=True, timeout=60,
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def old_tasks():
    """Windows scheduled tasks that run job_watcher.py: the app's own schedule replaces them."""
    if sys.platform != "win32":
        return []
    folder = str(BASE).replace("'", "''")
    r = _powershell(
        "Get-ScheduledTask | Where-Object { $t = $_; ($t.Actions | Where-Object { "
        "(($_.Execute + ' ' + $_.Arguments) -match 'job_watcher|jobwatcher') -or ($_.WorkingDirectory -eq '"
        + folder + "') }) } | Select-Object TaskName, TaskPath, @{n='State';e={[string]$_.State}} | ConvertTo-Json")
    if r.returncode != 0 or not r.stdout.strip():
        return []
    data = json.loads(r.stdout)
    return [{"name": t["TaskName"], "path": t["TaskPath"], "state": t["State"]}
            for t in (data if isinstance(data, list) else [data])]


def disable_task(name, path="\\"):
    if sys.platform != "win32":
        raise RuntimeError("Only Windows has scheduled tasks to turn off.")
    q = lambda s: "'" + str(s).replace("'", "''") + "'"  # noqa: E731
    r = _powershell(f"Disable-ScheduledTask -TaskName {q(name)} -TaskPath {q(path)} | Out-Null")
    if r.returncode != 0:
        raise RuntimeError((r.stderr or "Windows refused.").strip().splitlines()[0])


__all__ = ["launch_command", "startup_supported", "startup_enabled", "set_startup", "old_tasks", "disable_task"]
