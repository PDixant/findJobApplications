"""Tells you when a newer Job Watcher is published on GitHub (checked at most twice a day)."""
from __future__ import annotations

from .base import *  # noqa: F401,F403

REPO = os.environ.get("JOBWATCHER_UPDATE_REPO", "PDixant/findJobApplications")


def _version(text):
    return tuple(int(x) for x in re.findall(r"\d+", str(text))[:3])


def check(current, force=False):
    cache = BASE / ".app_window" / "update.json"
    data = load_json(cache, {})
    fresh = data.get("checked") and dt.datetime.now(dt.timezone.utc) - parse_date(data["checked"]) < dt.timedelta(hours=12)
    if force or not fresh:
        latest = url = None
        try:
            r = requests.get(f"https://api.github.com/repos/{REPO}/releases/latest", timeout=6,
                             headers={"Accept": "application/vnd.github+json"})
            if r.status_code == 200:
                body = r.json()
                latest, url = body.get("tag_name"), body.get("html_url")
        except (requests.RequestException, ValueError):
            pass
        data = {"checked": dt.datetime.now(dt.timezone.utc).isoformat(), "latest": latest, "url": url}
        cache.parent.mkdir(parents=True, exist_ok=True)
        save_json(cache, data)
    latest = data.get("latest")
    return {"current": current, "latest": latest, "url": data.get("url"),
            "newer": bool(latest) and _version(latest) > _version(current),
            "installed": bool(getattr(sys, "frozen", False))}


__all__ = ["check", "REPO"]
