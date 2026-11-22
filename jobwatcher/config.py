"""Settings: defaults, config.toml and config.json loading, checks, and the plain-English summary."""
from __future__ import annotations

from .base import *  # noqa: F401,F403


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
        "resume": "resume.pdf", "tailored_resumes_folder": "resumes", "about_me": "about_me.md",
        "contacts_per_company": 2, "departments": ["hr", "it", "management", "executive"],
        "verify_emails": True, "monthly_hunter_credit_limit": 50, "follow_up_after_days": 5,
        "imap_server": "imap.gmail.com", "drafts_folder": "[Gmail]/Drafts", "model": "claude-sonnet-5-5",
    },
    # 8. Automatic runs while the desktop app is open (or in the tray)
    "schedule": {"enabled": True, "times": ["08:00", "12:00", "16:00", "20:00"], "keep_in_tray": False},
    # 9. Claude: fit scores and tailored resumes (needs ANTHROPIC_API_KEY)
    "ai": {"score_new_matches": True, "monthly_limit": 300, "model": "claude-sonnet-5-5",
           "resume_tex": "resume.tex", "resume_rules": "resume_rules.md"},
    # 10. Phone alerts for new matches (ntfy.sh)
    "alerts": {"ntfy_server": "https://ntfy.sh"},
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
    "schedule": {k: ("schedule", k) for k in DEFAULTS["schedule"]},
    "ai": {k: ("ai", k) for k in DEFAULTS["ai"]},
    "alerts": {k: ("alerts", k) for k in DEFAULTS["alerts"]},
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


def config_from_text(text):
    """Settings from config.toml text, checked. Returns (cfg, unknown settings)."""
    if tomllib is None:
        raise ConfigError("config.toml needs Python 3.11 or newer, or run: pip install tomli")
    try:
        doc = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"config.toml has a typo: {exc}. Check that text is in \"quotes\" "
                          "and list items are separated by commas.") from None
    cfg, unknown = _from_toml(doc)
    return _checked(cfg), unknown


def load_config():
    """Read config.toml (or the older config.json). Raises ConfigError with a readable reason."""
    global _config_notice_shown
    if CONFIG_PATH.exists():
        cfg, unknown = config_from_text(CONFIG_PATH.read_text(encoding="utf-8-sig"))
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
    return _checked(cfg)


def _checked(cfg):
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
    bad_times = [t for t in cfg["schedule"]["times"] if not re.fullmatch(r"([01]?\d|2[0-3]):[0-5]\d", str(t).strip())]
    if bad_times:
        raise ConfigError(f"times in [schedule] should look like 08:00 or 17:30, not {', '.join(map(str, bad_times))}")
    cfg["schedule"]["times"] = sorted({f"{int(t.split(':')[0]):02d}:{t.split(':')[1]}" for t in cfg["schedule"]["times"]})
    bad_sites = [x for x in s["sites"] if x.lower() not in SEARCH_SITES]
    if bad_sites:
        raise ConfigError(f"sites in [search] can only use {', '.join(SEARCH_SITES)}; "
                          f"not {', '.join(bad_sites)}")
    return cfg


def load_env(path=ENV_PATH):
    """Load API keys: real environment variables win, then the password store the desktop app
    saves to (Windows Credential Manager or the macOS Keychain), then KEY=value lines in .env.
    Also accepts the .env.txt name Notepad tends to save, and UTF-16 files from PowerShell."""
    from .keys import load_keys
    if not path.exists() and path.with_name(path.name + ".txt").exists():
        path = path.with_name(path.name + ".txt")
    values = {}
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        load_keys(values)
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
        if key and value:
            values[key] = value
    load_keys(values)


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
    a, al = cfg.get("ai", DEFAULTS["ai"]), cfg.get("alerts", DEFAULTS["alerts"])
    lines.append(f"Claude fit scores for new matches: {'ON' if a['score_new_matches'] else 'OFF'}"
                 + (f", at most {a['monthly_limit']} Claude requests a month." if a["monthly_limit"] else ".")
                 + ("" if os.environ.get("ANTHROPIC_API_KEY") else " (No Anthropic key yet, so nothing is sent.)"))
    lines.append("Phone alerts: " + ("ON (ntfy topic saved with your keys)." if os.environ.get("NTFY_TOPIC") else
                                     "OFF. Save an ntfy topic under Keys and accounts to get them."))
    lines.append(f"Tracker: {cfg['excel_file']}   Companies list: {cfg['companies_file']}")
    return "\n".join(lines)



# ----------------------------------------------------------------- web search constants

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


__all__ = [n for n in list(globals()) if not n.startswith("__")]
