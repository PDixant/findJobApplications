"""Shared imports, paths, constants and small helpers used by every part of Job Watcher."""
from __future__ import annotations


import argparse
import csv
import datetime as dt
import gzip
import hashlib
import html
import json
import logging
import os
import re
import shutil
import sqlite3
import sys
import threading
import time
import zlib
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import partial
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urljoin, urlparse
from urllib.robotparser import RobotFileParser

try:
    import requests
    from openpyxl import Workbook, load_workbook
    from openpyxl.formatting.rule import FormulaRule
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.worksheet.datavalidation import DataValidation
except ImportError as exc:  # pragma: no cover
    sys.exit(f"Missing dependency: {exc.name}\nRun: pip install requests openpyxl")

try:
    import tomllib  # Python 3.11+
except ImportError:  # pragma: no cover
    try:
        import tomli as tomllib
    except ImportError:
        tomllib = None

# The folder that holds config.toml, companies.csv and the data files:
#   JOBWATCHER_HOME if set; otherwise the source folder when running from it (as always);
#   otherwise, for the installed app, the user's app-data folder, set up with the defaults.
SOURCE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_FILES = ("config.toml", "companies.csv", "about_me.example.md", ".env.example")


def user_data_dir():
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming") / "JobWatcher"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Job Watcher"
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "jobwatcher"


def find_home(env=None, source=SOURCE_DIR, frozen=None):
    env = os.environ.get("JOBWATCHER_HOME") if env is None else env
    frozen = getattr(sys, "frozen", False) if frozen is None else frozen
    if env:
        return Path(env)
    if not frozen and (source / "job_watcher.py").exists():
        return source
    return user_data_dir()


def defaults_dir():
    """Where the starting config.toml and companies list come from in an installed copy."""
    bundled = getattr(sys, "_MEIPASS", None)
    return Path(bundled) / "defaults" if bundled else SOURCE_DIR


def set_up_home(home):
    """Create the data folder and copy in the default settings and company list if missing."""
    home.mkdir(parents=True, exist_ok=True)
    source = defaults_dir()
    if source.resolve() == home.resolve():
        return
    for name in DEFAULT_FILES:
        if not (home / name).exists() and (source / name).exists():
            shutil.copy2(source / name, home / name)


BASE = find_home()
if BASE != SOURCE_DIR and not os.environ.get("JOBWATCHER_HOME"):
    set_up_home(BASE)  # the installed app: first launch gets the defaults
CONFIG_PATH = BASE / "config.toml"
LEGACY_CONFIG_PATH = BASE / "config.json"
ENV_PATH = BASE / ".env"
STATE_PATH = BASE / "job_watcher_state.json"
LOG_PATH = BASE / "job_watcher.log"

STATUSES = ["New", "Applied", "Interviewing", "Offer", "Rejected", "Skipped"]
# Outreach column: you pick Email (and later Sent / Replied); outreach.py sets the others.
OUTREACH = ["Email", "Prepared", "No contact", "Sent", "Follow-up", "Replied"]
OUTREACH_COLORS = {"Email": "FFF2CC", "Prepared": "DDEBF7", "No contact": "EDEDED", "Sent": "E4DFEC",
                   "Follow-up": "FCE4D6", "Replied": "C6EFCE"}
STATUS_COLORS = {
    "New": ("FFF2CC", "7F6000"),
    "Applied": ("DDEBF7", "1F4E79"),
    "Interviewing": ("E4DFEC", "5B2C83"),
    "Offer": ("C6EFCE", "006100"),
    "Rejected": ("F8CBAD", "843C0C"),
    "Skipped": ("D9D9D9", "404040"),
}
LEVEL_COLORS = {"Senior/Lead": "FFE699", "Mid": "E2EFDA", "Unlabelled": None}
TIER_COLORS = {"A": "C6EFCE", "B": "FFF2CC", "C": "F8CBAD"}
LEVER_HOSTS = ["https://api.lever.co", "https://api.eu.lever.co", "https://api.ca.lever.co"]
WORKDAY_SHARDS = ["wd1", "wd3", "wd5", "wd12", "wd10", "wd103", "wd108"]
COUNTRY_NAMES = {"CA": "Canada", "US": "United States", "IN": "India", "GB": "United Kingdom"}
MANUAL_BOARDS = {"other", "manual", "own"}
USER_AGENT = "job-watcher/2.0 (personal job search)"

# Filled from config at the start of each run
SETTINGS = {"search_terms": ["software engineer", "software developer", "frontend", "full stack",
                             "web developer"],
            "max_pages": 10, "window_days": 1.0, "unsorted_pages": 25, "hard_max_pages": 100,
            "backoff": 30,
            # Minimum seconds between requests to the same site, shared by all worker threads.
            "host_intervals": {"workable.com": 2.0, "smartrecruiters.com": 0.5, "myworkdayjobs.com": 0.5,
                               "lever.co": 0.3, "ashbyhq.com": 0.3, "greenhouse.io": 0.2,
                               "api.search.brave.com": 1.1, "default": 0.3},
            # Companies' own careers websites get more room than the job-board APIs.
            "website_interval": 1.0,
            "career_sites": True, "site_pages_per_run": 15, "rules": None,
            # How often to re-guess board names for a company with no board found (None = always).
            "guess_every": None}

log = logging.getLogger("job_watcher")
_local = threading.local()


def session():
    """One HTTP session per worker thread."""
    if not hasattr(_local, "s"):
        s = requests.Session()
        s.headers.update({"User-Agent": USER_AGENT,
                          "Accept": "application/json", "Accept-Language": "en-US"})
        _local.s = s
    return _local.s



# ----------------------------------------------------------------- helpers
def compile_terms(terms):
    """Whole-word, case-insensitive matchers. Short all-caps terms (BC, ON, CA) are case-sensitive."""
    out = []
    for term in terms:
        term = term.strip()
        if not term:
            continue
        flags = 0 if (term.isupper() and len(term) <= 3) else re.IGNORECASE
        pattern = r"(?<![A-Za-z0-9])" + re.escape(term) + r"(?![A-Za-z0-9])"
        out.append((term, re.compile(pattern, flags)))
    return out


def any_match(matchers, text):
    return any(p.search(text) for _, p in matchers)


def all_matches(matchers, text):
    return [t for t, p in matchers if p.search(text)]


def parse_date(value):
    if value in (None, ""):
        return None
    if isinstance(value, dt.datetime):
        return value if value.tzinfo else value.replace(tzinfo=dt.timezone.utc)
    if isinstance(value, (int, float)):
        return dt.datetime.fromtimestamp(value / 1000 if value > 1e11 else value, tz=dt.timezone.utc)
    s = str(value).strip().replace("Z", "+00:00")
    for candidate in (s, re.sub(r"\.\d+", "", s)):
        try:
            d = dt.datetime.fromisoformat(candidate)
            return d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)
        except ValueError:
            continue
    return None


def parse_relative(text):
    """Workday's 'Posted Today', 'Posted Yesterday', 'Posted 3 Days Ago', 'Posted 30+ Days Ago'.
    Workday only reports whole days, so 'Yesterday' is placed just inside a 24-hour window,
    and an open-ended '30+ Days' is treated as older than 30 days."""
    if not text:
        return None
    now = dt.datetime.now(dt.timezone.utc)
    t = text.lower()
    if "today" in t or "just" in t:
        return now
    m = re.search(r"(\d+)\+?\s*hour", t)
    if m:
        return now - dt.timedelta(hours=int(m.group(1)))
    if "yesterday" in t:
        return now - dt.timedelta(hours=23)
    m = re.search(r"(\d+)(\+?)\s*day", t)
    if m:
        return now - dt.timedelta(days=int(m.group(1)) + (1 if m.group(2) else 0))
    return None


def wd_age_days(text):
    """Whole days since posting from Workday's label. '30+ Days Ago' counts as 31."""
    if not text:
        return None
    t = text.lower()
    if "today" in t or "just" in t or "hour" in t:
        return 0
    if "yesterday" in t:
        return 1
    m = re.search(r"(\d+)(\+?)\s*day", t)
    return int(m.group(1)) + (1 if m.group(2) else 0) if m else None


TAG_RE = re.compile(r"<[^>]+>")


def html_to_text(s):
    if not s:
        return ""
    s = html.unescape(s)  # Greenhouse content arrives HTML-escaped
    return html.unescape(TAG_RE.sub(" ", s))


class RateLimited(Exception):
    """A site asked us to slow down. The board is skipped quietly and retried next run."""


class HostLimiter:
    """Spaces out requests to each site across all worker threads, and pauses a site
    entirely when it answers 429 Too Many Requests."""
    SITES = ("myworkdayjobs.com", "lever.co", "greenhouse.io", "ashbyhq.com",
             "smartrecruiters.com", "workable.com")

    def __init__(self):
        self.lock = threading.Lock()
        self.next_slot = {}
        self.counts = Counter()
        self.blocked = set()  # sites that kept refusing this run; left alone until the next run
        self.strikes = Counter()

    def reset(self):
        with self.lock:
            self.counts.clear()
            self.blocked.clear()
            self.strikes.clear()

    def refused(self, site):
        """Three refusals from a site, from any board, and it is left alone for this run."""
        with self.lock:
            self.strikes[site] += 1
            if self.strikes[site] >= 3:
                self.blocked.add(site)

    def accepted(self, site):
        with self.lock:
            self.strikes[site] = 0

    @classmethod
    def site(cls, url):
        host = urlparse(url).hostname or ""
        return next((s for s in cls.SITES if host == s or host.endswith("." + s)), host)

    def wait(self, site, default_gap=None):
        intervals = SETTINGS["host_intervals"]
        gap = intervals.get(site, intervals.get("default", 0.3) if default_gap is None else default_gap)
        with self.lock:
            now = time.monotonic()
            slot = max(now, self.next_slot.get(site, 0.0))
            self.next_slot[site] = slot + gap
            self.counts[site] += 1
        if slot > now:
            time.sleep(slot - now)

    def cool_down(self, site, seconds):
        with self.lock:
            self.next_slot[site] = max(self.next_slot.get(site, 0.0), time.monotonic() + seconds)
        emit("rate_limited", site=site, seconds=round(seconds))


LIMITER = HostLimiter()


def retry_after(response):
    value = response.headers.get("Retry-After", "")
    try:
        seconds = float(value)
    except ValueError:
        seconds = SETTINGS["backoff"]
    return max(5.0, min(seconds, 300.0))


def _request(method, url, params=None, body=None, retries=3):
    """JSON request. Returns None on 4xx. On 429 waits as asked, then gives up with RateLimited."""
    site = HostLimiter.site(url)
    for attempt in range(retries):
        last = attempt == retries - 1
        if site in LIMITER.blocked:
            raise RateLimited(f"{site} asked to slow down")  # no request sent
        LIMITER.wait(site)
        try:
            if method == "POST":
                r = session().post(url, json=body, timeout=25)
            else:
                r = session().get(url, params=params, timeout=25)
        except requests.RequestException:
            if last:
                raise
            time.sleep(2)
            continue
        if r.status_code == 429:
            LIMITER.cool_down(site, retry_after(r))  # every thread holds off this site
            LIMITER.refused(site)
            if last:
                raise RateLimited(f"{site} asked to slow down")
            continue
        LIMITER.accepted(site)
        if r.status_code >= 500:
            if last:
                r.raise_for_status()
            time.sleep(5)
            continue
        if r.status_code >= 400:
            return None
        if "json" not in r.headers.get("Content-Type", ""):
            if last:
                return None
            time.sleep(2)
            continue
        return r.json()
    return None


def get_json(url, params=None):
    return _request("GET", url, params=params)


def post_json(url, body, retries=3):
    return _request("POST", url, body=body, retries=retries)


def result(slug, jobs, host=None, complete=True, note=""):
    return {"slug": slug, "jobs": jobs, "host": host, "complete": complete, "note": note}


def scan_recent(fetch_page, age_days, key_of, page_size, tolerance=0.05, max_pages=None):
    """Page through a list that should be newest first, stopping once postings are older than
    the look-back window. If the list turns out not to be in date order, keep reading up to
    unsorted_pages so fresh postings further down are not missed.

    fetch_page(offset) -> (batch, total) or None; age_days(item) -> days old or None.
    Returns (items, complete, ordered, pages_read) or None if the first page fails."""
    window = SETTINGS["window_days"]
    limit = max_pages or SETTINGS["hard_max_pages"]
    items, offset, total, oldest_so_far, ordered = {}, 0, 0, -1.0, True
    for page in range(limit):
        got = fetch_page(offset)
        if got is None:
            return None if page == 0 else (items, False, ordered, page)
        batch, page_total = got
        if page == 0:
            total = page_total or 0
        ages = []
        for item in batch:
            k = key_of(item)
            if k:
                items.setdefault(k, item)
            a = age_days(item)
            if a is not None:
                if a < oldest_so_far - tolerance:  # clearly newer after older: not date-sorted
                    ordered = False
                oldest_so_far = max(oldest_so_far, a)
                ages.append(a)
        offset += page_size
        if not batch or (total and offset >= total):
            return items, True, ordered, page + 1
        if ordered and ages and ages[-1] > window:
            return items, False, ordered, page + 1  # everything after this is older still
        if not ordered and page + 1 >= SETTINGS["unsorted_pages"]:
            return items, False, ordered, page + 1
    return items, False, ordered, limit


def scan_note(pages, ordered, complete):
    if complete:
        return f"Read the full list ({pages} page{'s' if pages != 1 else ''})."
    if ordered:
        return f"Newest-first list: read {pages} page{'s' if pages != 1 else ''}, stopped at older postings."
    return (f"List is not in date order: read {pages} pages plus keyword searches. "
            "Very new postings further down could be missed.")



# ----------------------------------------------------------------- state
def load_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default
    except json.JSONDecodeError:
        log.warning("%s is corrupted; starting fresh", path.name)
        return default


def replace_file(tmp, path, wait=3.0):
    """os.replace, retried for a few seconds. On Windows a file cannot be replaced while another
    program is reading it (the desktop app, a backup or sync tool), so a short wait usually wins."""
    deadline = time.monotonic() + wait
    while True:
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.2)


def save_json(path, data):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    replace_file(tmp, path, wait=15.0)  # losing the state file would lose a whole run


def to_local_naive(iso):
    d = parse_date(iso)
    return d.astimezone().replace(tzinfo=None) if d else None



def norm_name(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())





# ----------------------------------------------------------------- progress, cancel, run lock
# A run reports what it is doing as events. The command line logs them; the desktop app
# streams them to its window. Listeners are called on the thread that emits.
_LISTENERS = []
CANCEL = threading.Event()  # set it to stop a run after the boards being read now


def on_event(listener):
    """Register listener(kind, data). Returns it, so it can be used as a decorator."""
    _LISTENERS.append(listener)
    return listener


def off_event(listener):
    if listener in _LISTENERS:
        _LISTENERS.remove(listener)


def emit(kind, **data):
    for listener in list(_LISTENERS):
        try:
            listener(kind, data)
        except Exception:  # a broken listener must never stop a run
            log.exception("Progress listener failed")


class RunLock:
    """Only one run (or one change to companies.csv) at a time, across processes: the scheduled
    task, a terminal and the desktop app all share it. The operating system releases the lock
    if the process holding it dies, so a crash never leaves it stuck."""

    def __init__(self, path=None):
        self.path = Path(path or BASE / "job_watcher.lock")
        self.handle = None

    def acquire(self):
        handle = open(self.path, "a+")
        try:
            if os.name == "nt":
                import msvcrt
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            return False
        self.handle = handle
        return True

    def release(self):
        if not self.handle:
            return
        try:
            if os.name == "nt":
                import msvcrt
                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle, fcntl.LOCK_UN)
        except OSError:
            pass
        self.handle.close()
        self.handle = None

    def __enter__(self):
        if not self.acquire():
            raise Busy("Another Job Watcher run is in progress (the app, a terminal or the "
                       "scheduled task). Try again when it finishes.")
        return self

    def __exit__(self, *exc):
        self.release()


class Busy(Exception):
    """A run already holds the lock."""


__all__ = [n for n in list(globals()) if not n.startswith("__")]
