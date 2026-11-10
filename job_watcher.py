#!/usr/bin/env python3
"""
Job Watcher

Checks company job boards on Greenhouse, Ashby and Lever directly through their
public JSON feeds, filters postings by title, location and tech stack, and keeps
a colour-coded Excel tracker up to date.

Usage:
  python job_watcher.py                 run once
  python job_watcher.py --loop          keep running, checking every interval_minutes
  python job_watcher.py --loop --interval 15
  python job_watcher.py --since-last-check
                                        only report postings that appeared since the
                                        last time you ran with this flag (first time:
                                        the past 24 hours). Results go to the
                                        "Since Last Check" tab and the log.
  python job_watcher.py --verify        re-check every company in companies.csv,
                                        including ones previously marked not_found
  python job_watcher.py --since-last-check --hours 6
                                        same, but look back a fixed 6 hours this time

Your Status and Notes edits in the Excel file are preserved between runs.
Close the Excel file if you want a run to update it; otherwise the update is
picked up on the next run (nothing is lost, everything is kept in the state file).
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import threading
from concurrent.futures import ThreadPoolExecutor
import html
import json
import logging
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import quote

try:
    import requests
    from openpyxl import Workbook, load_workbook
    from openpyxl.formatting.rule import FormulaRule
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.worksheet.datavalidation import DataValidation
except ImportError as exc:  # pragma: no cover
    sys.exit(f"Missing dependency: {exc.name}\nRun: pip install requests openpyxl")

BASE = Path(__file__).resolve().parent
CONFIG_PATH = BASE / "config.json"
STATE_PATH = BASE / "job_watcher_state.json"
LOG_PATH = BASE / "job_watcher.log"

STATUSES = ["New", "Applied", "Interviewing", "Offer", "Rejected", "Skipped"]
STATUS_COLORS = {
    "New": ("FFF2CC", "7F6000"),
    "Applied": ("DDEBF7", "1F4E79"),
    "Interviewing": ("E4DFEC", "5B2C83"),
    "Offer": ("C6EFCE", "006100"),
    "Rejected": ("F8CBAD", "843C0C"),
    "Skipped": ("D9D9D9", "404040"),
}
LEVEL_COLORS = {"Senior/Lead": "FFE699", "Mid": "E2EFDA", "Unlabelled": None}
LEVER_HOSTS = ["https://api.lever.co", "https://api.eu.lever.co", "https://api.ca.lever.co"]
COUNTRY_NAMES = {"CA": "Canada", "US": "United States", "IN": "India", "GB": "United Kingdom"}

log = logging.getLogger("job_watcher")
_local = threading.local()


def session():
    """One HTTP session per worker thread."""
    if not hasattr(_local, "s"):
        _local.s = requests.Session()
        _local.s.headers["User-Agent"] = "job-watcher/1.0 (personal job search)"
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


TAG_RE = re.compile(r"<[^>]+>")


def html_to_text(s):
    if not s:
        return ""
    s = html.unescape(s)  # Greenhouse content arrives HTML-escaped
    return html.unescape(TAG_RE.sub(" ", s))


def get_json(url, params=None):
    """GET a JSON document. Returns None on 404. Retries once on transient errors."""
    for attempt in range(2):
        try:
            r = session().get(url, params=params, timeout=25)
        except requests.RequestException:
            if attempt:
                raise
            time.sleep(2)
            continue
        if r.status_code == 404:
            return None
        if r.status_code == 429 or r.status_code >= 500:
            if attempt:
                r.raise_for_status()
            time.sleep(5)
            continue
        r.raise_for_status()
        if "json" not in r.headers.get("Content-Type", ""):
            if attempt:
                raise ValueError(f"Non-JSON response from {url}")
            time.sleep(2)
            continue
        return r.json()
    return None


# ----------------------------------------------------------------- job boards
def fetch_greenhouse(slug, host=None):
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
            "key": f"greenhouse:{slug}:{j.get('id')}",
            "title": j.get("title", ""),
            "locations": locs,
            "url": j.get("absolute_url", ""),
            "posted": parse_date(j.get("first_published") or j.get("updated_at")),
            "text": html_to_text(j.get("content", "")),
        })
    return None, jobs


def fetch_ashby(slug, host=None):
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
            "key": f"ashby:{slug}:{j.get('id')}",
            "title": j.get("title", ""),
            "locations": locs,
            "url": j.get("jobUrl") or j.get("applyUrl", ""),
            "posted": parse_date(j.get("publishedAt")),
            "text": j.get("descriptionPlain") or html_to_text(j.get("descriptionHtml", "")),
        })
    return None, jobs


def _norm_lever(slug, postings):
    jobs = []
    for j in postings:
        cat = j.get("categories") or {}
        locs = []
        if cat.get("location"):
            locs.append(cat["location"])
        locs.extend(l for l in (cat.get("allLocations") or []) if l)
        if j.get("country"):
            locs.append(COUNTRY_NAMES.get(j["country"], j["country"]))
        parts = [j.get("descriptionPlain") or ""]
        for lst in j.get("lists") or []:
            parts.append(lst.get("text", ""))
            parts.append(html_to_text(lst.get("content", "")))
        parts.append(j.get("additionalPlain") or "")
        jobs.append({
            "key": f"lever:{slug}:{j.get('id')}",
            "title": j.get("text", ""),
            "locations": locs,
            "url": j.get("hostedUrl", ""),
            "posted": parse_date(j.get("createdAt")),
            "text": " ".join(parts),
        })
    return jobs


def fetch_lever(slug, host=None):
    empty_host = None
    for h in ([host] if host else LEVER_HOSTS):
        try:
            data = get_json(f"{h}/v0/postings/{quote(slug)}", {"mode": "json"})
        except Exception:
            continue
        if not isinstance(data, list):
            continue
        if data:
            return h, _norm_lever(slug, data)
        empty_host = empty_host or h
    return (empty_host, []) if empty_host else None


FETCHERS = {"greenhouse": fetch_greenhouse, "ashby": fetch_ashby, "lever": fetch_lever}


def slug_variants(name):
    seen, out = set(), []
    low = name.lower().strip()
    for v in (name, low, low.replace(" ", ""), low.replace(" ", "-"),
              re.sub(r"[^a-z0-9]", "", low), re.sub(r"[^a-z0-9]+", "-", low).strip("-")):
        if v not in seen:
            seen.add(v)
            out.append(v)
    return out


def load_company(entry, state):
    """Return (provider, slug, jobs) or None. Auto-detects the board for entries without a prefix."""
    cache = state.setdefault("resolved", {})
    if entry in cache:
        c = cache[entry]
        result = FETCHERS[c["provider"]](c["slug"], c.get("host"))
        if result is not None:
            host, jobs = result
            return c["provider"], c["slug"], jobs
        del cache[entry]  # board moved or closed; re-detect below

    if ":" in entry and entry.split(":", 1)[0] in FETCHERS:
        provider, slug = entry.split(":", 1)
        candidates = [(provider, slug)]
    else:
        candidates = [(p, v) for p in FETCHERS for v in slug_variants(entry)]

    for provider, slug in candidates:
        result = FETCHERS[provider](slug)
        if result is not None:
            host, jobs = result
            cache[entry] = {"provider": provider, "slug": slug, "host": host}
            return provider, slug, jobs
    return None


def display_name(entry):
    name = entry.split(":", 1)[1] if ":" in entry else entry
    return name if any(c.isupper() for c in name) else name.replace("-", " ").title()


# ----------------------------------------------------------------- company list
CSV_FIELDS = ["priority", "region", "company", "board", "slug", "hubs", "enabled", "status",
              "verified", "notes"]


def companies_path(cfg):
    return BASE / cfg.get("companies_file", "companies.csv")


def load_company_rows(cfg):
    """Rows from companies.csv, plus any quick additions listed in config.json."""
    rows, path = [], companies_path(cfg)
    if path.exists():
        with open(path, newline="", encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                r = {k.strip().lower(): (v or "").strip() for k, v in r.items() if k}
                if r.get("company"):
                    rows.append(r)
    else:
        log.warning("%s not found; using only the companies listed in config.json", path.name)
    for extra in cfg.get("companies", []):
        board, slug = extra.split(":", 1) if ":" in extra else ("auto", extra)
        rows.append({"priority": "9", "region": "From config.json", "company": display_name(extra),
                     "board": board, "slug": slug, "enabled": "yes"})
    return rows


def save_company_rows(cfg, rows):
    """Write detected boards and slugs back so the list fixes itself over time."""
    path = companies_path(cfg)
    file_rows = [r for r in rows if r.get("region") != "From config.json"]
    fields = list(CSV_FIELDS) + sorted({k for r in file_rows for k in r} - set(CSV_FIELDS))
    tmp = path.with_name(path.stem + ".saving.csv")
    try:
        with open(tmp, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(file_rows)
        os.replace(tmp, path)
    except PermissionError:
        log.warning("%s is open in another program; board updates will be saved next run.", path.name)
        tmp.unlink(missing_ok=True)


def entry_for(row):
    board, slug = (row.get("board") or "auto").lower(), row.get("slug") or ""
    return f"{board}:{slug}" if board in FETCHERS and slug else (slug or row["company"])


def is_enabled(row):
    return (row.get("enabled") or "yes").lower() not in ("no", "n", "false", "0")


def priority_of(row):
    try:
        return int(row.get("priority") or 9)
    except ValueError:
        return 9


# ----------------------------------------------------------------- filtering
class Rules:
    def __init__(self, cfg):
        self.title_in = compile_terms(cfg["titles_include"])
        self.title_out = compile_terms(cfg["titles_exclude"])
        self.stack = compile_terms(cfg["stack_keywords"])
        self.loc_out = compile_terms(cfg.get("locations_exclude", []))
        self.regions = [(r["name"], compile_terms(r["match"])) for r in cfg["regions"]]
        self.require_stack = cfg.get("require_stack_match", True)
        days = cfg.get("max_posted_age_days")
        self.max_age = dt.timedelta(days=days) if days else None

    def region_for(self, locations):
        kept = [l for l in locations if not any_match(self.loc_out, l)]
        for name, matchers in self.regions:
            if any(any_match(matchers, l) for l in kept):
                return name
        return None

    def evaluate(self, job):
        """Return (region, stack_terms) if the job fits, else None."""
        title = job["title"]
        if not any_match(self.title_in, title) or any_match(self.title_out, title):
            return None
        region = self.region_for(job["locations"])
        if not region:
            return None
        stack = all_matches(self.stack, f"{title}\n{job['text']}")
        if self.require_stack and not stack:
            return None
        return region, stack


def level_for(title):
    if re.search(r"(?<![A-Za-z])(Senior|Sr\.?|Lead)(?![A-Za-z])", title, re.I):
        return "Senior/Lead"
    if re.search(r"(?<![A-Za-z0-9])(II|2|Intermediate|Level 2)(?![A-Za-z0-9])", title):
        return "Mid"
    return "Unlabelled"


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
HEADERS = ["Found", "Posted", "Company", "Title", "Location", "Region", "Level",
           "Stack match", "Board", "Status", "Notes", "Posting", "Key"]
WIDTHS = [17, 11, 18, 48, 36, 22, 12, 30, 11, 14, 32, 9, 10]


def fill(hex_color):
    return PatternFill("solid", start_color=hex_color, end_color=hex_color)


def read_excel_edits(path):
    """Pull Status and Notes the user typed into the tracker, keyed by job key."""
    if not path.exists():
        return {}
    try:
        wb = load_workbook(path, read_only=True)
        ws = wb["Jobs"]
        rows = ws.iter_rows(values_only=True)
        header = list(next(rows))
        ki, si, ni = header.index("Key"), header.index("Status"), header.index("Notes")
        edits = {}
        for row in rows:
            if row and row[ki]:
                edits[row[ki]] = {"status": row[si] or "New", "notes": row[ni] or ""}
        wb.close()
        return edits
    except Exception as exc:
        log.warning("Could not read edits from %s (%s)", path.name, exc)
        return {}


def write_excel(path, state, cfg):
    region_colors = {r["name"]: r.get("color") for r in cfg["regions"]}
    jobs = sorted(state["jobs"].values(),
                  key=lambda j: (j["open"], j["found"], j.get("posted") or ""), reverse=True)

    wb = Workbook()
    ws = wb.active
    ws.title = "Jobs"
    ws.append(HEADERS)
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = fill("1F3864")
        cell.alignment = Alignment(vertical="center")
    ws.row_dimensions[1].height = 22

    for j in jobs:
        ws.append([
            to_local_naive(j["found"]), to_local_naive(j.get("posted")), j["company"], j["title"],
            "; ".join(dict.fromkeys(j["locations"])), j["region"], j["level"], ", ".join(j["stack"]),
            j["provider"].title(), j.get("status", "New"), j.get("notes", ""),
            "Open" if j["open"] else "Closed", j["key"],
        ])
        r = ws.max_row
        ws.cell(r, 1).number_format = "yyyy-mm-dd hh:mm"
        ws.cell(r, 2).number_format = "yyyy-mm-dd"
        title = ws.cell(r, 4)
        if j.get("url"):
            title.hyperlink = j["url"]
            title.font = Font(color="0563C1", underline="single")
        if region_colors.get(j["region"]):
            ws.cell(r, 6).fill = fill(region_colors[j["region"]])
        if LEVEL_COLORS.get(j["level"]):
            ws.cell(r, 7).fill = fill(LEVEL_COLORS[j["level"]])

    last = max(ws.max_row, 2)
    for i, w in enumerate(WIDTHS, start=1):
        ws.column_dimensions[chr(64 + i)].width = w
    ws.column_dimensions["M"].hidden = True
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:L{last}"

    dv = DataValidation(type="list", formula1='"' + ",".join(STATUSES) + '"', allow_blank=True)
    ws.add_data_validation(dv)
    dv.add(f"J2:J{last + 500}")

    # Found in the last 24 hours: green, bold
    ws.conditional_formatting.add(f"A2:A{last}", FormulaRule(
        formula=["AND(ISNUMBER($A2),NOW()-$A2<1)"],
        fill=fill("C6EFCE"), font=Font(bold=True, color="006100")))
    # Status colours
    for status, (bg, fg) in STATUS_COLORS.items():
        ws.conditional_formatting.add(f"J2:J{last + 500}", FormulaRule(
            formula=[f'$J2="{status}"'], fill=fill(bg), font=Font(bold=True, color=fg)))
    # Rejected, skipped or closed postings: whole row greyed out
    ws.conditional_formatting.add(f"A2:L{last}", FormulaRule(
        formula=['OR($J2="Rejected",$J2="Skipped",$L2="Closed")'],
        font=Font(color="999999", italic=True)))

    # Since Last Check: postings that appeared between your last two checks
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
        sc.append(["Posted", "Found", "Company", "Title", "Location", "Region", "Level", "Stack match"])
        for cell in sc[4]:
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = fill("1F3864")
        for key in rep["keys"]:
            j = state["jobs"].get(key)
            if not j:
                continue
            sc.append([to_local_naive(j.get("posted")), to_local_naive(j["found"]), j["company"],
                       j["title"], "; ".join(dict.fromkeys(j["locations"])), j["region"], j["level"],
                       ", ".join(j["stack"])])
            r = sc.max_row
            sc.cell(r, 1).number_format = sc.cell(r, 2).number_format = "yyyy-mm-dd hh:mm"
            if j.get("url"):
                sc.cell(r, 4).hyperlink = j["url"]
                sc.cell(r, 4).font = Font(color="0563C1", underline="single")
            if region_colors.get(j["region"]):
                sc.cell(r, 6).fill = fill(region_colors[j["region"]])
            if LEVEL_COLORS.get(j["level"]):
                sc.cell(r, 7).fill = fill(LEVEL_COLORS[j["level"]])
        if not rep["keys"]:
            sc.append(["Nothing new matched your filters in this window."])
        for col, w in zip("ABCDEFGH", [17, 17, 18, 48, 36, 22, 12, 30]):
            sc.column_dimensions[col].width = w
        sc.freeze_panes = "A5"
        wb.active = 0

    # Legend
    lg = wb.create_sheet("Legend")
    lg.append(["Colour", "Meaning"])
    for cell in lg[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = fill("1F3864")
    legend = [("C6EFCE", "Found column: discovered in the last 24 hours")]
    legend += [(r.get("color"), f"Region: {r['name']}") for r in cfg["regions"] if r.get("color")]
    legend += [(c, f"Level: {lvl}") for lvl, c in LEVEL_COLORS.items() if c]
    legend += [(bg, f"Status: {s}") for s, (bg, _) in STATUS_COLORS.items()]
    legend += [("FFFFFF", "Grey italic row: Rejected, Skipped, or posting no longer on the board")]
    for color, text in legend:
        lg.append(["", text])
        lg.cell(lg.max_row, 1).fill = fill(color)
    lg.column_dimensions["A"].width = 10
    lg.column_dimensions["B"].width = 70

    # Companies
    cs = wb.create_sheet("Companies")
    cs.append(["Priority", "Region", "Company", "Board", "Slug", "Result", "Open postings",
               "Matches", "Last checked", "Error"])
    for cell in cs[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = fill("1F3864")
    ordered = sorted(state.get("companies", {}).items(),
                     key=lambda kv: (kv[1].get("priority", 9), kv[0].lower()))
    for name, c in ordered:
        cs.append([c.get("priority", ""), c.get("region", ""), name, c.get("provider", ""),
                   c.get("slug", ""), c["result"], c.get("open", 0), c.get("matches", 0),
                   to_local_naive(c.get("checked")), c.get("error", "")])
        r = cs.max_row
        cs.cell(r, 9).number_format = "yyyy-mm-dd hh:mm"
        cs.cell(r, 6).fill = fill({"OK": "C6EFCE", "NOT FOUND": "F8CBAD"}.get(c["result"], "FFE699"))
    for col, w in zip("ABCDEFGHIJ", [9, 24, 26, 12, 20, 12, 14, 10, 17, 60]):
        cs.column_dimensions[col].width = w
    cs.freeze_panes = "A2"
    cs.auto_filter.ref = f"A1:J{max(cs.max_row, 2)}"

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


def run_once(cfg, since_check=False, hours=None, verify=False):
    now = dt.datetime.now(dt.timezone.utc)
    rules = Rules(cfg)
    excel_path = BASE / cfg.get("excel_file", "job_tracker.xlsx")
    state = load_json(STATE_PATH, {"jobs": {}, "resolved": {}, "companies": {}})
    state.setdefault("companies", {})
    since_check = since_check or cfg.get("since_last_check_by_default", False)
    window_start = check_window(cfg, state, now, since_check, hours)
    if window_start:
        log.info("CHECK  looking for postings since %s (last check: %s)",
                 window_start.astimezone().strftime("%Y-%m-%d %H:%M"),
                 to_local_naive(state.get("last_check")) or "never")

    for key, edit in read_excel_edits(excel_path).items():
        if key in state["jobs"]:
            state["jobs"][key].update(edit)

    all_rows = load_company_rows(cfg)
    rows = sorted((r for r in all_rows if is_enabled(r)),
                  key=lambda r: (priority_of(r), r["company"].lower()))
    names = {r["company"] for r in rows}
    for stale in [e for e in state["companies"] if e not in names]:
        del state["companies"][stale]

    # Companies confirmed missing recently are skipped until the recheck interval passes.
    recheck = dt.timedelta(days=cfg.get("recheck_not_found_days", 14))
    todo, skipped = [], 0
    for r in rows:
        prev = state["companies"].get(r["company"], {})
        missing = prev.get("result") == "NOT FOUND" or (not prev and r.get("status") == "not_found")
        last = parse_date(prev.get("checked")) or parse_date(r.get("verified"))
        if missing and not verify and last and now - last < recheck:
            skipped += 1
            continue
        todo.append(r)

    delay = cfg.get("request_delay_seconds", 1)

    def fetch(row):
        try:
            return row, load_company(entry_for(row), state), None
        except Exception as exc:
            return row, None, exc
        finally:
            time.sleep(delay)

    log.info("Checking %d company boards (%d skipped as not found recently)...", len(todo), skipped)
    with ThreadPoolExecutor(max_workers=max(1, int(cfg.get("parallel_requests", 8)))) as pool:
        results = list(pool.map(fetch, todo))

    new_jobs, csv_dirty, today = [], False, now.astimezone().date().isoformat()
    for row, loaded, exc in results:
        company = row["company"]
        record = {"checked": now.isoformat(), "priority": priority_of(row),
                  "region": row.get("region", "")}
        if exc:
            record.update(result="ERROR", error=str(exc)[:200])
            state["companies"][company] = record
            log.warning("%s: %s", company, exc)
            continue
        if loaded is None:
            record.update(result="NOT FOUND",
                          error="No Greenhouse, Ashby or Lever board found. The company may use "
                                "another system; or copy the slug from its careers URL into companies.csv.")
            state["companies"][company] = record
            if row.get("status") != "not_found":
                row.update(status="not_found", verified=today)
                csv_dirty = True
            continue

        provider, slug, jobs = loaded
        if (row.get("board"), row.get("slug"), row.get("status")) != (provider, slug, "ok"):
            row.update(board=provider, slug=slug, status="ok", verified=today)
            csv_dirty = True
        seen, matches = set(), 0
        for job in jobs:
            verdict = rules.evaluate(job)
            if not verdict:
                continue
            region, stack = verdict
            key = job["key"]
            seen.add(key)
            matches += 1
            existing = state["jobs"].get(key)
            if existing is None:
                if rules.max_age and job["posted"] and now - job["posted"] > rules.max_age:
                    continue
                if window_start and job["posted"] and job["posted"] < window_start:
                    continue
                existing = state["jobs"][key] = {
                    "key": key, "found": now.isoformat(), "status": "New", "notes": ""}
                new_jobs.append(f"{company}: {job['title']} ({region})")
            existing.update({
                "company": company, "provider": provider, "title": job["title"],
                "locations": job["locations"], "region": region, "stack": stack,
                "level": level_for(job["title"]), "url": job["url"],
                "posted": job["posted"].isoformat() if job["posted"] else None, "open": True,
            })
        prefix = f"{provider}:{slug}:"
        for key, j in state["jobs"].items():
            if key.startswith(prefix) and key not in seen and j.get("open"):
                j["open"] = False
                j["closed"] = now.isoformat()
        record.update(result="OK", provider=provider, slug=slug, open=len(jobs), matches=matches)
        state["companies"][company] = record

    if csv_dirty:
        save_company_rows(cfg, all_rows)

    report = []
    if window_start:
        # Everything that appeared in the gap, including jobs a background run already picked up.
        # Uses the board's publish time when it has one, otherwise when this script first saw it.
        already_reported = set() if hours else set((state.get("last_check_report") or {}).get("keys", []))
        for j in state["jobs"].values():
            appeared = parse_date(j.get("posted")) or parse_date(j["found"])
            if j.get("open") and appeared >= window_start and j["key"] not in already_reported:
                report.append(j)
        report.sort(key=lambda j: j.get("posted") or j["found"], reverse=True)
        state["last_check_report"] = {"from": window_start.isoformat(), "to": now.isoformat(),
                                      "keys": [j["key"] for j in report]}
        if not hours:
            state["last_check"] = now.isoformat()
    state["last_run"] = now.isoformat()

    save_json(STATE_PATH, state)
    write_excel(excel_path, state, cfg)

    ok = sum(1 for c in state["companies"].values() if c["result"] == "OK")
    missing = sum(1 for c in state["companies"].values() if c["result"] != "OK")
    open_total = sum(1 for j in state["jobs"].values() if j.get("open"))
    log.info("Checked %d boards (%d not found or errored). %d new matches, %d open matches total.",
             ok, missing, len(new_jobs), open_total)
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
    args = parser.parse_args()
    if args.hours and not args.since_last_check:
        args.since_last_check = True

    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s",
                        datefmt="%Y-%m-%d %H:%M",
                        handlers=[logging.FileHandler(LOG_PATH, encoding="utf-8"),
                                  logging.StreamHandler(sys.stdout)])
    while True:
        cfg = load_json(CONFIG_PATH, None)  # reloaded every cycle so edits apply without a restart
        if cfg is None:
            sys.exit(f"Could not read {CONFIG_PATH.name}")
        try:
            run_once(cfg, since_check=args.since_last_check, hours=args.hours, verify=args.verify)
            args.verify = False  # only the first cycle of --loop forces a full re-check
        except Exception:
            log.exception("Run failed")
        if not args.loop:
            break
        minutes = args.interval or cfg.get("interval_minutes", 30)
        time.sleep(max(minutes, 5) * 60)


if __name__ == "__main__":
    main()
