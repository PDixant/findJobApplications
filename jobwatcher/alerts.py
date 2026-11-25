"""Phone alerts for new matches through ntfy (ntfy.sh, free, no account).

Install the ntfy app on your phone, subscribe to a topic name only you know, and save the same name
under Settings, Keys (NTFY_TOPIC). Anyone who knows the topic name can read it, so it is
kept with your keys, never in config.toml, and the app suggests a long random one. Alerts carry the
company, title and link of new matches, nothing else.
"""
from __future__ import annotations

import secrets

from .base import *  # noqa: F401,F403


def suggest_topic():
    return "jobwatcher-" + secrets.token_urlsafe(12).lower().replace("_", "").replace("-", "")


def send(cfg, title, message, url=None):
    a = cfg.get("alerts") or {}
    topic = (os.environ.get("NTFY_TOPIC") or a.get("ntfy_topic") or "").strip()
    if not topic:
        return False
    server = (a.get("ntfy_server") or "https://ntfy.sh").rstrip("/")
    headers = {"Title": title.encode("ascii", "replace").decode(), "Tags": "briefcase"}
    if url:
        headers["Click"] = url
    try:
        r = requests.post(f"{server}/{topic}", data=message.encode("utf-8"), headers=headers, timeout=10)
        return r.status_code < 300
    except requests.RequestException as exc:
        log.info("Phone alert not sent (%s).", exc.__class__.__name__)
        return False


def new_matches(cfg, matches):
    """matches: the run's new matches, as job dicts or 'Company: Title (Region)' lines."""
    if not matches:
        return False
    lines = [f"{m['company']}: {m['title']} ({m.get('region', '')})" if isinstance(m, dict) else str(m) for m in matches[:8]]
    more = f"\n...and {len(matches) - 8} more" if len(matches) > 8 else ""
    first_url = matches[0].get("url") if isinstance(matches[0], dict) else None
    return send(cfg, f"{len(matches)} new job match{'' if len(matches) == 1 else 'es'}", "\n".join(lines) + more, first_url)


__all__ = ["send", "new_matches", "suggest_topic"]
