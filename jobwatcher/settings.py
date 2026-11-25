"""The settings the desktop app edits, and saving them into my_settings.toml with its notes kept."""
from __future__ import annotations

from .base import *  # noqa: F401,F403
from .config import *  # noqa: F401,F403

# The fields the Settings screen shows, in config.toml's own section and key names.
# kind: list (of words), number, bool, choice, choices (several), text, regions
FIELDS = {
    "jobs": [
        ("titles", "list", "A job title must contain at least one of these"),
        ("skip_titles", "list", "Skip any title that contains one of these"),
        ("must_mention", "list", "The description must mention at least one of these. Leave empty to accept any stack"),
        ("max_age_hours", "number", "Only add jobs posted within this many hours"),
        ("years_experience", "number", "Your years of experience in the field. Postings that ask for more are "
                                       "left out (or marked, below). 0 = don't check"),
        ("years_over_ok", "number", "How many more years than yours a posting can ask for and still count. With 5 "
                                    "years and 1 here, a 6+ year job counts and a 7+ year job doesn't"),
        ("too_many_years", "choice:skip,flag", "skip = leave those jobs out; flag = keep them, marked with the "
                                               "years they ask for"),
    ],
    "places": [
        ("region", "regions", "A job must be in one of these regions. The first region that matches wins"),
        ("skip", "list", "A job listed only in these places is skipped"),
    ],
    "search": [
        ("enabled", "bool", "Search the web for companies you are not watching yet"),
        ("provider", "choice:serper,brave,serpapi", "Which search service to use"),
        ("runs_per_day", "number", "Searches per day"),
        ("look_back", "choice:day,week,month", "How far back each search looks"),
        ("sites", "choices:ashby,greenhouse,lever,workable,smartrecruiters,workday", "Job sites to search"),
        ("same_titles_as_jobs", "bool", "Search for the same job titles as your job filter (Which jobs, above). "
                                        "Long lists are split over several searches, so none gets cut off"),
        ("titles", "list", "Titles to search for when the switch above is off"),
        ("places", "list", "The posting must mention one of these places"),
        ("skip_words", "list", "Leave out postings that contain any of these words"),
        ("built_queries", "bool", "Run one search per job site built from the sites, titles, places and skip words above"),
        ("my_queries", "lines", "Your own searches, one per line, sent exactly as written (Google search syntax). "
                                "Keep site: in each so results link to job boards; other links are ignored"),
        ("pages_per_query", "number", "Result pages (10 results each) to read per search. 3 to 5 is plenty: "
                                      "most searches run out of fresh results before page 5"),
        ("monthly_query_limit", "number", "Never use more searches than this in a month"),
        ("new_companies", "choice:watch,review", "watch = start watching new companies right away; review = add them turned off"),
        ("career_page_lookups_per_run", "number", "Careers pages to look up per run with Find careers pages"),
    ],
    "career_sites": [
        ("enabled", "bool", "Read companies' own careers websites"),
        ("new_pages_per_site_per_run", "number", "New job pages to read per site per run"),
    ],
    "tracker": [
        ("desktop_notifications", "bool", "Pop-up on your desktop when new matches arrive"),
        ("since_last_check_by_default", "bool", "Every run lists what appeared since your last check"),
        ("check_all_companies_every_run", "bool", "Read every company on every run"),
        ("retry_board_search_hours", "number", "Hours between guesses for companies with no board found"),
        ("check_every_minutes", "number", "Minutes between checks with --loop"),
    ],
    "outreach": [
        ("resume", "text", "Your resume file, in the Job Watcher folder"),
        ("about_me", "text", "The file with your details for emails"),
        ("contacts_per_company", "number", "People to email at each company"),
        ("departments", "list", "Hunter departments to look in"),
        ("verify_emails", "bool", "Check each address with Hunter before using it"),
        ("monthly_hunter_credit_limit", "number", "Never use more Hunter credits than this in a month"),
        ("follow_up_after_days", "number", "Days to wait before a follow-up"),
        ("model", "text", "The Claude model that writes drafts"),
    ],
    "schedule": [
        ("enabled", "bool", "Run automatically at these times while Job Watcher is open"),
        ("times", "list", "When to run each day, as 24-hour times such as 08:00 or 17:30"),
        ("keep_in_tray", "bool", "When you close the window, keep Job Watcher running by the clock (Windows tray) so "
                                 "automatic runs still happen. Off: closing the window quits it. Takes effect the next "
                                 "time you open Job Watcher"),
    ],
    "ai": [
        ("score_new_matches", "bool", "Ask Claude for a fit score on each new match (needs your Anthropic key)"),
        ("monthly_limit", "number", "Never use more Claude requests than this in a month (fit scores and tailored resumes)"),
        ("model", "text", "The Claude model for fit scores and tailored resumes"),
        ("resume_tex", "text", "Your base LaTeX resume, in the Job Watcher folder"),
        ("resume_rules", "text", "Your rules for tailoring it (a text file; built-in rules if missing)"),
    ],
    "alerts": [
        ("ntfy_server", "text", "The ntfy server (leave as https://ntfy.sh unless you run your own)"),
    ],
}
SECTION_TITLES = {"jobs": "Which jobs", "places": "Where", "search": "Finding new companies",
                  "career_sites": "Careers websites", "tracker": "Runs and alerts", "outreach": "Recruiter emails",
                  "schedule": "Automatic runs", "ai": "AI help (Claude)", "alerts": "Phone alerts"}


def _value(cfg, section, key):
    target = CONFIG_LAYOUT[section][key]
    if isinstance(target, tuple):
        return cfg[target[0]][target[1]]
    if target == "regions":
        return [{"name": r["name"], "color": r.get("color") or "", "match": r["match"]} for r in cfg["regions"]]
    return cfg[target]


def current(cfg):
    return {"sections": [
        {"id": section, "title": SECTION_TITLES[section],
         "fields": [{"key": key, "kind": kind, "help": help_, "value": _value(cfg, section, key)}
                    for key, kind, help_ in fields]}
        for section, fields in FIELDS.items()]}


def _clean(kind, value, where):
    if kind == "lines":
        if isinstance(value, str):
            value = value.splitlines()
        if not isinstance(value, list):
            raise ConfigError(f"{where} should be a list, one per line")
        out = [" ".join(str(v).split()) for v in value if str(v).strip()]
        long = [q for q in out if len(q) > 2000]
        if long:
            raise ConfigError(f"{where}: a search is {len(long[0])} characters long; search services stop near 2,000")
        return out
    if kind in ("list",) or kind.startswith("choices:"):
        if isinstance(value, str):
            value = [v for v in (x.strip() for x in value.split(",")) if v]
        if not isinstance(value, list):
            raise ConfigError(f"{where} should be a list")
        out = [str(v).strip() for v in value if str(v).strip()]
        if kind.startswith("choices:"):
            allowed = kind.split(":", 1)[1].split(",")
            bad = [v for v in out if v not in allowed]
            if bad:
                raise ConfigError(f"{where} can only use {', '.join(allowed)}")
        return out
    if kind == "number":
        try:
            number = float(value)
        except (TypeError, ValueError):
            raise ConfigError(f"{where} should be a number") from None
        if number < 0:
            raise ConfigError(f"{where} can't be negative")
        return int(number) if number == int(number) else number
    if kind == "bool":
        return bool(value)
    if kind.startswith("choice:"):
        if value not in kind.split(":", 1)[1].split(","):
            raise ConfigError(f"{where} should be one of {kind.split(':', 1)[1].replace(',', ', ')}")
        return value
    if kind == "regions":
        if not isinstance(value, list) or not value:
            raise ConfigError("Add at least one region")
        out = []
        for i, r in enumerate(value, start=1):
            name = str(r.get("name") or "").strip()
            match = _clean("list", r.get("match") or [], f"Region {i}")
            if not name or not match:
                raise ConfigError(f"Region {i} needs a name and at least one place to match")
            color = str(r.get("color") or "").strip().lstrip("#").upper()
            if color and not re.fullmatch(r"[0-9A-F]{6}", color):
                raise ConfigError(f"The colour for {name} should look like C6EFCE")
            out.append({"name": name, "color": color, "match": match})
        return out
    return str(value or "").strip()


def apply_changes(text, changes):
    """config.toml text with changes ({section: {key: value}}) applied, notes kept.
    Returns (new text, settings). Raises ConfigError if the result would not load."""
    import tomlkit
    kinds = {s: {k: kind for k, kind, _ in fields} for s, fields in FIELDS.items()}
    doc = tomlkit.parse(text) if text.strip() else tomlkit.document()
    for section, values in (changes or {}).items():
        if section not in kinds or not isinstance(values, dict):
            raise ConfigError(f"Unknown settings section {section}")
        table = doc.get(section)
        if table is None:
            table = tomlkit.table()
            doc.add(tomlkit.nl())
            doc.add(section, table)
        for key, value in values.items():
            kind = kinds[section].get(key)
            if kind is None:
                raise ConfigError(f"Unknown setting {key} in [{section}]")
            value = _clean(kind, value, f"{key} in [{section}]")
            if kind == "regions":
                _set_regions(table, key, value)
            elif isinstance(value, list):
                table[key] = _array(value)
            else:
                table[key] = value
    new_text = tomlkit.dumps(doc)
    cfg, unknown = config_from_text(new_text)
    return new_text, cfg


def _set_regions(table, key, regions):
    """Edit the [[places.region]] blocks in place, so the notes after the last block (the next
    section's heading) stay where they are."""
    import tomlkit
    from tomlkit.items import Comment, Whitespace
    aot = table.get(key)
    if not isinstance(aot, tomlkit.items.AoT) or not len(aot):
        aot = tomlkit.aot()
        table[key] = aot
    old_last = aot[-1] if len(aot) else None
    trailing = []
    if old_last is not None:  # notes and blank lines after the last region's settings
        body = old_last.value.body
        while body and body[-1][0] is None and isinstance(body[-1][1], (Comment, Whitespace)):
            trailing.insert(0, body.pop()[1])
    while len(aot) > len(regions):
        aot.pop()  # AoT supports removing from the end
    for i, r in enumerate(regions):
        if i < len(aot):
            t = aot[i]
            same = (t.get("name") == r["name"] and str(t.get("color") or "").upper() == r["color"]
                    and list(t.get("match") or []) == r["match"])
            if same:
                continue  # leave it exactly as you wrote it
        else:
            if len(aot):
                aot[-1].value.body.append((None, Whitespace("\n")))  # a blank line between regions
            t = tomlkit.table()
            aot.append(t)
        for k in ("name", "color", "match"):
            if k in t:
                del t[k]
        t.add("name", r["name"])
        if r["color"]:
            t.add("color", r["color"])
        t.add("match", _array(r["match"]))
        if i < len(regions) - 1:
            t.value.body.append((None, Whitespace("\n")))  # keep the blank line before the next region
    for item in trailing:
        aot[-1].value.body.append((None, item))


def _array(items, indent=2):
    """A list written the way config.toml writes lists: wrapped at about 90 characters."""
    import tomlkit
    parts = [json.dumps(str(i), ensure_ascii=False) for i in items]
    one_line = "[" + ", ".join(parts) + "]"
    if len(one_line) <= 90:
        return tomlkit.parse(f"x = {one_line}")["x"]
    lines, line = [], ""
    for part in parts:
        if line and len(line) + len(part) + 2 > 86:
            lines.append(line)
            line = ""
        line += (", " if line else "") + part
    lines.append(line)
    pad = " " * indent
    return tomlkit.parse("x = [\n" + ",\n".join(pad + l for l in lines) + ",\n]")["x"]


def save(changes):
    """Apply changes to your settings (my_settings.toml) and save them. Returns the new settings."""
    make_my_copies()
    text = CONFIG_PATH.read_text(encoding="utf-8-sig") if CONFIG_PATH.exists() else ""
    new_text, cfg = apply_changes(text, changes)
    tmp = CONFIG_PATH.with_suffix(".saving.toml")
    tmp.write_text(new_text, encoding="utf-8")
    replace_file(tmp, CONFIG_PATH)
    return cfg


__all__ = ["FIELDS", "SECTION_TITLES", "current", "apply_changes", "save"]
