"""API keys and the Gmail app password.

Looked up in this order: real environment variables, then the operating system's password
store (Windows Credential Manager, the macOS Keychain, or on Linux the desktop keyring through
Secret Service, all through the keyring package), then the .env file. The desktop app saves keys to
the password store and takes them out of .env.

A computer with no password store (or only one that keeps secrets in a plain file) gets keys in
.env only after you agree to that, and the file is made readable by your account alone.
"""
from __future__ import annotations

import imaplib

from .base import *  # noqa: F401,F403

SERVICE = "Job Watcher"  # the app's first name: kept, so keys saved before the rename are still found
KEYS = {
    "SERPER_API_KEY": "Serper (web search)",
    "BRAVE_API_KEY": "Brave Search",
    "SERPAPI_API_KEY": "SerpApi",
    "HUNTER_API_KEY": "Hunter (finds recruiters)",
    "ANTHROPIC_API_KEY": "Anthropic (Claude: fit scores, resumes, email drafts)",
    "GMAIL_ADDRESS": "Gmail address",
    "GMAIL_APP_PASSWORD": "Gmail app password",
    "NTFY_TOPIC": "ntfy topic (phone alerts)",
}
SOURCES = {}  # name -> "environment", "password store" or ".env", filled by load_keys


class NoPasswordStore(ValueError):
    """There's no password store, and you haven't agreed to a plain file yet."""


def _secure(backend):
    """A real password store, not a stand-in that refuses everything or keeps secrets in plain text."""
    kind = f"{type(backend).__module__}.{type(backend).__name__}".lower()
    return not any(bad in kind for bad in ("fail", "null", "plaintext", "keyrings.alt", "uncrypted", "chainer"))


def _keyring():
    try:
        import keyring
        backend = keyring.get_keyring()
        if not _secure(backend):
            from keyring.backends import chainer  # several stores at once: fine if any is a real one
            if not (isinstance(backend, chainer.ChainerBackend) and any(_secure(b) for b in backend.backends)):
                return None
        return keyring
    except Exception:  # not installed, or no password store on this computer
        return None


def store_available():
    return _keyring() is not None


def store_name():
    """What the password store is called on this computer, for the Keys screen."""
    if not store_available():
        return None
    return {"win32": "Windows Credential Manager", "darwin": "the macOS Keychain"}.get(
        sys.platform, "your desktop keyring (Secret Service: GNOME Keyring or KDE Wallet)")


def load_keys(env_values):
    """Put each key into os.environ. env_values = what .env holds."""
    store = _keyring()
    for name in KEYS:
        if name in os.environ and SOURCES.get(name) is None:
            SOURCES[name] = "environment"
            continue
        if name in os.environ:
            continue
        value = None
        if store:
            try:
                value = store.get_password(SERVICE, name)
            except Exception:
                value = None
            if value:
                SOURCES[name] = "password store"
        if not value and env_values.get(name):
            value = env_values[name]
            SOURCES[name] = ".env"
        if value:
            os.environ[name] = value
    for name, value in env_values.items():  # anything else in .env
        if name not in KEYS and name not in os.environ:
            os.environ[name] = value


def status():
    return [{"name": n, "label": label, "set": bool(os.environ.get(n)), "source": SOURCES.get(n) if os.environ.get(n) else None,
             "preview": _mask(os.environ.get(n, ""), secret=n != "GMAIL_ADDRESS")} for n, label in KEYS.items()]


def _mask(value, secret=True):
    if not value:
        return ""
    if not secret:
        return value
    return ("•" * 6) + value[-4:] if len(value) > 8 else "•" * len(value)


def save_key(name, value, env_path=None, plain_file_ok=False):
    """Save to the password store (and take it out of .env). With no password store, save to .env
    only if plain_file_ok (you agreed), else raise NoPasswordStore. Returns where it went."""
    if name not in KEYS:
        raise ValueError(f"Unknown key {name}")
    value = value.strip()
    if not value:
        raise ValueError("The key is empty")
    if any(c in value for c in "\r\n\0"):
        raise ValueError("A key is one line of text")
    env_path = env_path or ENV_PATH
    store = _keyring()
    if store:
        store.set_password(SERVICE, name, value)
        _remove_from_env(env_path, name)
        where = "password store"
    elif not plain_file_ok:
        raise NoPasswordStore("This computer has no password store, so the key would be saved in .env, a plain "
                              "text file only your account can read. Save it under Settings, Keys, and agree to that.")
    else:
        _write_env(env_path, name, value)
        where = ".env"
    os.environ[name] = value
    SOURCES[name] = where
    return where


def delete_key(name, env_path=None):
    if name not in KEYS:
        raise ValueError(f"Unknown key {name}")
    store = _keyring()
    if store:
        try:
            store.delete_password(SERVICE, name)
        except Exception:
            pass
    _remove_from_env(env_path or ENV_PATH, name)
    if SOURCES.get(name) != "environment":
        os.environ.pop(name, None)
        SOURCES.pop(name, None)


def _env_lines(path):
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return []
    text = raw.decode("utf-16") if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else raw.decode("utf-8-sig", "replace")
    return text.splitlines()


def _is_line_for(line, name):
    key = line.split("=", 1)[0].strip().removeprefix("export ").strip() if "=" in line else ""
    return key == name


def _remove_from_env(path, name):
    lines = _env_lines(path)
    kept = [l for l in lines if not _is_line_for(l, name)]
    if len(kept) != len(lines):
        path.write_text("\n".join(kept) + "\n", encoding="utf-8")


def _write_env(path, name, value):
    lines = [l for l in _env_lines(path) if not _is_line_for(l, name)]
    lines.append(f"{name}={value}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    private_file(path)


def private_file(path):
    """Readable and writable by your account only (Mac and Linux; on Windows your user folder is already yours)."""
    if os.name == "posix":
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass


def test_key(name, cfg=None):
    """Check a key with the service. Returns (ok, message). Uses no search credits where the
    service allows it."""
    value = os.environ.get(name, "")
    if not value:
        return False, "Not set."
    try:
        if name == "HUNTER_API_KEY":
            r = requests.get("https://api.hunter.io/v2/account", params={"api_key": value}, timeout=15)
            if r.status_code != 200:
                return False, f"Hunter refused the key ({r.status_code})."
            data = r.json().get("data") or {}
            searches = (data.get("requests") or {}).get("searches") or {}
            return True, (f"Works. {searches.get('used', 0)} of {searches.get('available', '?')} searches used this "
                          f"month{', resets ' + data['reset_date'] if data.get('reset_date') else ''}.")
        if name == "NTFY_TOPIC":
            from .alerts import send
            ok = send({"alerts": {**((cfg or {}).get("alerts") or {}), "ntfy_topic": value}},
                      "Pathfinder test", "Phone alerts work. New matches will show up here.")
            return (True, "Sent a test alert. Check your phone.") if ok else (False, "ntfy didn't accept the alert.")
        if name == "ANTHROPIC_API_KEY":
            r = requests.get("https://api.anthropic.com/v1/models", timeout=15,
                             headers={"x-api-key": value, "anthropic-version": "2023-06-01"})
            if r.status_code != 200:
                return False, f"Anthropic refused the key ({r.status_code})."
            return True, "Works. The check is free."
        if name == "SERPAPI_API_KEY":
            r = requests.get("https://serpapi.com/account", params={"api_key": value}, timeout=15)
            if r.status_code != 200:
                return False, f"SerpApi refused the key ({r.status_code})."
            return True, f"Works. {r.json().get('total_searches_left', '?')} searches left."
        if name == "SERPER_API_KEY":
            r = requests.post("https://google.serper.dev/search", json={"q": "software engineer jobs", "num": 1},
                              headers={"X-API-KEY": value}, timeout=15)
            if r.status_code != 200:
                return False, f"Serper refused the key ({r.status_code})."
            return True, "Works. The check used one search."
        if name == "BRAVE_API_KEY":
            r = requests.get("https://api.search.brave.com/res/v1/web/search", params={"q": "jobs", "count": 1},
                             headers={"X-Subscription-Token": value, "Accept": "application/json"}, timeout=15)
            if r.status_code != 200:
                return False, f"Brave refused the key ({r.status_code})."
            return True, "Works. The check used one search."
        if name in ("GMAIL_APP_PASSWORD", "GMAIL_ADDRESS"):
            address, password = os.environ.get("GMAIL_ADDRESS", ""), os.environ.get("GMAIL_APP_PASSWORD", "")
            if not (address and password):
                return False, "Set both the Gmail address and the app password."
            server = ((cfg or {}).get("outreach") or {}).get("imap_server", "imap.gmail.com")
            box = imaplib.IMAP4_SSL(server, timeout=15)
            try:
                box.login(address, password.replace(" ", ""))
            finally:
                try:
                    box.logout()
                except Exception:
                    pass
            return True, f"Signed in to {server}."
    except imaplib.IMAP4.error:
        return False, "Gmail refused the address and app password."
    except requests.RequestException as exc:
        return False, f"Could not reach the service ({exc.__class__.__name__})."
    except OSError as exc:
        return False, f"Could not connect ({exc})."
    return False, "No check for this key."


__all__ = [n for n in list(globals()) if not n.startswith("__")]
