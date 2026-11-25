"""Settings: defaults, config.toml and config.json loading, checks, and the plain-English summary."""
from __future__ import annotations

import subprocess

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
    "years_experience": 0, "years_over_ok": 1, "too_many_years": "skip",
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
        "same_titles_as_jobs": True, "built_queries": True, "my_queries": [],
    },
    # 4. Companies' own careers websites
    "career_sites": {"enabled": True, "max_new_pages_per_site": 15},
    # 5. Tracker and timing
    "excel_file": "job_tracker.xlsx",
    "companies_file": "my_companies.csv",
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
             "must_mention": "stack_keywords", "max_age_hours": "max_posted_age_hours",
             "years_experience": "years_experience", "years_over_ok": "years_over_ok",
             "too_many_years": "too_many_years"},
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
        raise ConfigError("Reading settings needs Python 3.11 or newer, or run: pip install tomli")
    try:
        doc = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{CONFIG_PATH.name} has a typo: {exc}. Check that text is in \"quotes\" "
                          "and list items are separated by commas.") from None
    cfg, unknown = _from_toml(doc)
    return _checked(cfg), unknown


# ----------------------------------------------------------------- your own copies
_copies_checked = False
_COMPANIES_LINE = re.compile(r'^(\s*companies_file\s*=\s*)"([^"]*)"', re.M)


def find_git():
    """git, from PATH or from GitHub Desktop's own copy. None if neither is there."""
    found = shutil.which("git")
    if found or sys.platform != "win32":
        return found
    desktop = sorted(Path(os.environ.get("LOCALAPPDATA", "")).glob("GitHubDesktop/app-*/resources/app/git/cmd/git.exe"))
    return str(desktop[-1]) if desktop else None


def _git(home, *args):
    git = find_git()
    if not git:
        return None
    try:
        return subprocess.run([git, "-C", str(home), *args], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None


def tracked_by_git(home, name):
    """True when name is a file git tracks here, i.e. a shared file in a clone of the repository.
    A clone whose git can't be found counts its config.toml and companies.csv as shared too."""
    if not (home / ".git").exists():
        return False
    r = _git(home, "ls-files", "--error-unmatch", "--", name)
    return True if r is None else r.returncode == 0


CONFLICT_MARKERS = re.compile(r"^(<{7}|={7}|>{7})( |$)", re.M)


def _usable(path):
    """path, unless it is missing or still has git's conflict markers in it from an unfinished pull."""
    if not path.exists():
        return None
    if CONFLICT_MARKERS.search(path.read_text(encoding="utf-8-sig", errors="replace")):
        log.warning("%s has unresolved git conflict markers, so Job Watcher starts your copy from the "
                    "shared defaults instead. To finish the pull: git checkout --theirs %s", path.name, path.name)
        return None
    return path


def make_my_copies(home=None):
    """Your settings live in my_settings.toml and your company list in my_companies.csv. Job Watcher
    only ever writes these two, and git ignores them, so your changes never reach anyone else and
    updating Job Watcher never clashes with them. The first time, they start as copies of
    config.toml and companies.csv, edits included; in a clone of the repository those two then go
    back to the shared versions, so `git pull` brings new defaults without conflicts."""
    global _copies_checked
    home = Path(home or BASE)
    if _copies_checked and home == BASE:
        return []
    if home == BASE:
        _copies_checked = True
    mine, shared_cfg = home / MY_SETTINGS, home / "config.toml"
    in_clone = (home / ".git").exists()
    made = []
    point_at_mine = (lambda text: _COMPANIES_LINE.sub(lambda m: f'{m.group(1)}"{MY_COMPANIES}"', text, count=1))
    if not mine.exists():
        source = _usable(shared_cfg) or _usable(defaults_dir() / "config.toml")
        if source is None:
            return made
        text = source.read_text(encoding="utf-8-sig")
        named = _COMPANIES_LINE.search(text)
        mine.write_text(point_at_mine(text), encoding="utf-8")
        made.append("config.toml")
        list_source = home / (named.group(2) if named else "companies.csv")  # your list until now
    else:
        text = mine.read_text(encoding="utf-8-sig")
        named = _COMPANIES_LINE.search(text)
        list_name = named.group(2) if named else MY_COMPANIES
        if list_name == "companies.csv" and in_clone and tracked_by_git(home, "companies.csv"):
            mine.write_text(point_at_mine(text), encoding="utf-8")  # still pointing at the shared list
            list_source = home / "companies.csv"
        elif list_name == MY_COMPANIES:
            list_source = home / "companies.csv"  # used only if my_companies.csv is missing
        else:
            list_source = None  # a list file of your own: left as it is
    my_list = home / MY_COMPANIES
    if list_source is not None and not my_list.exists():
        source = _usable(list_source) or _usable(defaults_dir() / "companies.csv")
        if source is not None:
            shutil.copy2(source, my_list)
            made.append("companies.csv")
    if not made:
        return made
    log.info("Your settings are now in %s and your company list in %s. Only these two change from now on, "
             "and git ignores them, so updating Job Watcher never clashes with your edits.", MY_SETTINGS, MY_COMPANIES)
    if in_clone:
        for name in ("config.toml", "companies.csv"):
            if tracked_by_git(home, name):
                _git(home, "checkout", "--", name)  # back to the shared version; yours is copied
    elif home != SOURCE_DIR:
        for name in ("config.toml", "companies.csv"):  # an installed copy's old files, now replaced
            old = home / name
            if old.exists() and not (home / f"{name}.old").exists():
                old.rename(home / f"{name}.old")
    return made


def load_config():
    """Read your settings (or the older config.json). Raises ConfigError with a readable reason."""
    global _config_notice_shown
    make_my_copies()
    if CONFIG_PATH.exists():
        cfg, unknown = config_from_text(CONFIG_PATH.read_text(encoding="utf-8-sig"))
        for item in unknown:
            log.warning("%s: ignored unknown setting %s (check the spelling).", CONFIG_PATH.name, item)
        if LEGACY_CONFIG_PATH.exists() and not _config_notice_shown:
            log.info("Using %s. config.json is no longer read and can be deleted.", CONFIG_PATH.name)
        _config_notice_shown = True
    elif LEGACY_CONFIG_PATH.exists():
        try:
            cfg = _from_json(json.loads(LEGACY_CONFIG_PATH.read_text(encoding="utf-8-sig")))
        except json.JSONDecodeError as exc:
            raise ConfigError(f"config.json is not valid JSON: {exc}") from None
    else:
        log.info("No %s found; using the built-in defaults.", CONFIG_PATH.name)
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
    if cfg.get("years_experience"):
        most = cfg["years_experience"] + (cfg.get("years_over_ok") or 0)
        lines.append(f"  - it asks for at most {most:g} years of experience (you have {cfg['years_experience']:g})"
                     + ("" if cfg.get("too_many_years", "skip") == "skip" else "; ones asking more are kept and marked"))
    lines.append("")
    key_name = SEARCH_KEYS[s["provider"]]
    has_key = bool(os.environ.get(key_name))
    if s["enabled"]:
        searches = search_queries(cfg)
        per_run = len(searches) * s["pages_per_query"]
        lines.append(f"New-company search: ON, {s['runs_per_day']:g} times a day through {s['provider']} "
                     f"({key_name} {'found' if has_key else 'MISSING'}).")
        if not has_key:
            lines.append(f"  Put this line in {ENV_PATH}:  {key_name}=your-key")
        lines.append(f"  Up to {per_run} queries per search, at most {s['monthly_query_limit']} a month. "
                     f"Looks back one {s['look_back']}.")
        for q in searches:
            lines.append("  " + q["query"] + ("   (yours)" if q["mine"] else ""))
        if not searches:
            lines.append("  No searches to run: pick at least one job site, or add searches of your own.")
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


MAX_QUERY_WORDS = 32  # Google (and Serper, which uses it) ignores words past the 32nd


def query_words(query):
    """Words as a search engine counts them, erring high: operators and OR count too."""
    return len(re.sub(r'["()]', " ", query).split())


def search_titles(cfg):
    """The titles web searches look for: your job filter's titles, unless you turned that off."""
    s = cfg["search"]
    return list(cfg.get("titles_include") or []) if s.get("same_titles_as_jobs", True) else list(s["titles"])


def built_searches(host, titles, places, skip_words):
    """One search per site, or more when there are too many titles for one: titles are split
    across searches so none passes the search engine's word limit."""
    titles = [t for t in titles if str(t).strip()]
    groups, group = [], []
    for t in titles:
        if group and query_words(build_query(host, group + [t], places, skip_words)) > MAX_QUERY_WORDS:
            groups.append(group)
            group = []
        group.append(t)
    groups.append(group)
    return [build_query(host, g, places, skip_words) for g in groups]


def search_queries(cfg):
    """Every search a web search sends, in order: the ones built from your sites, titles, places
    and skip words (unless built_queries is off), then your own, exactly as written."""
    s = cfg["search"]
    out = []
    if s.get("built_queries", True):
        titles = search_titles(cfg)
        for site in s["sites"]:
            host = SEARCH_SITES.get(str(site).lower())
            if host:
                out += [{"site": str(site).lower(), "query": q, "mine": False}
                        for q in built_searches(host, titles, s["places"], s["skip_words"])]
    seen = {q["query"] for q in out}
    for q in s.get("my_queries") or []:
        q = " ".join(str(q).split())
        if q and q not in seen:
            seen.add(q)
            out.append({"site": None, "query": q, "mine": True})
    return out


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
