#!/usr/bin/env python3
"""
Job Watcher

Checks company career sites on Greenhouse, Ashby, Lever, Workday, SmartRecruiters and
Workable directly through their public JSON feeds, plus companies' own careers websites,
filters postings by title, location and tech stack, scores each company on stock, growth,
layoff and funding signals, and keeps a colour-coded Excel tracker up to date. A web search
step finds companies you are not watching yet and adds them to companies.csv.

Settings live in config.toml (plain-English notes inside). API keys go in .env.

Usage:
  python job_watcher.py                 run once
  python job_watcher.py --loop          keep running, checking every check_every_minutes
  python job_watcher.py --since-last-check
                                        only report postings that appeared since the last
                                        time you ran with this flag (first time: past 24h)
  python job_watcher.py --since-last-check --hours 6
                                        same, but look back a fixed 6 hours this time
  python job_watcher.py --show-config   explain your current settings in plain English
  python job_watcher.py --search        run the new-company web search now
  python job_watcher.py --search --look-back week
                                        search a wider window once (day, week or month)
  python job_watcher.py --add URL       watch a company from any job or careers page link
  python job_watcher.py --import companies.xlsx
                                        add every company in a spreadsheet (needs a Company
                                        column; a careers page column is used when present)
  python job_watcher.py --find-career-pages
                                        use web search to find careers pages for companies
                                        with no job board found yet
  python job_watcher.py --verify        re-check every company in companies.csv,
                                        including ones previously marked not_found
  python job_watcher.py --refresh-metrics
                                        refresh stock metrics and scores now
                                        (needs: pip install yfinance)
  python job_watcher.py --refilter      re-run your filters on the saved postings only,
                                        e.g. after editing config.toml; makes no requests

Every run keeps two lists: postings.db / all_postings.csv hold every open posting from
every board, and job_tracker.xlsx holds the ones that pass your filters.

Your Status and Notes edits in the Excel file are preserved between runs.
Close the Excel file if you want a run to update it; otherwise the update is picked up
on the next run (nothing is lost, everything is kept in the state file).
"""
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
import sqlite3
import sys
import threading
import time
import zlib
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
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

BASE = Path(__file__).resolve().parent
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


# ----------------------------------------------------------------- settings
# The defaults below are the settings the project ships with. Anything config.toml leaves
# out falls back to these, so a config file only needs the lines someone wants to change.
DEFAULTS = {
    # 1. Which jobs
    "titles_include": ["Software Engineer", "Software Developer", "Web Developer", "Frontend",
                       "Front End", "Front-End", "Full Stack", "Full-Stack", "Fullstack",
                       "UI Engineer", "Product Engineer", "React", "JavaScript"],
    "titles_exclude": ["Staff", "Principal", "Intern", "Internship", "Co-op", "Coop", "Junior", "Jr",
                       "New Grad", "Graduate", "Entry Level", "Manager", "Director", "Head of", "VP"],
    "stack_keywords": ["React", "React Native", "JavaScript", "TypeScript", "Node.js", "NodeJS",
                       "Next.js"],
    "require_stack_match": True,
    "max_posted_age_hours": 24,
    # 2. Where
    "locations_exclude": ["India", "Bengaluru", "Bangalore", "Hyderabad", "Pune", "Chennai",
                          "Gurugram", "Gurgaon", "Noida", "Mumbai", "Delhi"],
    "regions": [
        {"name": "Canada - BC", "color": "C6EFCE",
         "match": ["British Columbia", "BC", "Vancouver", "Burnaby", "Victoria", "Kelowna"]},
        {"name": "Canada - Ontario", "color": "DDEBF7",
         "match": ["Ontario", "ON", "Toronto", "Ottawa", "Waterloo", "Kitchener", "Mississauga",
                   "Brampton", "Markham", "Guelph"]},
        {"name": "Canada - Other/Remote", "color": "D9F2F2",
         "match": ["Canada", "Canadian", "Montreal", "Montréal", "Calgary", "Edmonton", "Halifax",
                   "Winnipeg", "Quebec"]},
        {"name": "California", "color": "FCE4D6",
         "match": ["California", "CA", "San Francisco", "SF", "Bay Area", "Los Angeles", "San Jose",
                   "San Diego", "Palo Alto", "Mountain View", "Menlo Park", "Sunnyvale",
                   "Santa Clara", "Redwood City", "Oakland", "Santa Monica", "Irvine", "Cupertino"]},
        {"name": "Seattle", "color": "E4DFEC", "match": ["Seattle", "Bellevue", "Redmond", "Kirkland"]},
        {"name": "Remote - North America", "color": "FFF2CC", "match": ["North America", "Americas"]},
    ],
    # 3. Web search for new companies
    "search": {
        "enabled": True, "provider": "serper", "runs_per_day": 2, "look_back": "day",
        "sites": ["ashby", "greenhouse", "lever"],
        "titles": ["Software Engineer", "Software Developer", "Web Developer"],
        "places": ["Toronto", "Vancouver", "Ottawa", "Canada"],
        "skip_words": ["India"],
        "pages_per_query": 3, "monthly_query_limit": 1000, "new_companies": "watch",
        "country": "", "career_page_lookups_per_run": 50,
    },
    # 4. Companies' own careers websites
    "career_sites": {"enabled": True, "max_new_pages_per_site": 15},
    # 5. Tracker and timing
    "excel_file": "job_tracker.xlsx",
    "companies_file": "companies.csv",
    "desktop_notifications": True,
    "since_last_check_by_default": False,
    "interval_minutes": 30,
    "check_all_every_run": True,
    "retry_board_search_hours": 24,
    # 7. Recruiter outreach (outreach.py)
    "outreach": {
        "resume": "resume.pdf", "tailored_resumes_folder": "", "about_me": "about_me.md",
        "contacts_per_company": 2, "departments": ["hr", "it", "management", "executive"],
        "verify_emails": True, "monthly_hunter_credit_limit": 50, "follow_up_after_days": 5,
        "imap_server": "imap.gmail.com", "drafts_folder": "[Gmail]/Drafts",
    },
    # 6. Advanced
    "postings_db": "postings.db",
    "all_postings_csv": "all_postings.csv",
    "request_delay_seconds": 0,
    "host_min_interval_seconds": {},
    "website_seconds_between_requests": 1.0,
    "rate_limit_backoff_seconds": 30,
    "parallel_requests": 8,
    "recheck_not_found_days": 14,
    "max_detections_per_run": 40,
    "max_detail_requests_per_board": 25,
    "search_terms": ["software engineer", "software developer", "frontend", "full stack", "web developer"],
    "max_pages_per_search": 10,
    "unsorted_scan_pages": 25,
    "metrics_refresh_days": 7,
    "metrics_delay_seconds": 0.5,
    "first_check_lookback_hours": 24,
    "check_overlap_minutes": 15,
    "companies": [],
}

# Where each config.toml setting lands. A tuple means a setting inside a group.
CONFIG_LAYOUT = {
    "jobs": {"titles": "titles_include", "skip_titles": "titles_exclude",
             "must_mention": "stack_keywords", "max_age_hours": "max_posted_age_hours"},
    "places": {"skip": "locations_exclude", "region": "regions"},
    "search": {k: ("search", k) for k in DEFAULTS["search"]},
    "career_sites": {"enabled": ("career_sites", "enabled"),
                     "new_pages_per_site_per_run": ("career_sites", "max_new_pages_per_site")},
    "tracker": {"tracker_file": "excel_file", "companies_file": "companies_file",
                "desktop_notifications": "desktop_notifications",
                "since_last_check_by_default": "since_last_check_by_default",
                "check_every_minutes": "interval_minutes",
                "check_all_companies_every_run": "check_all_every_run",
                "retry_board_search_hours": "retry_board_search_hours"},
    "outreach": {k: ("outreach", k) for k in DEFAULTS["outreach"]},
    "advanced": {"parallel_requests": "parallel_requests",
                 "recheck_not_found_days": "recheck_not_found_days",
                 "max_detections_per_run": "max_detections_per_run",
                 "max_detail_requests_per_board": "max_detail_requests_per_board",
                 "rate_limit_backoff_seconds": "rate_limit_backoff_seconds",
                 "metrics_refresh_days": "metrics_refresh_days",
                 "metrics_delay_seconds": "metrics_delay_seconds",
                 "first_check_lookback_hours": "first_check_lookback_hours",
                 "check_overlap_minutes": "check_overlap_minutes",
                 "workday_search_terms": "search_terms",
                 "max_pages_per_search": "max_pages_per_search",
                 "unsorted_scan_pages": "unsorted_scan_pages",
                 "extra_companies": "companies",
                 "request_delay_seconds": "request_delay_seconds",
                 "site_seconds_between_requests": "host_min_interval_seconds",
                 "website_seconds_between_requests": "website_seconds_between_requests",
                 "postings_db": "postings_db", "all_postings_csv": "all_postings_csv"},
}


class ConfigError(Exception):
    """A setting the script cannot use. The message says what to fix."""


def _coerce(value, default, where):
    """Accept the forgiving forms people type: a single word where a list is expected,
    "yes"/"no" for true/false, "24" for 24."""
    if isinstance(default, bool):
        if isinstance(value, bool):
            return value
        if str(value).strip().lower() in ("yes", "y", "true", "on", "1"):
            return True
        if str(value).strip().lower() in ("no", "n", "false", "off", "0"):
            return False
        raise ConfigError(f"{where} should be true or false, not {value!r}")
    if isinstance(default, (int, float)) and not isinstance(default, bool):
        try:
            number = float(value)
        except (TypeError, ValueError):
            raise ConfigError(f"{where} should be a number, not {value!r}") from None
        return int(number) if isinstance(default, int) and number.is_integer() else number
    if isinstance(default, list):
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, list):
            raise ConfigError(f"{where} should be a list in [square brackets]")
        return value
    if isinstance(default, str):
        return str(value)
    return value


def _check_regions(regions, where):
    out = []
    for i, r in enumerate(regions, start=1):
        if not isinstance(r, dict) or not r.get("name") or not r.get("match"):
            raise ConfigError(f"{where} entry {i} needs a name and a match list")
        match = r["match"] if isinstance(r["match"], list) else [r["match"]]
        out.append({"name": str(r["name"]), "color": str(r.get("color") or "").lstrip("#") or None,
                    "match": [str(m) for m in match]})
    return out


def _from_toml(doc):
    """Turn the friendly config.toml layout into the settings the script uses."""
    cfg = json.loads(json.dumps(DEFAULTS))  # deep copy
    unknown = []
    for section, values in doc.items():
        layout = CONFIG_LAYOUT.get(section)
        if layout is None or not isinstance(values, dict):
            unknown.append(f"[{section}]")
            continue
        for key, value in values.items():
            target = layout.get(key)
            if target is None:
                unknown.append(f"{key} in [{section}]")
                continue
            where = f"{key} in [{section}]"
            if isinstance(target, tuple):
                group, name = target
                cfg[group][name] = _coerce(value, DEFAULTS[group][name], where)
            elif target == "regions":
                cfg["regions"] = _check_regions(value if isinstance(value, list) else [value],
                                                "[[places.region]]")
            elif target == "host_min_interval_seconds":
                if not isinstance(value, dict):
                    raise ConfigError(f"{where} should look like {{ \"workable.com\" = 2.0 }}")
                cfg[target] = value
            else:
                cfg[target] = _coerce(value, DEFAULTS[target], where)
    if "must_mention" in (doc.get("jobs") or {}):
        cfg["require_stack_match"] = bool(cfg["stack_keywords"])
    return cfg, unknown


def _from_json(doc):
    """The older flat config.json still works."""
    cfg = json.loads(json.dumps(DEFAULTS))
    for key, value in doc.items():
        if key in ("search", "career_sites", "outreach") and isinstance(value, dict):
            cfg[key].update(value)
        else:
            cfg[key] = value
    cfg["regions"] = _check_regions(cfg["regions"], "regions")
    return cfg


_config_notice_shown = False


def load_config():
    """Read config.toml (or the older config.json). Raises ConfigError with a readable reason."""
    global _config_notice_shown
    if CONFIG_PATH.exists():
        if tomllib is None:
            raise ConfigError("config.toml needs Python 3.11 or newer, or run: pip install tomli")
        try:
            doc = tomllib.loads(CONFIG_PATH.read_text(encoding="utf-8-sig"))
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(f"config.toml has a typo: {exc}. Check that text is in \"quotes\" "
                              "and list items are separated by commas.") from None
        cfg, unknown = _from_toml(doc)
        for item in unknown:
            log.warning("config.toml: ignored unknown setting %s (check the spelling).", item)
        if LEGACY_CONFIG_PATH.exists() and not _config_notice_shown:
            log.info("Using config.toml. config.json is no longer read and can be deleted.")
        _config_notice_shown = True
    elif LEGACY_CONFIG_PATH.exists():
        try:
            cfg = _from_json(json.loads(LEGACY_CONFIG_PATH.read_text(encoding="utf-8-sig")))
        except json.JSONDecodeError as exc:
            raise ConfigError(f"config.json is not valid JSON: {exc}") from None
    else:
        log.info("No config.toml found; using the built-in defaults.")
        cfg = json.loads(json.dumps(DEFAULTS))
    s = cfg["search"]
    s["provider"] = s["provider"].strip().lower()
    s["look_back"] = s["look_back"].strip().lower()
    s["new_companies"] = s["new_companies"].strip().lower()
    if s["provider"] not in SEARCH_KEYS:
        raise ConfigError(f"provider in [search] should be one of {', '.join(SEARCH_KEYS)}, "
                          f"not {s['provider']!r}")
    if s["look_back"] not in LOOK_BACK:
        raise ConfigError(f"look_back in [search] should be day, week or month, not {s['look_back']!r}")
    if s["new_companies"] not in ("watch", "review"):
        raise ConfigError("new_companies in [search] should be \"watch\" or \"review\"")
    bad_sites = [x for x in s["sites"] if x.lower() not in SEARCH_SITES]
    if bad_sites:
        raise ConfigError(f"sites in [search] can only use {', '.join(SEARCH_SITES)}; "
                          f"not {', '.join(bad_sites)}")
    return cfg


def load_env(path=ENV_PATH):
    """Read KEY=value lines from .env into the environment (real environment variables win).
    Also accepts the .env.txt name Notepad tends to save, and UTF-16 files from PowerShell."""
    if not path.exists() and path.with_name(path.name + ".txt").exists():
        path = path.with_name(path.name + ".txt")
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        text = raw.decode("utf-16")
    else:
        text = raw.decode("utf-8-sig", errors="replace")
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().removeprefix("export ").strip()
        value = value.strip().strip('"').strip("'")
        if key and value and key not in os.environ:
            os.environ[key] = value


def describe_config(cfg):
    """Plain-English summary for --show-config."""
    s, c = cfg["search"], cfg["career_sites"]
    lines = ["A posting is added to your tracker when ALL of these are true:",
             f"  - the title contains one of: {', '.join(cfg['titles_include'])}"]
    if cfg["titles_exclude"]:
        lines.append(f"  - the title does not contain: {', '.join(cfg['titles_exclude'])}")
    if not any(t.lower() in ("senior", "sr", "sr.", "lead") for t in cfg["titles_exclude"]):
        lines.append("    (Senior and Lead roles are kept)")
    if cfg["require_stack_match"] and cfg["stack_keywords"]:
        lines.append(f"  - the description mentions one of: {', '.join(cfg['stack_keywords'])}")
    else:
        lines.append("  - any tech stack (no keyword check)")
    lines.append("  - the location is in one of your regions:")
    for r in cfg["regions"]:
        lines.append(f"      {r['name']}: {', '.join(r['match'])}")
    if cfg["locations_exclude"]:
        lines.append(f"    and not only in: {', '.join(cfg['locations_exclude'])}")
    lines.append(f"  - it was posted within the last {cfg['max_posted_age_hours']:g} hours")
    lines.append("")
    key_name = SEARCH_KEYS[s["provider"]]
    has_key = bool(os.environ.get(key_name))
    if s["enabled"]:
        per_run = len(s["sites"]) * s["pages_per_query"]
        lines.append(f"New-company search: ON, {s['runs_per_day']:g} times a day through {s['provider']} "
                     f"({key_name} {'found' if has_key else 'MISSING'}).")
        if not has_key:
            lines.append(f"  Put this line in {ENV_PATH}:  {key_name}=your-key")
        lines.append(f"  Up to {per_run} queries per search, at most {s['monthly_query_limit']} a month. "
                     f"Looks back one {s['look_back']}.")
        for site in s["sites"]:
            lines.append("  " + build_query(SEARCH_SITES[site.lower()], s["titles"], s["places"],
                                            s["skip_words"]))
        lines.append("  New companies are " + ("watched right away." if s["new_companies"] == "watch"
                                               else "added with enabled = no for you to review."))
    else:
        lines.append("New-company search: OFF (turn it on with enabled = true under [search]).")
    lines.append(f"Company careers websites: {'ON' if c['enabled'] else 'OFF'}"
                 + (f", up to {c['max_new_pages_per_site']} new job pages per site per run."
                    if c["enabled"] else "."))
    lines.append(f"Tracker: {cfg['excel_file']}   Companies list: {cfg['companies_file']}")
    return "\n".join(lines)


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


# ----------------------------------------------------------------- job boards
# Each fetcher returns result(...) or None when the company has no board there.
# A job may carry text=None plus load_detail(), which fetches the description only
# when the title and location already match, to keep request counts low.

def fetch_greenhouse(slug, host=None, auto=False):
    data = get_json(f"https://boards-api.greenhouse.io/v1/boards/{quote(slug)}/jobs", {"content": "true"})
    if not isinstance(data, dict) or "jobs" not in data:
        return None
    jobs = []
    for j in data["jobs"]:
        locs = []
        name = (j.get("location") or {}).get("name")
        if name:
            locs.append(name)
        for office in j.get("offices") or []:
            for k in ("location", "name"):
                if office.get(k):
                    locs.append(office[k])
        jobs.append({
            "key": f"greenhouse:{slug}:{j.get('id')}", "title": j.get("title", ""), "locations": locs,
            "url": j.get("absolute_url", ""),
            # updated_at changes on every edit, so it is not used as a posting date
            "posted": parse_date(j.get("first_published")),
            "text": html_to_text(j.get("content", "")),
        })
    return result(slug, jobs)


def fetch_ashby(slug, host=None, auto=False):
    data = get_json(f"https://api.ashbyhq.com/posting-api/job-board/{quote(slug)}",
                    {"includeCompensation": "true"})
    if not isinstance(data, dict) or "jobs" not in data:
        return None
    jobs = []
    for j in data["jobs"]:
        if j.get("isListed") is False:
            continue
        locs = [j["location"]] if j.get("location") else []
        for sec in j.get("secondaryLocations") or []:
            if isinstance(sec, dict) and sec.get("location"):
                locs.append(sec["location"])
        pa = (j.get("address") or {}).get("postalAddress") or {}
        addr = ", ".join(x for x in (pa.get("addressLocality"), pa.get("addressRegion"),
                                     pa.get("addressCountry")) if x)
        if addr:
            locs.append(addr)
        jobs.append({
            "key": f"ashby:{slug}:{j.get('id')}", "title": j.get("title", ""), "locations": locs,
            "url": j.get("jobUrl") or j.get("applyUrl", ""), "posted": parse_date(j.get("publishedAt")),
            "text": j.get("descriptionPlain") or html_to_text(j.get("descriptionHtml", "")),
        })
    return result(slug, jobs)


def _norm_lever(slug, postings):
    jobs = []
    for j in postings:
        cat = j.get("categories") or {}
        locs = [cat["location"]] if cat.get("location") else []
        locs.extend(l for l in (cat.get("allLocations") or []) if l)
        if j.get("country"):
            locs.append(COUNTRY_NAMES.get(j["country"], j["country"]))
        parts = [j.get("descriptionPlain") or ""]
        for lst in j.get("lists") or []:
            parts.append(lst.get("text", ""))
            parts.append(html_to_text(lst.get("content", "")))
        parts.append(j.get("additionalPlain") or "")
        jobs.append({
            "key": f"lever:{slug}:{j.get('id')}", "title": j.get("text", ""), "locations": locs,
            "url": j.get("hostedUrl", ""), "posted": parse_date(j.get("createdAt")),
            "text": " ".join(parts),
        })
    return jobs


def fetch_lever(slug, host=None, auto=False):
    empty_host = None
    for h in ([host] if host else LEVER_HOSTS):
        try:
            data = get_json(f"{h}/v0/postings/{quote(slug)}", {"mode": "json"})
        except RateLimited:
            raise
        except Exception:
            continue
        if not isinstance(data, list):
            continue
        if data:
            return result(slug, _norm_lever(slug, data), host=h)
        empty_host = empty_host or h
    return result(slug, [], host=empty_host) if empty_host and not auto else None


# --- SmartRecruiters
SR_API = "https://api.smartrecruiters.com/v1/companies"


def _sr_detail(slug, pid):
    d = get_json(f"{SR_API}/{quote(slug)}/postings/{pid}")
    if not isinstance(d, dict):
        return {}  # failed: leave the description empty so it is tried again next run
    sections = (d.get("jobAd") or {}).get("sections") or {}
    text = " ".join(html_to_text((sections.get(k) or {}).get("text", ""))
                    for k in ("jobDescription", "qualifications", "additionalInformation"))
    return {"text": text, "url": d.get("postingUrl")}


def fetch_smartrecruiters(slug, host=None, auto=False):
    base = f"{SR_API}/{quote(slug)}/postings"
    first = get_json(base, {"limit": 100, "offset": 0})
    if not isinstance(first, dict) or "content" not in first:
        return None
    total = first.get("totalFound") or 0
    if auto and total == 0:
        return None  # the API answers for any name, so an empty result proves nothing
    postings = {}
    now = dt.datetime.now(dt.timezone.utc)

    def page(extra, offset):
        data = first if (not extra and offset == 0) else get_json(base, {**extra, "limit": 100,
                                                                          "offset": offset})
        return (data.get("content") or [], data.get("totalFound") or 0) if isinstance(data, dict) else None

    def age(p):
        d = parse_date(p.get("releasedDate"))
        return (now - d).total_seconds() / 86400 if d else None

    if total <= 100 * SETTINGS["max_pages"]:  # small enough to read everything
        scan = scan_recent(lambda o: page({}, o), lambda p: None, lambda p: p.get("id"), 100)
        items, complete, ordered, pages = scan
        postings.update(items)
        note = scan_note(pages, True, complete)
    else:  # large employer: read newest first and stop once postings are older than the window
        items, complete, ordered, pages = scan_recent(lambda o: page({}, o), age,
                                                      lambda p: p.get("id"), 100)
        postings.update(items)
        if not ordered:
            for term in SETTINGS["search_terms"]:
                more = scan_recent(lambda o, t=term: page({"q": t}, o), lambda p: None,
                                   lambda p: p.get("id"), 100, max_pages=SETTINGS["max_pages"])
                if more:
                    postings.update(more[0])
        complete = False
        note = scan_note(pages, ordered, False)

    jobs = []
    for pid, p in postings.items():
        if not pid:
            continue
        loc = p.get("location") or {}
        country = COUNTRY_NAMES.get((loc.get("country") or "").upper(), loc.get("country") or "")
        locs = [", ".join(x for x in (loc.get("city"), loc.get("region"), country) if x)]
        if loc.get("fullLocation"):
            locs.append(loc["fullLocation"])
        if loc.get("remote"):
            locs.append(f"Remote {country}".strip())
        jobs.append({
            "key": f"smartrecruiters:{slug}:{pid}", "title": p.get("name", ""),
            "locations": [l for l in locs if l],
            "url": f"https://jobs.smartrecruiters.com/{slug}/{pid}",
            "posted": parse_date(p.get("releasedDate")), "text": None,
            "load_detail": partial(_sr_detail, slug, pid),
        })
    return result(slug, jobs, complete=complete, note=note)


# --- Workable
def fetch_workable(slug, host=None, auto=False):
    data = get_json(f"https://apply.workable.com/api/v1/widget/accounts/{quote(slug)}", {"details": "true"})
    if not isinstance(data, dict) or "jobs" not in data:
        return None
    jobs = []
    for j in data["jobs"]:
        locs = []
        main = ", ".join(x for x in (j.get("city"), j.get("state"), j.get("country")) if x)
        if main:
            locs.append(main)
        for l in j.get("locations") or []:
            s = ", ".join(x for x in (l.get("city"), l.get("region"), l.get("country")) if x)
            if s:
                locs.append(s)
        if j.get("telecommuting"):
            locs.append(f"Remote {j.get('country') or ''}".strip())
        code = j.get("shortcode") or j.get("code") or j.get("id")
        jobs.append({
            "key": f"workable:{slug}:{code}", "title": j.get("title", ""), "locations": locs,
            "url": j.get("url") or j.get("shortlink") or f"https://apply.workable.com/{slug}/j/{code}/",
            "posted": parse_date(j.get("published_on") or j.get("created_at")),
            "text": html_to_text(j.get("description", "")) + " " + html_to_text(j.get("requirements", "")),
        })
    return result(slug, jobs)


# --- Workday
WORKDAY_URL_RE = re.compile(
    r"(?:https?://)?([\w-]+)\.(wd\d+)\.myworkdayjobs\.com/(?:[a-z]{2}-[A-Z]{2}/)?([^/?#]+)")


def parse_workday(s):
    """Accepts a careers URL, 'tenant/wd5/site', or just 'tenant'."""
    s = s.strip()
    m = WORKDAY_URL_RE.match(s)
    if m:
        return m.group(1), m.group(2), m.group(3)
    parts = [p for p in s.split("/") if p]
    if len(parts) == 3:
        return parts[0], parts[1], parts[2]
    return s, None, None


def _wd_list(tenant, shard, site, term, offset, retries=3):
    url = f"https://{tenant}.{shard}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs"
    return post_json(url, {"appliedFacets": {}, "limit": 20, "offset": offset, "searchText": term}, retries)


def discover_workday(tenant, shard=None):
    """Find the career site name by trying the patterns most companies use."""
    t = tenant[:1].upper() + tenant[1:]
    sites = list(dict.fromkeys([
        "External", "Careers", "careers", tenant, t, f"{t}Careers", f"{tenant}careers",
        "External_Careers", "ExternalCareers", "External_Career_Site", "ExternalCareerSite",
        f"{t}_Careers", f"{t}External", "jobs", "Search"]))
    for sh in ([shard] if shard else WORKDAY_SHARDS):
        for site in sites:
            try:
                data = _wd_list(tenant, sh, site, "", 0, retries=1)
            except RateLimited:
                raise  # stop guessing for now; detection continues on a later run
            except requests.ConnectionError:
                break  # this tenant is not on this shard
            except Exception:
                continue
            if isinstance(data, dict) and "jobPostings" in data:
                return sh, site
    return None


def _wd_detail(base, tenant, site, path):
    d = get_json(f"{base}/wday/cxs/{tenant}/{site}{path}")
    if not isinstance(d, dict):
        return {}  # failed: leave the description empty so it is tried again next run
    info = d.get("jobPostingInfo") or d
    locs = [info.get("location")] + list(info.get("additionalLocations") or [])
    country = (info.get("country") or {}).get("descriptor") if isinstance(info.get("country"), dict) else None
    if country:
        locs.append(country)
    return {"text": html_to_text(info.get("jobDescription", "")),
            "locations": [l for l in locs if l],
            "posted": parse_date(info.get("startDate")), "url": info.get("externalUrl")}


def fetch_workday(slug, host=None, auto=False):
    tenant, shard, site = parse_workday(slug)
    if not site:
        found = discover_workday(tenant, shard)
        if not found:
            return None
        shard, site = found
    base = f"https://{tenant}.{shard}.myworkdayjobs.com"

    def page(term, offset):
        data = _wd_list(tenant, shard, site, term, offset)
        if not isinstance(data, dict) or "jobPostings" not in data:
            return None
        return data.get("jobPostings") or [], data.get("total") or 0  # total is only on page one

    # Keyword searches come back ranked by relevance, which buries today's postings under
    # older ones. The unsearched list is newest first, so read it from the top and stop at
    # the first postings older than the look-back window.
    # Labels are whole days and can disagree by one around midnight, hence tolerance=1.
    scan = scan_recent(lambda o: page("", o), lambda p: wd_age_days(p.get("postedOn")),
                       lambda p: p.get("externalPath"), 20, tolerance=1)
    if scan is None:
        return None
    postings, complete, ordered, pages = scan
    if not ordered:  # this site does not list newest first; add keyword searches as a backstop
        for term in SETTINGS["search_terms"]:
            more = scan_recent(lambda o, t=term: page(t, o), lambda p: None,
                               lambda p: p.get("externalPath"), 20, max_pages=SETTINGS["max_pages"])
            if more:
                for k, v in more[0].items():
                    postings.setdefault(k, v)
    note = scan_note(pages, ordered, complete)
    canonical = f"{tenant}/{shard}/{site}"
    jobs = []
    for path, p in postings.items():
        loc = p.get("locationsText") or ""
        ambiguous = bool(re.match(r"^\d+\s+Locations?$", loc.strip(), re.I))
        jobs.append({
            "key": f"workday:{canonical}:{path}", "title": p.get("title", ""),
            "locations": [] if ambiguous or not loc else [loc],
            "url": f"{base}/en-US/{site}{path}", "posted": parse_relative(p.get("postedOn")),
            "text": None, "ambiguous_location": ambiguous or not loc,
            "load_detail": partial(_wd_detail, base, tenant, site, path),
        })
    return result(canonical, jobs, complete=complete, note=note)


# --- Recognising job-board links
# Any job or board link from Greenhouse, Ashby, Lever, Workable, SmartRecruiters or Workday
# can be turned into (board, slug). Search results, careers pages and --add all use this.
NOT_SLUGS = {"embed", "v0", "v1", "v3", "api", "j", "jobs", "job", "careers", "search", "wday",
             "posting-api", "job-board", "postings", "companies", "widget", "accounts", "static",
             "assets", "favicon.ico", "robots.txt", "sitemap.xml", "apply", "login", "signup", "www"}
BOARD_URL_RE = re.compile(
    r"(?:https?:)?//[A-Za-z0-9.-]+\.(?:greenhouse\.io|ashbyhq\.com|lever\.co|workable\.com|"
    r"smartrecruiters\.com|myworkdayjobs\.com)[^\s\"'<>\\)]*")
LEVER_EMBED_RE = re.compile(r"accountName\s*[:=]\s*['\"]([\w.-]+)['\"]")


def _slug_ok(slug):
    slug = (slug or "").strip()
    if (not slug or slug.lower() in NOT_SLUGS or len(slug) > 80
            or not re.fullmatch(r"[\w .&'-]+", slug)):
        return None
    return slug


def board_from_url(url):
    """('greenhouse', 'acme') style tuple for a job-board link, or None."""
    if not url:
        return None
    url = url.strip().replace("\\/", "/")
    if url.startswith("//"):
        url = "https:" + url
    if "://" not in url:
        url = "https://" + url
    try:
        p = urlparse(url)
        host = (p.hostname or "").lower()
    except ValueError:
        return None
    segs = [unquote(x) for x in p.path.split("/") if x]
    query = parse_qs(p.query)

    def pick(provider, slug):
        slug = _slug_ok(slug)
        return (provider, slug) if slug else None

    if host == "greenhouse.io" or host.endswith(".greenhouse.io"):
        if query.get("for"):
            return pick("greenhouse", query["for"][0])
        if host.startswith("boards-api.") and len(segs) >= 3 and segs[:2] == ["v1", "boards"]:
            return pick("greenhouse", segs[2])
        if host.split(".")[0] in ("boards", "job-boards") and segs:
            return pick("greenhouse", segs[0])
        return None
    if host == "jobs.ashbyhq.com" and segs:
        return pick("ashby", segs[0])
    if host == "api.ashbyhq.com" and len(segs) >= 3 and segs[:2] == ["posting-api", "job-board"]:
        return pick("ashby", segs[2])
    if host in ("jobs.lever.co", "jobs.eu.lever.co") and segs:
        return pick("lever", segs[0])
    if host.startswith("api.") and host.endswith("lever.co") and len(segs) >= 3 and segs[:2] == ["v0", "postings"]:
        return pick("lever", segs[2])
    if host == "apply.workable.com" and segs:
        if segs[0] == "api":
            if "accounts" in segs and segs.index("accounts") + 1 < len(segs):
                return pick("workable", segs[segs.index("accounts") + 1])
            return None
        return pick("workable", segs[0])
    if host in ("jobs.smartrecruiters.com", "careers.smartrecruiters.com") and segs:
        return pick("smartrecruiters", segs[0])
    if host == "api.smartrecruiters.com" and len(segs) >= 3 and segs[:2] == ["v1", "companies"]:
        return pick("smartrecruiters", segs[2])
    if host.endswith(".myworkdayjobs.com"):
        parts = host.split(".")
        if len(parts) < 4 or not re.fullmatch(r"wd\d+", parts[1]):
            return None
        if segs[:2] == ["wday", "cxs"]:
            site = segs[3] if len(segs) >= 4 else None
        else:
            rest = segs[1:] if segs and re.fullmatch(r"[a-z]{2}-[A-Za-z]{2}", segs[0]) else segs
            site = rest[0] if rest else None
        if not site or site.lower() == "wday" or not re.fullmatch(r"[\w.-]{1,80}", site):
            return None  # site names like "Careers" or "jobs" are normal on Workday
        return "workday", f"https://{parts[0]}.{parts[1]}.myworkdayjobs.com/{site}"
    return None


def board_key(provider, slug):
    """Comparable identity for a board, so 'Acme' and 'acme' count as the same one."""
    provider = (provider or "").lower()
    if provider == "workday":
        tenant, _, site = parse_workday(slug or "")
        return provider, (tenant or "").lower(), (site or "").lower()
    return provider, (slug or "").strip().lower()


def find_boards_in_html(text):
    """Job boards a careers page links to or embeds, most-mentioned first."""
    text = (text or "").replace("\\/", "/")
    votes, seen = Counter(), {}
    for m in BOARD_URL_RE.finditer(text):
        b = board_from_url(m.group(0))
        if b:
            votes[board_key(*b)] += 1
            seen.setdefault(board_key(*b), b)
    if "lever" in text.lower():
        for m in LEVER_EMBED_RE.finditer(text):
            b = ("lever", _slug_ok(m.group(1)))
            if b[1]:
                votes[board_key(*b)] += 1
                seen.setdefault(board_key(*b), b)
    return [seen[k] for k, _ in votes.most_common()]


# --- Reading web pages (companies' own careers sites)
_ROBOTS = {}
_ROBOTS_LOCK = threading.Lock()
_PAGES_LOCK = threading.Lock()  # guards the cache of job pages already read


def get_page(url, accept="text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
             max_bytes=5_000_000, retries=2):
    """(final_url, text) for an ordinary web page, or None if it cannot be read.
    Shares the polite per-site spacing and 429 handling used for the job boards."""
    site = HostLimiter.site(url)
    for attempt in range(retries):
        last = attempt == retries - 1
        if site in LIMITER.blocked:
            raise RateLimited(f"{site} asked to slow down")
        LIMITER.wait(site, SETTINGS["website_interval"])
        try:
            r = session().get(url, headers={"Accept": accept}, timeout=25, stream=True)
            if r.status_code == 429:
                r.close()
                LIMITER.cool_down(site, retry_after(r))
                LIMITER.refused(site)
                if last:
                    raise RateLimited(f"{site} asked to slow down")
                continue
            LIMITER.accepted(site)
            if r.status_code >= 500:
                r.close()
                if last:
                    return None
                time.sleep(3)
                continue
            if r.status_code >= 400:
                r.close()
                return None
            body = bytearray()
            for chunk in r.iter_content(65536):
                body.extend(chunk)
                if len(body) > max_bytes:
                    break
            r.close()
        except requests.RequestException:
            if last:
                return None
            time.sleep(2)
            continue
        raw = bytes(body)
        if url.lower().endswith(".gz") or raw[:2] == b"\x1f\x8b":
            try:
                raw = gzip.decompress(raw)
            except OSError:
                return None
        charset = re.search(r"charset=([\w-]+)", r.headers.get("Content-Type", ""), re.I)
        try:
            text = raw.decode(charset.group(1) if charset else "utf-8", errors="replace")
        except LookupError:
            text = raw.decode("utf-8", errors="replace")
        return r.url, text
    return None


def robots_for(url):
    """The site's robots.txt rules (read once per run). A missing file allows everything."""
    p = urlparse(url)
    origin = f"{p.scheme}://{p.netloc}"
    with _ROBOTS_LOCK:
        if origin in _ROBOTS:
            return _ROBOTS[origin]
    got = get_page(origin + "/robots.txt", accept="text/plain,*/*", max_bytes=500_000)
    rules = RobotFileParser()
    rules.parse(got[1].splitlines() if got and "<html" not in got[1][:500].lower() else [])
    with _ROBOTS_LOCK:
        _ROBOTS[origin] = rules
    return rules


def robots_allow(url):
    return robots_for(url).can_fetch(USER_AGENT, url)


class PageParser(HTMLParser):
    """Collects what the careers reader needs from a page: links with their text,
    JSON-LD blocks, schema.org microdata for job postings, and the visible text."""
    WANTED = {"title", "datePosted", "description", "addressLocality", "addressRegion",
              "addressCountry", "validThrough", "jobLocation"}
    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source",
            "track", "wbr"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links, self.jsonld, self.text = [], [], []
        self.micro = {}
        self.has_microdata_job = False
        self.base = None
        self._a = None
        self._script = None
        self._skip = 0
        self._captures = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "base" and a.get("href") and not self.base:
            self.base = a["href"]
        if tag == "a" and a.get("href"):
            self._a = [a["href"], []]
        if tag == "script":
            self._script = [] if "ld+json" in (a.get("type") or "").lower() else None
            self._skip += 1
        elif tag in ("style", "noscript", "template"):
            self._skip += 1
        if "jobposting" in (a.get("itemtype") or "").lower():
            self.has_microdata_job = True
        prop = a.get("itemprop")
        if prop in self.WANTED and prop not in self.micro:
            if a.get("content") or a.get("datetime"):
                self.micro[prop] = a.get("content") or a.get("datetime")
            elif tag not in self.VOID:
                self._captures.append([prop, tag, 0, []])
        for cap in self._captures:
            if cap[1] == tag:
                cap[2] += 1

    def handle_endtag(self, tag):
        if tag == "a" and self._a is not None:
            self.links.append((self._a[0], " ".join(" ".join(self._a[1]).split())))
            self._a = None
        if tag == "script":
            if self._script is not None:
                self.jsonld.append("".join(self._script))
            self._script = None
            self._skip = max(0, self._skip - 1)
        elif tag in ("style", "noscript", "template"):
            self._skip = max(0, self._skip - 1)
        for cap in list(self._captures):
            if cap[1] == tag:
                cap[2] -= 1
                if cap[2] <= 0:
                    self.micro.setdefault(cap[0], " ".join(" ".join(cap[3]).split()))
                    self._captures.remove(cap)

    def handle_data(self, data):
        if self._script is not None:
            self._script.append(data)
            return
        if self._skip:
            return
        self.text.append(data)
        if self._a is not None:
            self._a[1].append(data)
        for cap in self._captures:
            cap[3].append(data)


def parse_page(text):
    parser = PageParser()
    try:
        parser.feed(text)
        parser.close()
    except Exception:  # a badly broken page still yields whatever was read before the error
        pass
    return parser


def _as_list(x):
    return x if isinstance(x, list) else ([] if x is None else [x])


def _names(x):
    out = []
    for item in _as_list(x):
        if isinstance(item, dict):
            item = item.get("name") or item.get("addressCountry") or ""
        if item:
            out.append(COUNTRY_NAMES.get(str(item).upper(), str(item)))
    return out


def _walk_jsonld(node, found):
    if isinstance(node, list):
        for item in node:
            _walk_jsonld(item, found)
    elif isinstance(node, dict):
        kinds = [str(t).lower() for t in _as_list(node.get("@type"))]
        if "jobposting" in kinds:
            found.append(node)
        for key in ("@graph", "itemListElement", "item", "mainEntity"):
            if key in node:
                _walk_jsonld(node[key], found)


def posted_from(value):
    """A posting date. A bare date ('2026-11-20') is read as the end of that day, so a job
    posted late in the evening is not mistaken for one that is a day older."""
    if isinstance(value, str) and re.fullmatch(r"\s*\d{4}-\d{2}-\d{2}\s*", value):
        d = dt.datetime.fromisoformat(value.strip()).replace(hour=23, minute=59, tzinfo=dt.timezone.utc)
        return min(d, dt.datetime.now(dt.timezone.utc))
    return parse_date(value)


def job_from_jsonld(jp, page_url):
    locs = []
    for place in _as_list(jp.get("jobLocation")):
        if isinstance(place, str):
            locs.append(place)
            continue
        if not isinstance(place, dict):
            continue
        addr = place.get("address")
        if isinstance(addr, str):
            locs.append(addr)
        elif isinstance(addr, dict):
            parts = [addr.get("addressLocality"), addr.get("addressRegion")] + _names(addr.get("addressCountry"))
            joined = ", ".join(str(x) for x in parts if x)
            if joined:
                locs.append(joined)
        elif place.get("name"):
            locs.append(str(place["name"]))
    if "telecommute" in str(jp.get("jobLocationType", "")).lower():
        where = _names(jp.get("applicantLocationRequirements"))
        locs.append("Remote" + (f" - {', '.join(where)}" if where else ""))
    text_parts = [str(jp.get("description") or "")]
    for k in ("qualifications", "responsibilities", "skills", "experienceRequirements"):
        if isinstance(jp.get(k), str):
            text_parts.append(jp[k])
    title = html_to_text(str(jp.get("title") or jp.get("name") or "")).strip()
    url = jp.get("url") if isinstance(jp.get("url"), str) and jp["url"].startswith("http") else page_url
    return {"title": " ".join(title.split()), "posted": posted_from(jp.get("datePosted")),
            "locations": [" ".join(l.split()) for l in locs if l and l.strip()],
            "text": html_to_text(" ".join(text_parts)), "url": url,
            "expired": (parse_date(jp.get("validThrough")) or dt.datetime.max.replace(
                tzinfo=dt.timezone.utc)) < dt.datetime.now(dt.timezone.utc)}


def jobs_on_page(parsed, page_url):
    """Job postings a page publishes in standard form (JSON-LD, or microdata as a fallback)."""
    found = []
    for block in parsed.jsonld:
        block = re.sub(r"^\s*(?:<!--|/[/*]\s*<!\[CDATA\[(?:\s*\*/)?)|(?:-->|/[/*]\s*\]\]>(?:\s*\*/)?)\s*$", "",
                       block.strip())  # some sites wrap the data in comment or CDATA markers
        try:
            _walk_jsonld(json.loads(block, strict=False), found)
        except ValueError:
            continue
    jobs = [job_from_jsonld(jp, page_url) for jp in found]
    if not jobs and parsed.has_microdata_job and parsed.micro.get("title"):
        m = parsed.micro
        loc = ", ".join(x for x in (m.get("addressLocality"), m.get("addressRegion"),
                                    COUNTRY_NAMES.get(str(m.get("addressCountry", "")).upper(),
                                                      m.get("addressCountry"))) if x)
        jobs.append({"title": m["title"], "posted": posted_from(m.get("datePosted")),
                     "locations": [loc] if loc else ([m["jobLocation"]] if m.get("jobLocation") else []),
                     "text": m.get("description") or " ".join(" ".join(parsed.text).split()),
                     "url": page_url, "expired": False})
    return [j for j in jobs if j["title"] and not j["expired"]]


JOB_PATH_RE = re.compile(r"/(?:jobs?|careers?|positions?|openings?|opportunit(?:y|ies)|"
                         r"vacanc(?:y|ies)|requisitions?|roles?|postings?|apply)(?:[/?#_-]|$)", re.I)
TRACKING_PARAMS = re.compile(r"^(?:utm_\w+|gh_src|source|src|ref|lever-source|lever-origin|trk)$", re.I)


def _site_of(host):
    parts = (host or "").lower().split(".")
    return ".".join(parts[-2:])


def clean_url(url):
    p = urlparse(url)
    query = "&".join(x for x in p.query.split("&") if x and not TRACKING_PARAMS.match(x.split("=")[0]))
    return p._replace(fragment="", query=query).geturl()


def looks_like_job_link(url, careers_url):
    p, c = urlparse(url), urlparse(careers_url)
    if p.scheme not in ("http", "https") or _site_of(p.hostname) != _site_of(c.hostname):
        return False
    path = p.path.rstrip("/")
    if not path or path == c.path.rstrip("/") or not JOB_PATH_RE.search(path + "/"):
        return False
    last = path.split("/")[-1]
    return bool(re.search(r"\d{3,}", path)) or last.count("-") >= 2 or last.count("_") >= 2


def title_from_url(url):
    """'.../jobs/12345/Senior-Software-Engineer' or '.../job/senior-software-engineer/12345'
    -> 'Senior Software Engineer'. Empty when the link has no words in it."""
    for seg in reversed([x for x in urlparse(url).path.split("/") if x]):
        words = re.sub(r"[-_+]+|\d+|\.html?$", " ", unquote(seg)).split()
        if len([w for w in words if len(w) > 1]) >= 2:
            return " ".join(words)
    return ""


def sitemap_entries(xml):
    """[(kind, url, lastmod)] from a sitemap or sitemap index."""
    out = []
    kind = "index" if "<sitemapindex" in xml[:3000] else "url"
    for block in re.findall(r"<(?:url|sitemap)\b.*?</(?:url|sitemap)>", xml, re.S | re.I):
        loc = re.search(r"<loc>\s*(.*?)\s*</loc>", block, re.S | re.I)
        mod = re.search(r"<lastmod>\s*(.*?)\s*</lastmod>", block, re.S | re.I)
        if loc:
            out.append((kind, html.unescape(loc.group(1)), parse_date(mod.group(1)) if mod else None))
    return out


def sitemap_job_links(careers_url, robots, max_files=3, max_urls=5000):
    """Job links (url, lastmod) from the careers site's sitemaps."""
    p = urlparse(careers_url)
    origin = f"{p.scheme}://{p.netloc}"
    todo = [u for u in (robots.site_maps() or []) if _site_of(urlparse(u).hostname) == _site_of(p.hostname)]
    todo = todo or [origin + "/sitemap.xml"]
    links, read = {}, 0
    while todo and read < max_files and len(links) < max_urls:
        url = todo.pop(0)
        if not robots.can_fetch(USER_AGENT, url):
            continue
        got = get_page(url, accept="application/xml,text/xml,*/*", max_bytes=10_000_000)
        read += 1
        if not got:
            continue
        entries = sitemap_entries(got[1])
        children = [e for e in entries if e[0] == "index"]
        if children:
            jobby = [e for e in children if re.search(r"job|career|position|posting|vacanc", e[1], re.I)]
            todo = [e[1] for e in (jobby or children)[:max_files]] + todo
            continue
        for _, loc, mod in entries:
            if looks_like_job_link(loc, careers_url):
                links.setdefault(clean_url(loc), mod)
    return links


def fetch_website(url, host=None, auto=False):
    """A company's own careers site. If the page is really a Greenhouse, Lever, Ashby,
    Workable, SmartRecruiters or Workday board, that board is read instead (and the company is
    switched to it). Otherwise job pages that publish standard job details are read, a few new
    ones per run, and each page only once."""
    careers = url.strip()
    if "://" not in careers:
        careers = "https://" + careers
    if not robots_allow(careers):
        return result(careers, [], complete=False,
                      note="The site's robots.txt asks automated tools not to read this page, so it is skipped.")
    page = get_page(careers)
    if page is None:
        return None
    final, text = page

    direct = board_from_url(final)
    for provider, slug in ([direct] if direct else find_boards_in_html(text))[:2]:
        r = FETCHERS[provider](slug, None, False)
        if r is not None:
            r["switch_to"] = (provider, r["slug"])
            r["note"] = (f"The careers page uses {provider.title()}, so its board is now read directly. "
                         + (r.get("note") or "")).strip()
            return r

    rules = SETTINGS.get("rules")
    title_ok = rules.title_ok if rules else (lambda t: True)
    cache = SETTINGS.setdefault("website_pages", {})  # shared by worker threads; see _PAGES_LOCK
    # Each careers site gets its own key space, even when two share a host name.
    prefix = f"website:{urlparse(careers).netloc}:{hashlib.sha1(careers.encode()).hexdigest()[:6]}:"
    max_age = dt.timedelta(days=SETTINGS["window_days"] + 2)
    now = dt.datetime.now(dt.timezone.utc)

    def key_for(link):
        return prefix + hashlib.sha1(clean_url(link).encode("utf-8")).hexdigest()[:16]

    parsed = parse_page(text)
    jobs = {}
    for j in jobs_on_page(parsed, final):  # some careers pages list every job as structured data
        if j["url"] and j["url"] != final:
            jobs[key_for(j["url"])] = {**j, "key": key_for(j["url"])}

    candidates = {}  # url -> (title, lastmod)
    base = parsed.base or final
    for href, label in parsed.links:
        link = clean_url(urljoin(base, href))
        if looks_like_job_link(link, final) and link not in candidates:
            candidates[link] = (label, None)
    used_sitemap = False
    if len(candidates) + len(jobs) < 3:
        used_sitemap = True
        for link, mod in sitemap_job_links(final, robots_for(final)).items():
            candidates.setdefault(link, ("", mod))

    to_read = []
    for link, (label, mod) in candidates.items():
        key = key_for(link)
        if key in jobs:
            continue
        title = next((t for t in (label, title_from_url(link)) if len(t) > 3 and title_ok(t)), None)
        if not title:
            continue  # clearly not a role you want; its page is never downloaded
        if mod and now - mod > max_age:
            continue  # last changed before your look-back window, so posted before it too
        with _PAGES_LOCK:
            known = cache.get(key)
        if known is not None:
            if known.get("t"):
                jobs[key] = {"key": key, "title": known["t"], "posted": parse_date(known.get("p")),
                             "locations": known.get("l") or [], "url": link, "text": None}
            else:
                jobs[key] = {"key": key, "title": title, "posted": None, "locations": [], "url": link,
                             "text": None}
            continue
        to_read.append((mod or dt.datetime.min.replace(tzinfo=dt.timezone.utc), link, key, title))

    to_read.sort(reverse=True)  # newest first when the sitemap says when pages changed
    read = no_data = 0
    for _, link, key, title in to_read:
        if read >= SETTINGS["site_pages_per_run"]:
            jobs[key] = {"key": key, "title": title, "posted": None, "locations": [], "url": link,
                         "text": None}
            continue
        if not robots_allow(link):
            continue
        got = get_page(link)
        read += 1
        found = jobs_on_page(parse_page(got[1]), got[0]) if got else []
        if found:
            j = found[0]
            with _PAGES_LOCK:
                cache[key] = {"t": j["title"], "p": j["posted"].isoformat() if j["posted"] else None,
                              "l": j["locations"]}
            jobs[key] = {**j, "key": key, "url": link}
        else:
            no_data += 1
            if got:
                with _PAGES_LOCK:
                    cache[key] = {}
            jobs[key] = {"key": key, "title": title, "posted": None, "locations": [], "url": link,
                         "text": None}

    with _PAGES_LOCK:
        for key in [k for k in cache if k.startswith(prefix) and k not in jobs]:
            del cache[key]  # forget pages that are no longer listed

    if not jobs:
        note = ("No job links found on the careers page. It probably loads jobs with JavaScript, "
                "which this script cannot read; check it directly.")
    else:
        note = (f"Careers website{' (sitemap)' if used_sitemap else ''}: {len(jobs)} relevant job "
                f"link{'s' if len(jobs) != 1 else ''}, {read} page{'s' if read != 1 else ''} read this run"
                + (f", {no_data} without job details" if no_data else "") + ".")
    out = [{k: j.get(k) for k in ("key", "title", "locations", "url", "posted", "text")}
           for j in jobs.values()]
    return result(careers, out, complete=False, note=note)


FETCHERS = {"greenhouse": fetch_greenhouse, "ashby": fetch_ashby, "lever": fetch_lever,
            "smartrecruiters": fetch_smartrecruiters, "workable": fetch_workable,
            "workday": fetch_workday, "website": fetch_website}
AUTO_ORDER = ["greenhouse", "ashby", "lever", "smartrecruiters", "workable"]


def slug_variants(name):
    """The few spellings boards actually use: 'roserocket', 'rose-rocket', 'Rose Rocket'.
    'Jane (Jane App)' also tries 'janeapp'; 'Acme Inc.' also tries 'acme'."""
    base = re.sub(r"\s*\(.*?\)\s*", " ", name).strip() or name.strip()
    base = re.sub(r"[,.]?\s+(inc|ltd|llc|corp|corporation|limited)\.?$", "", base, flags=re.I).strip()
    paren = re.findall(r"\((.*?)\)", name)
    out = []
    pieces = [base] + ([x.strip() for x in base.split(" / ")] if " / " in base else [])
    for text in pieces + [p for p in paren if len(p) > 3 and p.lower() not in ("digital", "canada", "remote")]:
        low = text.lower()
        for v in (re.sub(r"[^a-z0-9]", "", low), re.sub(r"[^a-z0-9]+", "-", low).strip("-")):
            if v and v not in out:
                out.append(v)
    if name.strip() not in out and " " in name.strip() and "(" not in name:
        out.append(name.strip())  # Ashby keeps spaces: 'Rose Rocket'
    return out[:6]


ATS_HINTS = {"greenhouse": ["greenhouse"], "ashby": ["ashby"], "lever": ["lever"], "workable": ["workable"],
             "smartrecruiters": ["smartrecruiters"], "internal": [], "own": [], "workday": [], "icims": [],
             "taleo": [], "successfactors": [], "other": []}


def load_company(entry, state, only=None):
    """Return (provider, result) or None. Auto-detects the board for entries without a prefix.
    only: the job boards worth guessing for this company (from the ats column); None = all."""
    cache = state.setdefault("resolved", {})
    if entry in cache:
        c = cache[entry]
        r = FETCHERS[c["provider"]](c["slug"], c.get("host"))
        if r is not None:
            return c["provider"], r
        cache.pop(entry, None)  # board moved or closed; re-detect below

    if ":" in entry and entry.split(":", 1)[0] in FETCHERS:
        provider, slug = entry.split(":", 1)
        candidates = [(provider, slug, False)]
    else:
        variants = slug_variants(entry)
        # Workable and SmartRecruiters allow the fewest requests, so only the likeliest spelling
        # is tried there unless the company is known to use them.
        candidates = [(p, v, True) for p in AUTO_ORDER if only is None or p in only
                      for v in (variants if only or p not in ("workable", "smartrecruiters") else variants[:1])]

    for provider, slug, auto in candidates:
        r = FETCHERS[provider](slug, None, auto)
        if r is not None:
            if r.get("switch_to"):  # a careers website that is really a job board
                provider = r["switch_to"][0]
            cache[entry] = {"provider": provider, "slug": r["slug"], "host": r.get("host")}
            return provider, r
    return None


def display_name(entry):
    name = entry.split(":", 1)[1] if ":" in entry else entry
    return name if any(c.isupper() for c in name) else name.replace("-", " ").title()


# ----------------------------------------------------------------- company list
CSV_FIELDS = ["priority", "region", "company", "score", "tier", "type", "ticker", "stage",
              "last_raise", "layoffs_12m", "board", "slug", "careers_url", "domain", "ats", "hubs", "enabled",
              "status", "verified", "market_cap_b", "stock_12m_pct", "revenue_growth_pct",
              "profit_margin_pct", "metrics_updated", "notes"]


def companies_path(cfg):
    return BASE / cfg.get("companies_file", "companies.csv")


def load_company_rows(cfg):
    """Rows from companies.csv, plus any extra_companies listed in config.toml."""
    rows, path = [], companies_path(cfg)
    if path.exists():
        with open(path, newline="", encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                r = {k.strip().lower(): (v or "").strip() for k, v in r.items() if k}
                if r.get("company"):
                    rows.append(r)
    else:
        log.warning("%s not found; using only the extra_companies listed in config.toml", path.name)
    for extra in cfg.get("companies", []):
        board, slug = extra.split(":", 1) if ":" in extra else ("auto", extra)
        rows.append({"priority": "9", "region": "From config file", "company": display_name(extra),
                     "board": board, "slug": slug, "enabled": "yes"})
    return rows


def save_company_rows(cfg, rows):
    """Write detected boards, metrics and scores back so the list maintains itself."""
    path = companies_path(cfg)
    file_rows = sorted((r for r in rows if r.get("region") != "From config file"),
                       key=lambda r: (priority_of(r), r["company"].lower()))
    fields = list(CSV_FIELDS) + sorted({k for r in file_rows for k in r} - set(CSV_FIELDS))
    tmp = path.with_name(path.stem + ".saving.csv")
    try:
        with open(tmp, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(file_rows)
        os.replace(tmp, path)
    except PermissionError:
        log.warning("%s is open in another program; updates will be saved next run.", path.name)
        tmp.unlink(missing_ok=True)


def reads_website(row):
    """True when this company is read from its own careers website."""
    board = (row.get("board") or "").lower()
    url = row.get("careers_url") or (row.get("slug") if board == "website" else "")
    return bool(SETTINGS["career_sites"] and url and (board == "website" or board in MANUAL_BOARDS))


def entry_for(row):
    board, slug = (row.get("board") or "auto").lower(), row.get("slug") or ""
    if reads_website(row):
        return f"website:{row.get('careers_url') or slug}"
    return f"{board}:{slug}" if board in FETCHERS and board != "website" and slug else (slug or row["company"])


def is_enabled(row):
    return (row.get("enabled") or "yes").lower() not in ("no", "n", "false", "0")


def is_manual(row):
    board = (row.get("board") or "").lower()
    return (board in MANUAL_BOARDS or board == "website") and not reads_website(row)


def priority_of(row):
    try:
        return int(row.get("priority") or 9)
    except ValueError:
        return 9


# ----------------------------------------------------------------- scoring
def num(value):
    try:
        return float(str(value).replace("%", "").replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def clamp(x, lo, hi):
    return max(lo, min(hi, x))


STAGE_BASE = {"seed": 45, "early-stage": 47, "series a": 50, "series b": 55, "series c": 60,
              "series d+": 64, "late": 64, "unicorn": 68, "bootstrapped": 55, "nonprofit": 55,
              "subsidiary": 58}


def score_row(r, today=None):
    """0-100 'worth applying' score. Returns ('', '') when there is not enough data yet."""
    today = today or dt.date.today()
    layoffs = (r.get("layoffs_12m") or "").strip()
    penalty = 0 if not layoffs else (4 if "minor" in layoffs.lower() else 12)
    if (r.get("type") or "").lower() == "public":
        s12, rg, pm = num(r.get("stock_12m_pct")), num(r.get("revenue_growth_pct")), num(r.get("profit_margin_pct"))
        cap = num(r.get("market_cap_b"))
        if s12 is None and rg is None and pm is None:
            return "", ""
        score = 50.0
        if s12 is not None:
            score += clamp(s12, -50, 50) * 0.3       # share price trend: up to +/-15
        if rg is not None:
            score += clamp(rg, -20, 40) * 0.5        # revenue growth: -10 to +20
        if pm is not None:
            score += clamp(pm, -30, 30) * 0.25       # profitability: +/-7.5
        if cap is not None:
            score += 5 if cap >= 50 else 3 if cap >= 5 else 0 if cap >= 1 else -5
    else:
        base = STAGE_BASE.get((r.get("stage") or "").strip().lower())
        if base is None:
            return "", ""
        score = float(base)
        m = re.match(r"(\d{4})(?:-(\d{1,2}))?", r.get("last_raise") or "")
        if m:
            months = (today.year - int(m.group(1))) * 12 + today.month - int(m.group(2) or 6)
            score += 8 if months <= 18 else -8 if months > 30 else 0  # fresh funding = runway
    score = round(clamp(score - penalty, 0, 100))
    return score, ("A" if score >= 65 else "B" if score >= 50 else "C")


def refresh_metrics(cfg, rows, force=False):
    """Pull market cap, 12-month stock change, revenue growth and margin for public companies."""
    days = cfg.get("metrics_refresh_days", 7)
    today = dt.date.today()

    def due(r):
        last = parse_date(r.get("metrics_updated"))
        return force or not last or (today - last.date()).days >= days

    todo = [r for r in rows if r.get("ticker") and due(r)]
    if not todo:
        return False
    try:
        import yfinance as yf
    except ImportError:
        log.info("Stock metrics skipped: run 'pip install yfinance' to enable them.")
        return False
    log.info("Refreshing stock metrics for %d companies...", len(todo))
    for r in todo:
        try:
            t = yf.Ticker(r["ticker"])
            info = t.info or {}
            change = info.get("52WeekChange")
            if change is None:
                hist = t.history(period="1y")
                if len(hist) > 1:
                    change = float(hist["Close"].iloc[-1] / hist["Close"].iloc[0] - 1)
            cap, rg, pm = info.get("marketCap"), info.get("revenueGrowth"), info.get("profitMargins")
            r["market_cap_b"] = f"{cap / 1e9:.1f}" if cap else ""
            r["stock_12m_pct"] = f"{change * 100:.1f}" if change is not None else ""
            r["revenue_growth_pct"] = f"{rg * 100:.1f}" if rg is not None else ""
            r["profit_margin_pct"] = f"{pm * 100:.1f}" if pm is not None else ""
            r["metrics_updated"] = today.isoformat()
        except Exception as exc:
            log.warning("Metrics for %s (%s) failed: %s", r["company"], r["ticker"], exc)
        time.sleep(cfg.get("metrics_delay_seconds", 0.5))
    return True


def rescore(rows):
    changed = False
    for r in rows:
        score, tier = score_row(r)
        if (str(r.get("score", "")), r.get("tier", "")) != (str(score), tier):
            r["score"], r["tier"] = str(score), tier
            changed = True
    return changed


# ----------------------------------------------------------------- filtering
class Rules:
    def __init__(self, cfg):
        self.title_in = compile_terms(cfg["titles_include"])
        self.title_out = compile_terms(cfg["titles_exclude"])
        self.stack = compile_terms(cfg["stack_keywords"])
        self.loc_out = compile_terms(cfg.get("locations_exclude", []))
        self.regions = [(r["name"], compile_terms(r["match"])) for r in cfg["regions"]]
        self.require_stack = cfg.get("require_stack_match", True)
        hours = cfg.get("max_posted_age_hours")
        if hours is None and cfg.get("max_posted_age_days"):
            hours = cfg["max_posted_age_days"] * 24
        self.max_age = dt.timedelta(hours=hours if hours is not None else 24)

    def title_ok(self, title):
        return any_match(self.title_in, title) and not any_match(self.title_out, title)

    def region_for(self, locations):
        kept = [l for l in locations if l and not any_match(self.loc_out, l)]
        for name, matchers in self.regions:
            if any(any_match(matchers, l) for l in kept):
                return name
        return None

    def stack_for(self, title, text):
        return all_matches(self.stack, f"{title}\n{text or ''}")


def level_for(title):
    if re.search(r"(?<![A-Za-z])(Senior|Sr\.?|Lead)(?![A-Za-z])", title, re.I):
        return "Senior/Lead"
    if re.search(r"(?<![A-Za-z0-9])(II|2|Intermediate|Level 2)(?![A-Za-z0-9])", title):
        return "Mid"
    return "Unlabelled"


def fetch_board(row, state, rules, has_text, now, max_age, window_start, known_boards, detail_cap):
    """Runs in a worker thread. Downloads one board's full list (normally a single request)
    and returns every posting; filtering happens later, locally, against the saved list.

    Workday and SmartRecruiters lists carry no descriptions, so a description is fetched
    only for postings that could match (right title, fresh enough, in one of your regions)
    and only once: it is saved and never requested again."""
    entry = entry_for(row)
    careers = row.get("careers_url") if SETTINGS["career_sites"] else ""
    hint = (row.get("ats") or "").strip().lower()
    only = ATS_HINTS.get(hint.split()[0]) if hint else None  # unknown hint: guess every board
    try:
        loaded = site = None
        if (careers and not entry.startswith("website:") and entry not in state["resolved"]
                and (row.get("board") or "auto").lower() in ("auto", "")):
            # A company with no known board: its careers page usually names the board, which
            # is far cheaper than guessing board names. A careers page with readable jobs
            # needs no guessing at all.
            site = load_company(f"website:{careers}", state)
            if site and (site[0] != "website" or site[1].get("jobs")):
                loaded = site
        guessing = entry not in state["resolved"] and (
            ":" not in entry or (entry.startswith("workday:") and not parse_workday(entry[8:])[2]))
        guesses = state.setdefault("board_guesses", {})
        last_guess = parse_date(guesses.get(row["company"]))
        may_guess = (not guessing or SETTINGS["guess_every"] is None or last_guess is None
                     or now - last_guess >= SETTINGS["guess_every"])
        if loaded is None and may_guess:
            loaded = load_company(entry, state, only)
            if guessing:
                # Spread the daily retries over the day instead of doing them all in one run.
                spread = (zlib.crc32(row["company"].encode()) % 600) / 1000 * (SETTINGS["guess_every"] or dt.timedelta(0))
                guesses[row["company"]] = (now - spread).isoformat()
            if loaded is not None:
                guesses.pop(row["company"], None)
        if loaded is None and careers and not entry.startswith("website:"):
            loaded = site or load_company(f"website:{careers}", state)  # no board: read the website
    except RateLimited as exc:
        return {"row": row, "deferred": str(exc)}
    except Exception as exc:
        return {"row": row, "error": exc}
    if loaded is None:
        return {"row": row, "missing": True}
    provider, res = loaded
    first_check = f"{provider}:{res['slug']}" not in known_boards

    def worth_a_detail_request(job):
        if not rules.title_ok(job["title"]):
            return False
        posted = job.get("posted")
        if posted is None and first_check:
            return False  # undated and already up the first time this board was read
        if posted is not None and (now - posted > max_age or (window_start and posted < window_start)):
            return False
        return job.get("ambiguous_location") or rules.region_for(job["locations"]) is not None

    details = 0
    postings = []
    for job in res["jobs"]:
        if (job.get("text") is None and job.get("load_detail") and job["key"] not in has_text
                and details < detail_cap and worth_a_detail_request(job)):
            details += 1
            try:
                d = job["load_detail"]() or {}
            except RateLimited:
                d, detail_cap = {}, 0  # stop asking this site for details until next run
            except Exception:
                d = {}
            if d.get("locations"):
                job["locations"] = d["locations"]
            if "text" in d:
                job["text"] = d["text"]
            job["url"] = d.get("url") or job["url"]
            job["posted"] = job.get("posted") or d.get("posted")
        postings.append({k: job.get(k) for k in ("key", "title", "locations", "url", "posted", "text")})
    meta = {k: v for k, v in res.items() if k != "jobs"}
    return {"row": row, "provider": provider, "res": meta, "postings": postings, "details": details}


# ----------------------------------------------------------------- local postings store
class PostingStore:
    """Every posting from every board, kept locally in SQLite. Filtering reads from here,
    so changing your rules or re-running the filters never touches the job boards."""

    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS postings (
                key TEXT PRIMARY KEY, board TEXT, company TEXT, provider TEXT, title TEXT,
                locations TEXT, url TEXT, posted TEXT, first_seen TEXT, last_seen TEXT,
                closed TEXT, open INTEGER, baseline INTEGER, text BLOB);
            CREATE INDEX IF NOT EXISTS postings_board ON postings(board);
            CREATE INDEX IF NOT EXISTS postings_open ON postings(open);""")

    def keys_with_text(self):
        return {r[0] for r in self.db.execute("SELECT key FROM postings WHERE text IS NOT NULL")}

    def boards(self):
        return {r[0] for r in self.db.execute("SELECT DISTINCT board FROM postings")}

    def save_board(self, board, company, provider, postings, now, complete, first_time):
        stamp = now.isoformat()
        existing = {r["key"]: r for r in self.db.execute(
            "SELECT key, locations, text IS NOT NULL AS has_text FROM postings WHERE board = ?", (board,))}
        new = 0
        for p in postings:
            locations = json.dumps(p.get("locations") or [])
            posted = p["posted"].isoformat() if p.get("posted") else None
            old = existing.get(p["key"])
            text = None
            if p.get("text") is not None and not (old and old["has_text"]):
                text = zlib.compress(p["text"][:50000].encode("utf-8"))
            if old:
                if not p.get("locations"):
                    locations = old["locations"]  # keep locations learned from a detail page
                self.db.execute(
                    "UPDATE postings SET company=?, provider=?, title=?, locations=?, url=?,"
                    " posted=COALESCE(posted, ?), last_seen=?, open=1, closed=NULL,"
                    " text=COALESCE(text, ?) WHERE key=?",
                    (company, provider, p["title"], locations, p["url"], posted, stamp, text, p["key"]))
            else:
                new += 1
                self.db.execute(
                    "INSERT OR REPLACE INTO postings (key, board, company, provider, title, locations, url,"
                    " posted, first_seen, last_seen, open, baseline, text) VALUES (?,?,?,?,?,?,?,?,?,?,1,?,?)",
                    (p["key"], board, company, provider, p["title"], locations, p["url"], posted,
                     stamp, stamp, 1 if first_time else 0, text))
        if complete:  # a full list proves anything missing from it was taken down
            self.db.execute("UPDATE postings SET open=0, closed=? WHERE board=? AND open=1 AND last_seen<?",
                            (stamp, board, stamp))
        self.db.commit()
        return new

    def open_postings(self):
        return self.db.execute(
            "SELECT key, board, company, provider, title, locations, url, posted, first_seen, baseline,"
            " text IS NOT NULL AS has_text FROM postings WHERE open=1").fetchall()

    def text_of(self, key):
        row = self.db.execute("SELECT text FROM postings WHERE key=?", (key,)).fetchone()
        return zlib.decompress(row[0]).decode("utf-8") if row and row[0] is not None else None

    def status_of(self, keys):
        found = {}
        keys = list(keys)
        for i in range(0, len(keys), 500):
            chunk = keys[i:i + 500]
            q = f"SELECT key, open FROM postings WHERE key IN ({','.join('?' * len(chunk))})"
            found.update({r["key"]: bool(r["open"]) for r in self.db.execute(q, chunk)})
        return found

    def counts(self):
        r = self.db.execute("SELECT COUNT(*), SUM(open) FROM postings").fetchone()
        return r[0] or 0, r[1] or 0

    def export_csv(self, path, tracked):
        """The 'all postings' list: every open posting, newest first, readable in Excel."""
        rows = self.db.execute(
            "SELECT first_seen, posted, company, title, locations, provider, url, key FROM postings"
            " WHERE open=1 ORDER BY COALESCE(posted, first_seen) DESC").fetchall()
        tmp = path.with_name(path.stem + ".saving.csv")
        try:
            with open(tmp, "w", newline="", encoding="utf-8-sig") as f:
                w = csv.writer(f)
                w.writerow(["First seen", "Posted", "Company", "Title", "Location", "Board",
                            "In your tracker", "Link"])
                for r in rows:
                    first, posted = to_local_naive(r["first_seen"]), to_local_naive(r["posted"])
                    w.writerow([f"{first:%Y-%m-%d %H:%M}" if first else "",
                                f"{posted:%Y-%m-%d}" if posted else "", r["company"], r["title"],
                                "; ".join(json.loads(r["locations"] or "[]")), r["provider"],
                                "yes" if r["key"] in tracked else "", r["url"]])
            os.replace(tmp, path)
        except PermissionError:
            log.warning("%s is open in another program; it will be updated next run.", path.name)
            tmp.unlink(missing_ok=True)

    def close(self):
        self.db.close()


def find_new_matches(store, state, rules, now, max_age, window_start):
    """The local filter: checks every saved open posting against your rules. No requests."""
    matches = []
    for r in store.open_postings():
        key = r["key"]
        if key in state["jobs"] or not rules.title_ok(r["title"]):
            continue
        posted = parse_date(r["posted"])
        # Undated postings count from when they first appeared, unless they were already
        # up the first time their board was read.
        appeared = posted or (None if r["baseline"] else parse_date(r["first_seen"]))
        if appeared is None or now - appeared > max_age or (window_start and appeared < window_start):
            continue
        locations = json.loads(r["locations"] or "[]")
        region = rules.region_for(locations)
        if not region:
            continue
        text = store.text_of(key) if r["has_text"] else None
        if text is None and rules.require_stack:
            continue  # description not downloaded yet; it is fetched on the next run
        stack = rules.stack_for(r["title"], text)
        if rules.require_stack and not stack:
            continue
        matches.append({"key": key, "company": r["company"], "provider": r["provider"], "title": r["title"],
                        "locations": locations, "region": region, "stack": stack, "url": r["url"],
                        "posted": posted})
    return matches


# ----------------------------------------------------------------- finding new companies
# A web search for fresh postings on the job-board sites, the same as typing
#   site:jobs.ashbyhq.com (intitle:"Software Engineer" OR ...) ("Toronto" OR ...) -India
# into Google. Each result link names the company's board, and any board you are not
# already watching is added to companies.csv. From then on the whole board is read through
# its own feed, which gives exact posting dates and full descriptions.
SEARCH_SITES = {"ashby": "jobs.ashbyhq.com", "greenhouse": "greenhouse.io", "lever": "jobs.lever.co",
                "workable": "apply.workable.com", "smartrecruiters": "jobs.smartrecruiters.com",
                "workday": "myworkdayjobs.com"}
SEARCH_KEYS = {"serper": "SERPER_API_KEY", "brave": "BRAVE_API_KEY", "serpapi": "SERPAPI_API_KEY"}
SEARCH_PAGE_SIZE = {"serper": 10, "brave": 20, "serpapi": 10}
# look_back -> (Google tbs value, Brave freshness value)
LOOK_BACK = {"day": ("qdr:d", "pd"), "week": ("qdr:w", "pw"), "month": ("qdr:m", "pm"), "any": (None, None)}
NOT_CAREER_SITES = ("linkedin.", "indeed.", "glassdoor.", "ziprecruiter.", "wikipedia.", "crunchbase.",
                    "builtin", "wellfound.", "angel.co", "monster.", "simplyhired.", "workopolis.",
                    "jobbank.gc.ca", "facebook.", "twitter.", "x.com", "instagram.", "youtube.",
                    "bloomberg.", "levels.fyi", "teamblind.", "comparably.", "zoominfo.", "rocketreach.",
                    "theorg.com", "welcometothejungle.", "ycombinator.com", "reddit.", "talent.com",
                    "jooble.", "careerbuilder.", "dice.com", "eluta.ca", "jobillico.", "workingnomads",
                    "remoteok", "weworkremotely")


class SearchError(Exception):
    """The search service refused or failed; the message says why."""


def build_query(host, titles, places, skip_words):
    def quoted(t):
        return '"' + str(t).replace('"', "").strip() + '"'
    parts = [f"site:{host}"]
    if titles:
        parts.append("(" + " OR ".join(f"intitle:{quoted(t)}" for t in titles) + ")")
    if places:
        parts.append("(" + " OR ".join(quoted(p) for p in places) + ")")
    for w in skip_words:
        w = str(w).strip()
        if w:
            parts.append("-" + (quoted(w) if " " in w else w))
    return " ".join(parts)


def search_page(provider, key, query, page, look_back, country=""):
    """One page of results as [(url, title)]."""
    tbs, freshness = LOOK_BACK[look_back]
    endpoint = {"serper": "https://google.serper.dev/search",
                "brave": "https://api.search.brave.com/res/v1/web/search",
                "serpapi": "https://serpapi.com/search.json"}[provider]
    LIMITER.wait(urlparse(endpoint).hostname)
    try:
        if provider == "serper":
            body = {"q": query, "num": 10, "page": page}
            if tbs:
                body["tbs"] = tbs
            if country:
                body["gl"] = country
            r = session().post(endpoint, json=body, timeout=30,
                               headers={"X-API-KEY": key, "Content-Type": "application/json"})
        elif provider == "brave":
            params = {"q": query, "count": 20, "offset": page - 1}
            if freshness:
                params["freshness"] = freshness
            if country:
                params["country"] = country
            r = session().get(endpoint, params=params, timeout=30,
                              headers={"X-Subscription-Token": key, "Accept": "application/json"})
        else:
            params = {"engine": "google", "q": query, "num": 10, "start": (page - 1) * 10, "api_key": key}
            if tbs:
                params["tbs"] = tbs
            if country:
                params["gl"] = country
            r = session().get(endpoint, params=params, timeout=45)
    except requests.RequestException as exc:
        raise SearchError(f"could not reach {provider} ({exc.__class__.__name__})") from None
    if r.status_code in (401, 403):
        raise SearchError(f"{provider} rejected the API key; check {SEARCH_KEYS[provider]} in .env")
    if r.status_code in (402, 429):
        raise SearchError(f"{provider} says the plan's query limit or rate limit was reached")
    if r.status_code >= 400:
        raise SearchError(f"{provider} answered HTTP {r.status_code}: {r.text[:150]}")
    try:
        data = r.json()
    except ValueError:
        raise SearchError(f"{provider} sent a reply that is not JSON") from None
    if provider == "serper":
        items = [(o.get("link"), o.get("title", "")) for o in data.get("organic") or []]
    elif provider == "brave":
        items = [(o.get("url"), o.get("title", "")) for o in (data.get("web") or {}).get("results") or []]
    else:
        if data.get("error") and "hasn't returned any results" not in data["error"]:
            raise SearchError(f"serpapi: {data['error']}")
        items = [(o.get("link"), o.get("title", "")) for o in data.get("organic_results") or []]
    return [(u, html.unescape(t or "")) for u, t in items if u]


def norm_name(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def company_name_for(provider, slug, title=""):
    """A readable company name: from the result title when it agrees with the slug,
    otherwise from the slug ('rose-rocket' -> 'Rose Rocket')."""
    if provider == "workday":
        base = parse_workday(slug)[0].replace("-", " ").title()
        key = norm_name(base)
    else:
        base, key = display_name(slug), norm_name(slug)
    t = " ".join((title or "").split())
    m = {"greenhouse": re.search(r"\bat\s+(.+?)\s*$", t),
         "ashby": re.search(r"@\s*(.+?)\s*$", t),
         "lever": re.match(r"(.+?)\s+[-|]\s+", t),
         "workable": re.search(r"[-|]\s+([^-|]+?)\s*$", t),
         "smartrecruiters": re.search(r"[-|]\s+([^-|]+?)\s*$", t)}.get(provider)
    guess = m.group(1).strip(" -|") if m else ""
    if guess and len(guess) <= 60 and key and (key in norm_name(guess) or norm_name(guess) in key):
        return guess
    return base


def _append_note(old, text):
    old = (old or "").strip()
    return f"{old}; {text}" if old else text


def merge_found_boards(state, rows, found, today, mode="watch", region="Found by search",
                       how="search"):
    """Add boards to the company list. found: [(provider, slug, title, link)].
    A board already watched is skipped. A company already listed without a working board
    is given this one. Anything else becomes a new row. Returns (added, updated) rows."""
    by_board, by_name = {}, {}
    for r in rows:
        b = (r.get("board") or "").lower()
        if b in FETCHERS and b != "website" and r.get("slug"):
            by_board[board_key(b, r["slug"])] = r
        if r.get("region") == "From config file":
            continue  # lives in config.toml, not companies.csv
        by_name.setdefault(norm_name(r["company"]), r)
        if r.get("slug") and b not in ("workday", "website"):
            by_name.setdefault(norm_name(r["slug"]), r)
    for c in state.get("resolved", {}).values():  # boards found earlier by auto-detection
        by_board.setdefault(board_key(c["provider"], c["slug"]), None)

    names_taken = {norm_name(r["company"]) for r in rows}
    added, updated = [], []
    for provider, slug, title, link in found:
        k = board_key(provider, slug)
        if k in by_board:
            continue
        name = company_name_for(provider, slug, title)
        r = by_name.get(norm_name(name)) or (by_name.get(norm_name(slug)) if provider != "workday" else None)
        if r is not None:
            b = (r.get("board") or "").lower()
            has_board = (b in FETCHERS and b != "website" and r.get("slug")
                         and r.get("status") != "not_found")
            by_board[k] = r
            if has_board:
                continue  # already watched on another board
            r.update(board=provider, slug=slug, status="", verified="")
            r["notes"] = _append_note(r.get("notes"), f"{provider.title()} board found by {how} {today}")
            state.get("companies", {}).pop(r["company"], None)  # forget an old NOT FOUND
            updated.append(r)
            continue
        if norm_name(name) in names_taken:
            name = f"{name} ({provider.title()})"
        row = {"priority": "8", "region": region, "company": name, "board": provider, "slug": slug,
               "enabled": "no" if mode == "review" else "yes",
               "notes": f"Found by {how} {today}" + (f": {title}" if title else "")}
        row["notes"] = row["notes"][:200]
        rows.append(row)
        added.append(row)
        by_board[k] = row
        by_name[norm_name(name)] = row
        names_taken.add(norm_name(name))
    return added, updated


def _search_key(cfg):
    provider = cfg["search"]["provider"]
    key = os.environ.get(SEARCH_KEYS[provider], "").strip()
    if not key:
        log.warning("Web search skipped: %s is not set. Put it in a .env file next to job_watcher.py "
                    "(see .env.example), or set enabled = false under [search] in config.toml.",
                    SEARCH_KEYS[provider])
    return provider, key


def _queries_left(cfg, state, now):
    month = now.astimezone().strftime("%Y-%m")
    used = state.setdefault("search", {}).setdefault("queries_by_month", {})
    for old in sorted(m for m in used if m < month)[:-2]:
        used.pop(old, None)  # keep a few months of history
    return int(cfg["search"]["monthly_query_limit"]) - used.get(month, 0), month, used


def search_due(cfg, state, now):
    s = cfg["search"]
    if not s["enabled"]:
        return False
    last = parse_date(state.get("search", {}).get("last_run"))
    gap = dt.timedelta(hours=24 / max(0.1, float(s["runs_per_day"]))) - dt.timedelta(minutes=10)
    return last is None or now - last >= gap


def run_search(cfg, state, rows, now, look_back=None):
    """Search for fresh postings and add their companies. Returns True if companies.csv changed."""
    s = cfg["search"]
    provider, key = _search_key(cfg)
    if not key:
        return False
    left, month, used = _queries_left(cfg, state, now)
    if left <= 0:
        log.info("Web search skipped: this month's limit of %s queries is used up "
                 "(monthly_query_limit in config.toml).", s["monthly_query_limit"])
        return False
    look_back = look_back or s["look_back"]
    found, queries, failed = {}, 0, None
    for site in s["sites"]:
        query = build_query(SEARCH_SITES[site.lower()], s["titles"], s["places"], s["skip_words"])
        for page in range(1, int(s["pages_per_query"]) + 1):
            if queries >= left or failed:
                break
            try:
                results = search_page(provider, key, query, page, look_back, s.get("country", ""))
            except SearchError as exc:
                failed = str(exc)
                break
            queries += 1
            for link, title in results:
                b = board_from_url(link)
                if b:
                    found.setdefault(board_key(*b), (b[0], b[1], title, link))
            if len(results) < SEARCH_PAGE_SIZE[provider]:
                break
    used[month] = used.get(month, 0) + queries
    if failed:
        log.warning("Web search stopped: %s.", failed)
    if queries:
        state["search"]["last_run"] = now.isoformat()
    today = now.astimezone().date().isoformat()
    added, updated = merge_found_boards(state, rows, list(found.values()), today,
                                        mode=s["new_companies"])
    log.info("Web search (%s, past %s): %d queries, %d used in %s of %s allowed. %d job boards in "
             "the results, %d new compan%s added, %d listed compan%s given a board.",
             provider, look_back, queries, used[month], month, s["monthly_query_limit"], len(found),
             len(added), "y" if len(added) == 1 else "ies", len(updated), "y" if len(updated) == 1 else "ies")
    for r in added:
        log.info("  ADDED  %s (%s: %s)%s", r["company"], r["board"], r["slug"],
                 "  [enabled = no, for review]" if r["enabled"] == "no" else "")
    for r in updated:
        log.info("  BOARD  %s now uses %s: %s", r["company"], r["board"], r["slug"])
    return bool(added or updated)


def looks_like_company_site(link, company):
    host = (urlparse(link).hostname or "").lower()
    if not host or any(bad in host for bad in NOT_CAREER_SITES):
        return False
    squashed = norm_name(host)
    words = [w for w in re.findall(r"[a-z0-9]+", company.lower()) if len(w) >= 3 and w not in
             {"inc", "ltd", "the", "and", "technologies", "technology", "labs", "software", "systems",
              "group", "corp", "company", "canada", "global", "digital", "solutions", "health"}]
    return norm_name(company) in squashed or any(w in squashed for w in words)


def find_career_pages(cfg, state, rows, now):
    """For companies with no job board found and no careers_url, search '"Company" careers'
    and record the careers page (or the job board it points to). Returns True if rows changed."""
    provider, key = _search_key(cfg)
    if not key:
        return False
    limit = int(cfg["search"]["career_page_lookups_per_run"])
    left, month, used = _queries_left(cfg, state, now)
    tried = state.setdefault("career_page_lookups", {})
    today = now.astimezone().date().isoformat()
    todo = []
    for r in rows:
        board = (r.get("board") or "auto").lower()
        unresolved = (board in ("auto", "") or r.get("status") == "not_found"
                      or state["companies"].get(r["company"], {}).get("result") == "NOT FOUND")
        last = parse_date(tried.get(r["company"]))
        if (is_enabled(r) and unresolved and not r.get("careers_url") and board not in MANUAL_BOARDS
                and r.get("region") != "From config file" and not (last and now - last < dt.timedelta(days=60))):
            todo.append(r)
    todo = todo[:max(0, min(limit, left))]
    if not todo:
        log.info("Career page lookup: nothing to look up (or this month's query limit is used up).")
        return False
    log.info("Looking up careers pages for %d companies with no job board found...", len(todo))
    changed, queries = False, 0
    for r in todo:
        try:
            results = search_page(provider, key, f'"{r["company"]}" careers jobs', 1, "any",
                                  cfg["search"].get("country", ""))
        except SearchError as exc:
            log.warning("Career page lookup stopped: %s.", exc)
            break
        queries += 1
        tried[r["company"]] = today
        hit = None
        for link, title in results[:5]:
            b = board_from_url(link)
            if b:
                hit = ("board", b, link)
                break
            if looks_like_company_site(link, r["company"]):
                hit = ("site", None, link)
                break
        if hit is None:
            log.info("  NONE   %s: no confident match", r["company"])
            continue
        if hit[0] == "board":
            r.update(board=hit[1][0], slug=hit[1][1], status="", verified="")
            log.info("  BOARD  %s: %s %s", r["company"], hit[1][0], hit[1][1])
        else:
            r.update(careers_url=clean_url(hit[2]), board="website", slug="", status="", verified="")
            log.info("  PAGE   %s: %s", r["company"], r["careers_url"])
        r["notes"] = _append_note(r.get("notes"), f"careers page found by search {today}")
        state["companies"].pop(r["company"], None)
        changed = True
    used[month] = used.get(month, 0) + queries
    return changed


def add_company(cfg, link, name=None):
    """--add: watch a company from any job posting, board or careers page link."""
    now = dt.datetime.now(dt.timezone.utc)
    today = now.astimezone().date().isoformat()
    state = load_json(STATE_PATH, {"jobs": {}, "resolved": {}, "companies": {}})
    state.setdefault("companies", {})
    rows = load_company_rows(cfg)
    b = board_from_url(link)
    if b:
        added, updated = merge_found_boards(state, rows, [(b[0], b[1], "", link)], today,
                                            region="Added by you", how="you")
        if added and name:
            added[0]["company"] = name
        if not (added or updated):
            print(f"Already watching that board ({b[0]}: {b[1]}).")
            return
        r = (added or updated)[0]
        print(f"{'Added' if added else 'Updated'} {r['company']}: {r['board']} board '{r['slug']}'.")
    else:
        if "://" not in link:
            link = "https://" + link
        host = (urlparse(link).hostname or "").lower()
        label = name or next((p for p in host.split(".") if p not in ("www", "careers", "jobs", "join",
                                                                       "work", "apply", "boards")),
                             host).replace("-", " ").title()
        existing = next((r for r in rows if norm_name(r["company"]) == norm_name(label)
                         and r.get("region") != "From config file"), None)
        if existing:
            no_board = ((existing.get("board") or "auto").lower() in ("auto", "", "other")
                        or existing.get("status") != "ok")
            existing.update(careers_url=clean_url(link), status="", verified="")
            if no_board:
                existing.update(board="website", slug="")
            state["companies"].pop(existing["company"], None)
            print(f"Updated {existing['company']}: careers page {existing['careers_url']}.")
        else:
            rows.append({"priority": "8", "region": "Added by you", "company": label, "board": "website",
                         "careers_url": clean_url(link), "enabled": "yes", "notes": f"Added by you {today}"})
            print(f"Added {label}: careers page {clean_url(link)}. "
                  "The next run checks whether it uses a job board and reads it.")
    save_company_rows(cfg, rows)
    save_json(STATE_PATH, state)
    print(f"Saved to {companies_path(cfg).name}. Run the watcher to read it now.")


# ----------------------------------------------------------------- importing company lists
# --import reads any spreadsheet (.xlsx or .csv) that has a "Company" column, and ideally a
# careers page column, and adds every company you are not watching yet to companies.csv.
AGGREGATOR_HOSTS = NOT_CAREER_SITES + ("communitech.", "edtech.com", "builtinvancouver", "builtintoronto")
NAME_STOPWORDS = {"digital", "canada", "remote", "us", "usa", "inc", "the"}


def name_keys(name):
    """Every name a company might be listed under: 'Jane (Jane App)' -> jane, janeapp."""
    base = re.sub(r"\s*\(.*?\)\s*", " ", name).strip()
    keys = {norm_name(name), norm_name(base)}
    keys |= {norm_name(p) for p in re.findall(r"\((.*?)\)", name) if p.strip().lower() not in NAME_STOPWORDS}
    if " / " in base:
        keys |= {norm_name(x) for x in base.split(" / ")}
    stripped = re.sub(r"\b(inc|ltd|llc|corp|corporation|limited|technologies|technology|software|labs|group)\b\.?",
                      "", base, flags=re.I)
    keys.add(norm_name(stripped))
    return {k for k in keys if len(k) >= 2}


def site_domain(url):
    host = (urlparse(url if "://" in (url or "") else "https://" + (url or "")).hostname or "").lower()
    return _site_of(host) if host and "." in host else ""


def guess_region(text):
    t = f" {text or ''} "
    checks = [("Vancouver / BC", r"Vancouver|\bBC\b|British Columbia|Burnaby|Victoria|Richmond|Surrey|Kelowna|"
                                 r"Kamloops|Langley|Coquitlam|New Westminster"),
              ("Toronto / GTA", r"Toronto|\bGTA\b|Mississauga|Markham|Kitchener|Waterloo|Guelph|Hamilton|Oakville"),
              ("Ottawa", r"Ottawa"),
              ("Seattle", r"Seattle|Bellevue|Redmond|Kirkland|,\s*WA\b"),
              ("California", r"California|,\s*CA\b|San Francisco|Bay Area|Los Angeles|San Diego|Palo Alto|San Jose"),
              ("Rest of Canada", r"Canada|Montr[eé]al|Calgary|Edmonton|Halifax|Winnipeg|Quebec|Ontario|Alberta")]
    for region, pattern in checks:
        if re.search(pattern, t, re.I):
            return region
    return "Remote / other US"


def _sheet_rows(path):
    """(sheet name, header, rows) for every sheet or CSV that has a Company column."""
    if path.suffix.lower() == ".csv":
        with open(path, newline="", encoding="utf-8-sig") as f:
            tables = [(path.stem, list(csv.reader(f)))]
    else:
        wb = load_workbook(path, read_only=True, data_only=True)
        tables = [(ws.title, [list(r) for r in ws.iter_rows(values_only=True)]) for ws in wb.worksheets]
        wb.close()
    out = []
    for title, rows in tables:
        for i, row in enumerate(rows[:15]):
            cells = [str(c or "").strip().lower() for c in row]
            if "company" in cells:
                header = [str(c or "").strip() for c in row]
                body = [r for r in rows[i + 1:] if r and any(c not in (None, "") for c in r)]
                out.append((title, header, body))
                break
    return out


def _column(header, *words, avoid=()):
    for i, h in enumerate(header):
        low = h.lower()
        if any(w in low for w in words) and not any(a in low for a in avoid):
            return i
    return None


def import_companies(cfg, paths):
    """--import: add companies from spreadsheets to companies.csv. Returns (added, updated)."""
    rows = load_company_rows(cfg)
    file_rows = [r for r in rows if r.get("region") != "From config file"]
    by_key, by_site, by_board = {}, {}, {}
    for r in file_rows:
        for k in name_keys(r["company"]):
            by_key.setdefault(k, r)
        if r.get("slug") and (r.get("board") or "").lower() in FETCHERS:
            by_board[board_key(r["board"], r["slug"])] = r
            by_key.setdefault(norm_name(r["slug"]), r)
        if site_domain(r.get("careers_url")) and not any(a in r.get("careers_url", "") for a in AGGREGATOR_HOSTS):
            by_site.setdefault(site_domain(r["careers_url"]), r)
    priority_of_region = {}
    for r in file_rows:
        priority_of_region.setdefault(r.get("region", ""), Counter())[priority_of(r)] += 1
    today = dt.date.today().isoformat()
    added, updated, seen = [], [], 0
    for path in paths:
        path = Path(path)
        if not path.exists():
            raise ConfigError(f"{path} not found")
        for title, header, body in _sheet_rows(path):
            ci = header.index(next(h for h in header if h.lower() == "company"))
            ui = _column(header, "careers url", "careers / job", "career", "job board", "url", "link",
                         avoid=("role title", "job link"))
            ai = _column(header, "ats")
            loc_i = _column(header, "location", "geography", "hq", "city")
            info = [i for i in (_column(header, "sector", "industry", "category"),
                                _column(header, "remote policy", "work model"),
                                _column(header, "hires in canada", "work auth"),
                                _column(header, "stack")) if i is not None]
            for raw in body:
                cell = str(raw[ci] or "").strip() if ci < len(raw) else ""
                if not cell or cell.lower() in ("company", "example"):
                    continue
                names = [x.strip() for x in cell.split(" / ")] if cell.count(" / ") >= 2 else [cell]
                url = str(raw[ui] or "").strip() if ui is not None and ui < len(raw) else ""
                url = url if url.startswith("http") and not any(a in url for a in AGGREGATOR_HOSTS) else ""
                ats = str(raw[ai] or "").strip() if ai is not None and ai < len(raw) else ""
                place = str(raw[loc_i] or "").strip() if loc_i is not None and loc_i < len(raw) else ""
                extra = "; ".join(str(raw[i]).strip() for i in info if i < len(raw) and raw[i])
                for name in names:
                    seen += 1
                    board = board_from_url(url) if url and len(names) == 1 else None
                    match = (by_board.get(board_key(*board)) if board else None) \
                        or next((by_key[k] for k in name_keys(name) if k in by_key), None) \
                        or (by_site.get(site_domain(url)) if url and not board and len(names) == 1 else None)
                    if match is not None:
                        changed = False
                        if url and not board and not match.get("careers_url"):
                            match["careers_url"] = url
                            changed = True
                        if board and (match.get("board") or "auto").lower() in ("auto", "", "other") \
                                and match.get("status") != "ok":
                            match.update(board=board[0], slug=board[1], status="")
                            changed = True
                        if ats and not match.get("ats"):
                            match["ats"] = ats.lower()
                            changed = True
                        if changed and match not in updated and match not in added:
                            updated.append(match)
                        continue
                    region = guess_region(place) if place else "Rest of Canada"
                    counts = priority_of_region.get(region)
                    row = {"priority": str(counts.most_common(1)[0][0]) if counts else "7", "region": region,
                           "company": name, "board": board[0] if board else "auto",
                           "slug": board[1] if board else "", "careers_url": "" if board else url,
                           "ats": ats.lower(), "hubs": place[:80], "enabled": "yes",
                           "notes": f"Imported {today} from {path.name} ({title})" + (f": {extra}" if extra else "")}
                    row["notes"] = row["notes"][:220]
                    rows.append(row)
                    added.append(row)
                    for k in name_keys(name):
                        by_key.setdefault(k, row)
                    if board:
                        by_board[board_key(*board)] = row
                    elif url:
                        by_site.setdefault(site_domain(url), row)
    save_company_rows(cfg, rows)
    print(f"Read {seen} company rows. Added {len(added)} new companies and filled in missing details "
          f"for {len(updated)} you already had. Saved to {companies_path(cfg).name}.")
    regions = Counter(r["region"] for r in added)
    if regions:
        print("New companies by region: " + ", ".join(f"{k} {v}" for k, v in regions.most_common()))
    return added, updated


# ----------------------------------------------------------------- state
def load_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default
    except json.JSONDecodeError:
        log.warning("%s is corrupted; starting fresh", path.name)
        return default


def save_json(path, data):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    os.replace(tmp, path)


def to_local_naive(iso):
    d = parse_date(iso)
    return d.astimezone().replace(tzinfo=None) if d else None


# ----------------------------------------------------------------- excel
COLUMNS = [("Found", 17), ("Posted", 11), ("Company", 20), ("Score", 7), ("Title", 46),
           ("Location", 34), ("Region", 22), ("Level", 12), ("Stack match", 28), ("Board", 14),
           ("Status", 14), ("Notes", 30), ("Outreach", 12), ("Posting", 9), ("Key", 10)]
HEADERS = [c for c, _ in COLUMNS]
COL = {name: i + 1 for i, name in enumerate(HEADERS)}
LETTER = {name: chr(64 + i) for name, i in COL.items()}


def fill(hex_color):
    return PatternFill("solid", start_color=hex_color, end_color=hex_color)


def header_row(ws):
    for cell in ws[ws.max_row]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = fill("1F3864")
        cell.alignment = Alignment(vertical="center")


def read_excel_edits(path):
    """Pull Status and Notes the user typed into the tracker, keyed by job key."""
    if not path.exists():
        return {}
    try:
        wb = load_workbook(path, read_only=True)
        rows = wb["Jobs"].iter_rows(values_only=True)
        header = list(next(rows))
        ki, si, ni = header.index("Key"), header.index("Status"), header.index("Notes")
        oi = header.index("Outreach") if "Outreach" in header else None  # older trackers lack it
        edits = {}
        for row in rows:
            if row and row[ki]:
                edits[row[ki]] = {"status": row[si] or "New", "notes": row[ni] or ""}
                if oi is not None:
                    edits[row[ki]]["outreach"] = (row[oi] or "").strip()
        wb.close()
        return edits
    except Exception as exc:
        log.warning("Could not read edits from %s (%s)", path.name, exc)
        return {}


def apply_excel_edits(state, edits, now):
    """Bring your Status, Notes and Outreach edits from the tracker into the saved state."""
    for key, edit in edits.items():
        j = state["jobs"].get(key)
        if not j:
            continue
        outreach = edit.pop("outreach", None)  # None when the tracker has no Outreach column
        j.update(edit)
        if outreach is None:
            continue
        old = j.get("outreach", "")
        if outreach == "Email" and old and old != "Email" and j.get("outreach_prepared"):
            continue  # a tracker saved before outreach.py ran; keep what outreach.py recorded
        if outreach == "Sent" and old != "Sent" and not j.get("outreach_sent"):
            j["outreach_sent"] = now.isoformat()  # starts the follow-up clock
        j["outreach"] = outreach


def write_excel(path, state, cfg, scores):
    region_colors = {r["name"]: r.get("color") for r in cfg["regions"]}
    jobs = sorted(state["jobs"].values(),
                  key=lambda j: (j["open"], j["found"], j.get("posted") or ""), reverse=True)

    def style_job_cells(ws, r, j, cols):
        if j.get("url"):
            ws.cell(r, cols["Title"]).hyperlink = j["url"]
            ws.cell(r, cols["Title"]).font = Font(color="0563C1", underline="single")
        if region_colors.get(j["region"]):
            ws.cell(r, cols["Region"]).fill = fill(region_colors[j["region"]])
        if LEVEL_COLORS.get(j["level"]):
            ws.cell(r, cols["Level"]).fill = fill(LEVEL_COLORS[j["level"]])
        tier = scores.get(j["company"], ("", ""))[1]
        if tier in TIER_COLORS:
            ws.cell(r, cols["Score"]).fill = fill(TIER_COLORS[tier])

    wb = Workbook()
    ws = wb.active
    ws.title = "Jobs"
    ws.append(HEADERS)
    header_row(ws)
    ws.row_dimensions[1].height = 22
    for j in jobs:
        score = scores.get(j["company"], ("", ""))[0]
        ws.append([
            to_local_naive(j["found"]), to_local_naive(j.get("posted")), j["company"],
            int(score) if str(score).isdigit() else None, j["title"],
            "; ".join(dict.fromkeys(j["locations"])), j["region"], j["level"], ", ".join(j["stack"]),
            j["provider"].title(), j.get("status", "New"), j.get("notes", ""), j.get("outreach", ""),
            "Open" if j["open"] else "Closed", j["key"],
        ])
        r = ws.max_row
        ws.cell(r, COL["Found"]).number_format = "yyyy-mm-dd hh:mm"
        ws.cell(r, COL["Posted"]).number_format = "yyyy-mm-dd"
        style_job_cells(ws, r, j, COL)

    last = max(ws.max_row, 2)
    for name, width in COLUMNS:
        ws.column_dimensions[LETTER[name]].width = width
    ws.column_dimensions[LETTER["Key"]].hidden = True
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:{LETTER['Posting']}{last}"
    S, F, P = LETTER["Status"], LETTER["Found"], LETTER["Posting"]

    dv = DataValidation(type="list", formula1='"' + ",".join(STATUSES) + '"', allow_blank=True)
    ws.add_data_validation(dv)
    dv.add(f"{S}2:{S}{last + 500}")
    O = LETTER["Outreach"]
    dv_out = DataValidation(type="list", formula1='"' + ",".join(OUTREACH) + '"', allow_blank=True)
    ws.add_data_validation(dv_out)
    dv_out.add(f"{O}2:{O}{last + 500}")
    for value, bg in OUTREACH_COLORS.items():
        ws.conditional_formatting.add(f"{O}2:{O}{last + 500}", FormulaRule(
            formula=[f'${O}2="{value}"'], fill=fill(bg)))
    ws.conditional_formatting.add(f"{F}2:{F}{last}", FormulaRule(
        formula=[f"AND(ISNUMBER(${F}2),NOW()-${F}2<1)"],
        fill=fill("C6EFCE"), font=Font(bold=True, color="006100")))
    for status, (bg, fg) in STATUS_COLORS.items():
        ws.conditional_formatting.add(f"{S}2:{S}{last + 500}", FormulaRule(
            formula=[f'${S}2="{status}"'], fill=fill(bg), font=Font(bold=True, color=fg)))
    ws.conditional_formatting.add(f"A2:{P}{last}", FormulaRule(
        formula=[f'OR(${S}2="Rejected",${S}2="Skipped",${P}2="Closed")'],
        font=Font(color="999999", italic=True)))

    # Since Last Check
    rep = state.get("last_check_report")
    if rep:
        sc = wb.create_sheet("Since Last Check", 0)
        start, end = to_local_naive(rep["from"]), to_local_naive(rep["to"])
        sc.append([f"Postings since {start:%Y-%m-%d %H:%M}  (checked {end:%Y-%m-%d %H:%M})"])
        sc["A1"].font = Font(bold=True, size=13, color="1F3864")
        sc.append([f"{len(rep['keys'])} matching posting(s). Track status on the Jobs tab; "
                   "this tab is rebuilt each time you run with --since-last-check."])
        sc["A2"].font = Font(italic=True, color="595959")
        sc.append([])
        sc_cols = ["Posted", "Found", "Company", "Score", "Title", "Location", "Region", "Level",
                   "Stack match"]
        sc.append(sc_cols)
        header_row(sc)
        cols = {n: i + 1 for i, n in enumerate(sc_cols)}
        for key in rep["keys"]:
            j = state["jobs"].get(key)
            if not j:
                continue
            score = scores.get(j["company"], ("", ""))[0]
            sc.append([to_local_naive(j.get("posted")), to_local_naive(j["found"]), j["company"],
                       int(score) if str(score).isdigit() else None, j["title"],
                       "; ".join(dict.fromkeys(j["locations"])), j["region"], j["level"],
                       ", ".join(j["stack"])])
            r = sc.max_row
            sc.cell(r, 1).number_format = sc.cell(r, 2).number_format = "yyyy-mm-dd hh:mm"
            style_job_cells(sc, r, j, cols)
        if not rep["keys"]:
            sc.append(["Nothing new matched your filters in this window."])
        for i, w in enumerate([17, 17, 20, 7, 46, 34, 22, 12, 28], start=1):
            sc.column_dimensions[chr(64 + i)].width = w
        sc.freeze_panes = "A5"
        wb.active = 0

    # Legend
    lg = wb.create_sheet("Legend")
    lg.append(["Colour", "Meaning"])
    header_row(lg)
    legend = [("C6EFCE", "Found column: discovered in the last 24 hours")]
    legend += [(TIER_COLORS["A"], "Score 65+: tier A, strong signals"),
               (TIER_COLORS["B"], "Score 50-64: tier B, mixed signals"),
               (TIER_COLORS["C"], "Score under 50: tier C, weak signals or recent layoffs")]
    legend += [(r.get("color"), f"Region: {r['name']}") for r in cfg["regions"] if r.get("color")]
    legend += [(c, f"Level: {lvl}") for lvl, c in LEVEL_COLORS.items() if c]
    legend += [(bg, f"Status: {s}") for s, (bg, _) in STATUS_COLORS.items()]
    legend += [(OUTREACH_COLORS["Email"], "Outreach: Email = you want a recruiter email prepared (run outreach.py)"),
               (OUTREACH_COLORS["Prepared"], "Outreach: Prepared = contacts found, ready for drafting"),
               (OUTREACH_COLORS["No contact"], "Outreach: No contact = no recruiter email found; see the packet for LinkedIn links"),
               (OUTREACH_COLORS["Sent"], "Outreach: Sent = you sent it (set this yourself)"),
               (OUTREACH_COLORS["Follow-up"], "Outreach: Follow-up = a follow-up has been prepared"),
               (OUTREACH_COLORS["Replied"], "Outreach: Replied = they answered (set this yourself)")]
    legend += [("FFFFFF", "Grey italic row: Rejected, Skipped, or posting no longer on the board")]
    for color, text in legend:
        lg.append(["", text])
        lg.cell(lg.max_row, 1).fill = fill(color)
    lg.column_dimensions["A"].width = 10
    lg.column_dimensions["B"].width = 70

    # Companies
    cs = wb.create_sheet("Companies")
    cs.append(["Priority", "Region", "Company", "Score", "Tier", "Board", "Slug", "Result",
               "Open postings", "Matches", "Last checked", "Note"])
    header_row(cs)
    ordered = sorted(state.get("companies", {}).items(),
                     key=lambda kv: (kv[1].get("priority", 9), kv[0].lower()))
    result_colors = {"OK": "C6EFCE", "NOT FOUND": "F8CBAD", "MANUAL": "DDEBF7", "RETRY": "FFE699",
                     "PENDING": "EDEDED", "ERROR": "F4B183", "NO JOBS READ": "FCE4D6"}
    for name, c in ordered:
        score, tier = scores.get(name, ("", ""))
        cs.append([c.get("priority", ""), c.get("region", ""), name,
                   int(score) if str(score).isdigit() else None, tier, c.get("provider", ""),
                   c.get("slug", ""), c["result"], c.get("open", 0), c.get("matches", 0),
                   to_local_naive(c.get("checked")), c.get("error", "")])
        r = cs.max_row
        cs.cell(r, 11).number_format = "yyyy-mm-dd hh:mm"
        cs.cell(r, 8).fill = fill(result_colors.get(c["result"], "FFE699"))
        if tier in TIER_COLORS:
            cs.cell(r, 4).fill = cs.cell(r, 5).fill = fill(TIER_COLORS[tier])
        if c.get("careers_url"):
            cs.cell(r, 12).hyperlink = c["careers_url"]
            cs.cell(r, 12).font = Font(color="0563C1", underline="single")
    for col, w in zip("ABCDEFGHIJKL", [9, 20, 28, 7, 6, 15, 26, 12, 13, 9, 17, 60]):
        cs.column_dimensions[col].width = w
    cs.freeze_panes = "A2"
    cs.auto_filter.ref = f"A1:L{max(cs.max_row, 2)}"

    tmp = path.with_name(path.stem + ".saving.xlsx")
    try:
        wb.save(tmp)
        os.replace(tmp, path)
        return True
    except PermissionError:
        log.warning("%s is open in Excel; it will be updated on the next run after you close it.",
                    path.name)
        tmp.unlink(missing_ok=True)
        return False


# ----------------------------------------------------------------- main loop
def notify(count, sample):
    try:
        from plyer import notification  # optional: pip install plyer
        notification.notify(title=f"Job Watcher: {count} new match{'es' if count != 1 else ''}",
                            message=sample[:250], timeout=10)
    except Exception:
        pass


def check_window(cfg, state, now, since_check, hours):
    """Start of the look-back window for a since-last-check run, or None for a normal run."""
    if not since_check:
        return None
    if hours:
        return now - dt.timedelta(hours=hours)
    last = parse_date(state.get("last_check"))
    if last:
        # small overlap so postings published while the last check was running are not missed
        return last - dt.timedelta(minutes=cfg.get("check_overlap_minutes", 15))
    return now - dt.timedelta(hours=cfg.get("first_check_lookback_hours", 24))


def fetch_all(cfg, state, store, rows, rules, now, max_age, window_start, verify):
    """Step 1: download each board's full list into the local store, one board per request
    wherever possible. Returns (companies.csv changed?, stats)."""
    check_all = cfg.get("check_all_every_run", True)
    # check_all: every company is read on every run, including ones with no board found yet
    recheck = dt.timedelta(0) if check_all else dt.timedelta(days=cfg.get("recheck_not_found_days", 14))
    cap = int(cfg.get("max_detections_per_run", 40))
    detections_left = float("inf") if check_all or cap <= 0 else cap
    stats = Counter()
    todo = []
    for r in rows:
        prev = state["companies"].get(r["company"], {})
        base = {"checked": now.isoformat(), "priority": priority_of(r), "region": r.get("region", ""),
                "careers_url": r.get("careers_url", "")}
        if is_manual(r):
            state["companies"][r["company"]] = {**base, "result": "MANUAL",
                                                "error": "Uses its own careers site; check it directly."}
            continue
        missing = prev.get("result") == "NOT FOUND" or (not prev and r.get("status") == "not_found")
        last = parse_date(prev.get("checked")) or parse_date(r.get("verified"))
        if missing and not verify and last and now - last < recheck:
            stats["skipped"] += 1
            continue
        if (prev.get("result") == "NO JOBS READ" and not verify and not check_all and last
                and now - last < dt.timedelta(hours=20) and reads_website(r)):
            stats["unreadable"] += 1  # a careers site with nothing readable is tried once a day
            continue
        entry = entry_for(r)
        explicit = ":" in entry and entry.split(":", 1)[0] in FETCHERS
        needs_detection = entry not in state["resolved"] and (
            not explicit or (entry.startswith("workday:") and not parse_workday(entry.split(":", 1)[1])[2]))
        if needs_detection:
            if detections_left <= 0:  # finding a board takes many guesses; spread them over runs
                state["companies"][r["company"]] = {
                    **base, "result": "PENDING",
                    "error": "Board detection is spread over several runs to avoid rate limits."}
                stats["waiting"] += 1
                continue
            detections_left -= 1
        todo.append(r)

    has_text, known_boards = store.keys_with_text(), store.boards()
    detail_cap = int(cfg.get("max_detail_requests_per_board", 25))
    delay = cfg.get("request_delay_seconds", 0)

    def work(row):
        try:
            return fetch_board(row, state, rules, has_text, now, max_age, window_start, known_boards, detail_cap)
        finally:
            if delay:
                time.sleep(delay)

    log.info("Reading %d company boards (%d skipped as not found recently, %d waiting for detection%s)...",
             len(todo), stats["skipped"], stats["waiting"],
             f", {stats['unreadable']} careers sites with nothing readable rechecked daily"
             if stats["unreadable"] else "")
    with ThreadPoolExecutor(max_workers=max(1, int(cfg.get("parallel_requests", 8)))) as pool:
        results = list(pool.map(work, todo))

    csv_dirty, today = False, now.astimezone().date().isoformat()
    for out in results:
        row = out["row"]
        company = row["company"]
        prev = state["companies"].get(company, {})
        record = {"checked": now.isoformat(), "priority": priority_of(row),
                  "region": row.get("region", ""), "careers_url": row.get("careers_url", "")}
        if out.get("deferred"):
            record.update({k: prev[k] for k in ("provider", "slug", "open") if k in prev})
            record.update(result="RETRY", error=f"Paused: {out['deferred']}. Retried next run.")
            state["companies"][company] = record
            stats["deferred"] += 1
            continue
        if out.get("error"):
            record.update(result="ERROR", error=str(out["error"])[:200])
            state["companies"][company] = record
            log.warning("%s: %s", company, out["error"])
            continue
        if out.get("missing"):
            if reads_website(row) or (SETTINGS["career_sites"] and row.get("careers_url")):
                why = ("The careers page could not be loaded (it may be down or block scripts). "
                       "Retried in a few days.")
            else:
                why = ("No job board found yet (board names are guessed again once a day). Add the "
                       "careers_url or slug in companies.csv, or run --find-career-pages.")
            record.update(result="NOT FOUND", error=why)
            state["companies"][company] = record
            if row.get("status") != "not_found":
                row.update(status="not_found", verified=today)
                csv_dirty = True
            continue

        provider, res = out["provider"], out["res"]
        slug = res["slug"]
        # A website is identified by its careers_url, so the slug column stays empty for it.
        csv_slug = "" if provider == "website" and row.get("careers_url") else slug
        if (row.get("board"), row.get("slug"), row.get("status")) != (provider, csv_slug, "ok"):
            if res.get("switch_to"):
                log.info("  BOARD  %s: careers page uses %s (%s); reading it directly from now on.",
                         company, provider.title(), slug)
            row.update(board=provider, slug=csv_slug, status="ok", verified=today)
            csv_dirty = True
        board = f"{provider}:{slug}"
        stats["new_postings"] += store.save_board(board, company, provider, out["postings"], now,
                                                  res.get("complete", True), board not in known_boards)
        stats["fetched"] += 1
        stats["details"] += out["details"]
        nothing_read = provider == "website" and not out["postings"]
        record.update(result="NO JOBS READ" if nothing_read else "OK", provider=provider, slug=slug,
                      open=len(out["postings"]), error=res.get("note", ""))
        state["companies"][company] = record
    return csv_dirty, stats


def run_once(cfg, since_check=False, hours=None, verify=False, refresh=False, offline=False,
             search=None, look_back=None, find_pages=False):
    """search: True runs the web search now, False skips it, None runs it when it is due."""
    now = dt.datetime.now(dt.timezone.utc)
    started = time.monotonic()
    SETTINGS["search_terms"] = cfg.get("search_terms") or SETTINGS["search_terms"]
    SETTINGS["max_pages"] = int(cfg.get("max_pages_per_search", 10))
    SETTINGS["backoff"] = float(cfg.get("rate_limit_backoff_seconds", 30))
    SETTINGS["host_intervals"] = {**SETTINGS["host_intervals"], **cfg.get("host_min_interval_seconds", {})}
    SETTINGS["website_interval"] = float(cfg.get("website_seconds_between_requests", 1.0))
    SETTINGS["career_sites"] = bool(cfg["career_sites"]["enabled"])
    SETTINGS["site_pages_per_run"] = int(cfg["career_sites"]["max_new_pages_per_site"])
    retry = float(cfg.get("retry_board_search_hours", 24))
    SETTINGS["guess_every"] = (dt.timedelta(hours=retry) if cfg.get("check_all_every_run", True) and retry > 0
                               else None)
    LIMITER.reset()
    with _ROBOTS_LOCK:
        _ROBOTS.clear()
    rules = Rules(cfg)
    SETTINGS["rules"] = rules
    excel_path = BASE / cfg.get("excel_file", "job_tracker.xlsx")
    state = load_json(STATE_PATH, {"jobs": {}, "resolved": {}, "companies": {}})
    for k in ("jobs", "resolved", "companies"):
        state.setdefault(k, {})
    SETTINGS["website_pages"] = state.setdefault("website_pages", {})
    state.pop("rejected", None)  # replaced by the local postings store
    since_check = since_check or cfg.get("since_last_check_by_default", False)
    window_start = check_window(cfg, state, now, since_check, hours)
    if window_start:
        log.info("CHECK  looking for postings since %s (last check: %s)",
                 window_start.astimezone().strftime("%Y-%m-%d %H:%M"),
                 to_local_naive(state.get("last_check")) or "never")

    apply_excel_edits(state, read_excel_edits(excel_path), now)

    # How old a posting may be when first found. --hours widens it for that run only.
    max_age = rules.max_age
    if hours:
        max_age = max(max_age, dt.timedelta(hours=hours))
    SETTINGS["window_days"] = max_age.total_seconds() / 86400
    SETTINGS["unsorted_pages"] = int(cfg.get("unsorted_scan_pages", 25))

    # Clear out postings left over from older, looser settings: ones that were already
    # older than the limit when found. Anything you have given a status or note is kept.
    pruned = 0
    for key, j in list(state["jobs"].items()):
        posted, found = parse_date(j.get("posted")), parse_date(j.get("found"))
        untouched = (j.get("status") or "New") == "New" and not j.get("notes")
        if ("allowed_age_h" not in j and untouched and posted and found
                and found - posted > rules.max_age + dt.timedelta(hours=1)):
            del state["jobs"][key]
            pruned += 1
    if pruned:
        log.info("Removed %d older postings that did not meet the %d-hour limit when found.",
                 pruned, rules.max_age.total_seconds() // 3600)

    all_rows = load_company_rows(cfg)
    found_dirty = False
    if not offline:  # step 0: look for companies you are not watching yet
        if search is True or (search is None and search_due(cfg, state, now)):
            found_dirty = run_search(cfg, state, all_rows, now, look_back)
        if find_pages:
            found_dirty = find_career_pages(cfg, state, all_rows, now) or found_dirty
    csv_dirty = False if offline else refresh_metrics(cfg, all_rows, force=refresh)
    csv_dirty = csv_dirty or found_dirty
    csv_dirty = rescore(all_rows) or csv_dirty
    scores = {r["company"]: (r.get("score", ""), r.get("tier", "")) for r in all_rows}
    rows = sorted((r for r in all_rows if is_enabled(r)),
                  key=lambda r: (priority_of(r), r["company"].lower()))
    names = {r["company"] for r in rows}
    for stale in [e for e in state["companies"] if e not in names]:
        del state["companies"][stale]

    store = PostingStore(BASE / cfg.get("postings_db", "postings.db"))
    try:
        stats = Counter()
        if not offline:  # step 1: download
            changed, stats = fetch_all(cfg, state, store, rows, rules, now, max_age, window_start, verify)
            csv_dirty = changed or csv_dirty
        if csv_dirty:
            save_company_rows(cfg, all_rows)

        # Step 2: filter locally against everything saved.
        new_jobs = []
        for m in find_new_matches(store, state, rules, now, max_age, window_start):
            state["jobs"][m["key"]] = {
                **m, "found": now.isoformat(), "status": "New", "notes": "", "open": True,
                "level": level_for(m["title"]), "posted": m["posted"].isoformat() if m["posted"] else None,
                "allowed_age_h": round(max_age.total_seconds() / 3600, 1)}
            new_jobs.append(f"{m['company']}: {m['title']} ({m['region']})")
        status = store.status_of(state["jobs"].keys())  # keep open/closed in step with the boards
        for key, j in state["jobs"].items():
            if key in status:
                if j.get("open") and not status[key]:
                    j["closed"] = now.isoformat()
                j["open"] = status[key]
        tracked_open = Counter(j["company"] for j in state["jobs"].values() if j.get("open"))
        for name, c in state["companies"].items():
            c["matches"] = tracked_open.get(name, 0)
        store.export_csv(BASE / cfg.get("all_postings_csv", "all_postings.csv"), set(state["jobs"]))
        saved_total, saved_open = store.counts()
    finally:
        store.close()

    report = []
    if window_start:
        # Everything that appeared in the gap, including jobs a background run already picked up.
        # Uses the board's publish time when it has one, otherwise when this script first saw it.
        # A posting shown in any earlier check is never shown again (except in an --hours look).
        for j in state["jobs"].values():
            appeared = parse_date(j.get("posted")) or parse_date(j["found"])
            if j.get("open") and appeared >= window_start and (hours or not j.get("reported")):
                report.append(j)
        report.sort(key=lambda j: j.get("posted") or j["found"], reverse=True)
        state["last_check_report"] = {"from": window_start.isoformat(), "to": now.isoformat(),
                                      "keys": [j["key"] for j in report]}
        if not hours:
            state["last_check"] = now.isoformat()
            for j in report:
                j["reported"] = now.isoformat()
    state["last_run"] = now.isoformat()

    save_json(STATE_PATH, state)
    write_excel(excel_path, state, cfg, scores)

    if offline:
        log.info("Re-filtered %d saved open postings locally (no requests made).", saved_open)
    else:
        sites = ", ".join(f"{s} {n}" for s, n in LIMITER.counts.most_common())
        log.info("Read %d boards with %d requests (%s). %d paused by rate limits and retried next run.",
                 stats["fetched"], sum(LIMITER.counts.values()), sites or "none", stats["deferred"])
        log.info("All postings: %d open saved locally, %d new this run, %d descriptions downloaded.",
                 saved_open, stats["new_postings"], stats["details"])
    open_total = sum(1 for j in state["jobs"].values() if j.get("open"))
    log.info("%d new matches, %d open matches in your tracker. Run took %.1f minutes.",
             len(new_jobs), open_total, (time.monotonic() - started) / 60)
    for line in new_jobs:
        log.info("  NEW  %s", line)
    if window_start:
        log.info("CHECK  %d posting%s since your last check:", len(report), "" if len(report) == 1 else "s")
        for j in report:
            log.info("  SINCE  %s: %s (%s)  %s", j["company"], j["title"], j["region"], j.get("url", ""))
        if report and cfg.get("desktop_notifications", True):
            notify(len(report), "\n".join(f"{j['company']}: {j['title']}" for j in report[:5]))
    elif new_jobs and cfg.get("desktop_notifications", True):
        notify(len(new_jobs), "\n".join(new_jobs[:5]))
    return report if window_start else new_jobs


def main():
    parser = argparse.ArgumentParser(description="Watch job boards for matching postings.")
    parser.add_argument("--loop", action="store_true", help="keep running in the background")
    parser.add_argument("--interval", type=float, help="minutes between checks (overrides config)")
    parser.add_argument("--since-last-check", action="store_true",
                        help="report only postings that appeared since your last check")
    parser.add_argument("--hours", type=float,
                        help="with --since-last-check: look back this many hours instead "
                             "(does not move your last-check timestamp)")
    parser.add_argument("--verify", action="store_true",
                        help="re-check every company in companies.csv, including ones marked not_found")
    parser.add_argument("--refresh-metrics", action="store_true",
                        help="refresh stock metrics and company scores now (needs yfinance)")
    parser.add_argument("--refilter", action="store_true",
                        help="re-run your filters on the saved postings only; makes no requests")
    parser.add_argument("--show-config", action="store_true",
                        help="explain your current settings in plain English, then exit")
    parser.add_argument("--search", action="store_true",
                        help="run the new-company web search now, even if it is not due")
    parser.add_argument("--no-search", action="store_true", help="skip the web search this run")
    parser.add_argument("--look-back", choices=["day", "week", "month"],
                        help="with --search: how far back to search this time")
    parser.add_argument("--find-career-pages", action="store_true",
                        help="use web search to find careers pages for companies with no board found")
    parser.add_argument("--add", metavar="URL",
                        help="watch a company from a job posting, job board or careers page link")
    parser.add_argument("--name", help="with --add: the company name to show")
    parser.add_argument("--import", dest="import_files", nargs="+", metavar="FILE",
                        help="add the companies in these .xlsx or .csv files to companies.csv")
    args = parser.parse_args()
    if args.hours and not args.since_last_check:
        args.since_last_check = True

    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s",
                        datefmt="%Y-%m-%d %H:%M",
                        handlers=[logging.FileHandler(LOG_PATH, encoding="utf-8"),
                                  logging.StreamHandler(sys.stdout)])
    load_env()

    def settings():
        try:
            return load_config()  # reloaded every cycle so edits apply without a restart
        except ConfigError as exc:
            log.error("Settings problem: %s", exc)
            return None

    if args.show_config or args.add or args.import_files:
        cfg = settings()
        if cfg is None:
            sys.exit(1)
        SETTINGS["career_sites"] = bool(cfg["career_sites"]["enabled"])
        if args.show_config:
            print(describe_config(cfg))
        elif args.import_files:
            try:
                import_companies(cfg, args.import_files)
            except ConfigError as exc:
                log.error("%s", exc)
                sys.exit(1)
        else:
            add_company(cfg, args.add, args.name)
        return

    search = True if args.search else (False if args.no_search else None)
    while True:
        cfg = settings()
        if cfg is None and not args.loop:
            sys.exit(1)
        if cfg is not None:
            try:
                run_once(cfg, since_check=args.since_last_check, hours=args.hours,
                         verify=args.verify, refresh=args.refresh_metrics, offline=args.refilter,
                         search=search, look_back=args.look_back, find_pages=args.find_career_pages)
                # only the first cycle of --loop forces these
                args.verify = args.refresh_metrics = args.find_career_pages = False
                search, args.look_back = (False if args.no_search else None), None
            except Exception:
                log.exception("Run failed")
        if not args.loop:
            break
        minutes = args.interval or (cfg or {}).get("interval_minutes", 30)
        time.sleep(max(minutes, 5) * 60)


if __name__ == "__main__":
    main()
