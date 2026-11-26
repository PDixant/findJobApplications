"""Report a problem or suggest a feature, on GitHub.

Pathfinder never sends a report itself, and holds no GitHub key (a key shipped inside an app anyone can
download would let anyone post as you). Instead it fills in GitHub's own form and opens it in your
browser: you sign in, add screenshots, read it over and press Submit. This module gathers the details
that help fix a problem (version, system, the last lines of the log), with keys and your home folder
taken out, so you can see exactly what would be shared before anything leaves your computer."""
from __future__ import annotations

import platform
import sys
from pathlib import Path

from .base import LOG_PATH, redact
from . import __version__, updates

LOG_LINES = 40  # the end of job_watcher.log offered with a problem report


def _private(text):
    """Keys, passwords and your home folder's path (which holds your user name) taken out."""
    text = redact(text)
    home = str(Path.home())
    for form in {home, home.replace("\\", "/"), home.replace("\\", "\\\\")}:
        if len(form) > 3:
            text = text.replace(form, "~")
    return text


def app_details():
    """Version, how Pathfinder was installed, and the system it runs on."""
    how = updates.kind()
    commit = ""
    if how == "git":
        try:
            r = updates.git("rev-parse", "--short", "HEAD")
            commit = (r.stdout or "").strip() if r.returncode == 0 else ""
        except Exception:  # noqa: BLE001 - a missing commit only shortens the report
            commit = ""
    system = f"{platform.system()} {platform.release()}".strip()
    lines = [
        f"Pathfinder {__version__}" + (f" ({commit})" if commit else ""),
        f"Installed as: {how}",
        f"System: {system} ({platform.machine()})",
        f"Python: {sys.version.split()[0]}",
    ]
    return _private("\n".join(lines))


def recent_log(lines=LOG_LINES):
    try:
        text = LOG_PATH.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return _private("\n".join(text.splitlines()[-lines:]))


def feedback_info():
    return {"repo": updates.REPO, "details": app_details(), "log": recent_log()}


__all__ = ["feedback_info", "app_details", "recent_log"]
