#!/usr/bin/env python3
"""
Job Watcher

Checks company career sites on Greenhouse, Ashby, Lever, Workday, SmartRecruiters and
Workable directly through their public JSON feeds, filters postings by title, location
and tech stack, scores each company on stock, growth, layoff and funding signals, and
keeps a colour-coded Excel tracker up to date.

Usage:
  python job_watcher.py                 run once
  python job_watcher.py --loop          keep running, checking every interval_minutes
  python job_watcher.py --since-last-check
                                        only report postings that appeared since the last
                                        time you ran with this flag (first time: past 24h)
  python job_watcher.py --since-last-check --hours 6
                                        same, but look back a fixed 6 hours this time
  python job_watcher.py --verify        re-check every company in companies.csv,
                                        including ones previously marked not_found
  python job_watcher.py --refresh-metrics
                                        refresh stock metrics and scores now
                                        (needs: pip install yfinance)

Your Status and Notes edits in the Excel file are preserved between runs.
Close the Excel file if you want a run to update it; otherwise the update is picked up
on the next run (nothing is lost, everything is kept in the state file).
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import html
import json
import logging
import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from functools import partial
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
TIER_COLORS = {"A": "C6EFCE", "B": "FFF2CC", "C": "F8CBAD"}
LEVER_HOSTS = ["https://api.lever.co", "https://api.eu.lever.co", "https://api.ca.lever.co"]
WORKDAY_SHARDS = ["wd1", "wd3", "wd5", "wd12", "wd10", "wd103", "wd108"]
COUNTRY_NAMES = {"CA": "Canada", "US": "United States", "IN": "India", "GB": "United Kingdom"}
MANUAL_BOARDS = {"other", "manual", "own"}

# Filled from config at the start of each run
SETTINGS = {"search_terms": ["software engineer", "software developer", "frontend", "full stack"],
            "max_pages": 10, "window_days": 1.0, "unsorted_pages": 25, "hard_max_pages": 100}

log = logging.getLogger("job_watcher")
_local = threading.local()


def session():
    """One HTTP session per worker thread."""
    if not hasattr(_local, "s"):
        s = requests.Session()
        s.headers.update({"User-Agent": "job-watcher/2.0 (personal job search)",
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


def _request(method, url, params=None, body=None, retries=2):
    """JSON request. Returns None on 4xx (except 429). Retries transient errors."""
    for attempt in range(retries):
        last = attempt == retries - 1
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
        if r.status_code == 429 or r.status_code >= 500:
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


def post_json(url, body, retries=2):
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
    d = get_json(f"{SR_API}/{quote(slug)}/postings/{pid}") or {}
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


def _wd_list(tenant, shard, site, term, offset, retries=2):
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
            except requests.ConnectionError:
                break  # this tenant is not on this shard
            except Exception:
                continue
            if isinstance(data, dict) and "jobPostings" in data:
                return sh, site
    return None


def _wd_detail(base, tenant, site, path):
    d = get_json(f"{base}/wday/cxs/{tenant}/{site}{path}") or {}
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


FETCHERS = {"greenhouse": fetch_greenhouse, "ashby": fetch_ashby, "lever": fetch_lever,
            "smartrecruiters": fetch_smartrecruiters, "workable": fetch_workable,
            "workday": fetch_workday}
AUTO_ORDER = ["greenhouse", "ashby", "lever", "smartrecruiters", "workable"]


def slug_variants(name):
    seen, out = set(), []
    low = name.lower().strip()
    for v in (name, low, low.replace(" ", ""), low.replace(" ", "-"),
              re.sub(r"[^a-z0-9]", "", low), re.sub(r"[^a-z0-9]+", "-", low).strip("-")):
        if v and v not in seen:
            seen.add(v)
            out.append(v)
    return out


def load_company(entry, state):
    """Return (provider, result) or None. Auto-detects the board for entries without a prefix."""
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
        candidates = [(p, v, True) for p in AUTO_ORDER for v in slug_variants(entry)]

    for provider, slug, auto in candidates:
        r = FETCHERS[provider](slug, None, auto)
        if r is not None:
            cache[entry] = {"provider": provider, "slug": r["slug"], "host": r.get("host")}
            return provider, r
    return None


def display_name(entry):
    name = entry.split(":", 1)[1] if ":" in entry else entry
    return name if any(c.isupper() for c in name) else name.replace("-", " ").title()


# ----------------------------------------------------------------- company list
CSV_FIELDS = ["priority", "region", "company", "score", "tier", "type", "ticker", "stage",
              "last_raise", "layoffs_12m", "board", "slug", "careers_url", "hubs", "enabled",
              "status", "verified", "market_cap_b", "stock_12m_pct", "revenue_growth_pct",
              "profit_margin_pct", "metrics_updated", "notes"]


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
    """Write detected boards, metrics and scores back so the list maintains itself."""
    path = companies_path(cfg)
    file_rows = sorted((r for r in rows if r.get("region") != "From config.json"),
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


def entry_for(row):
    board, slug = (row.get("board") or "auto").lower(), row.get("slug") or ""
    return f"{board}:{slug}" if board in FETCHERS and slug else (slug or row["company"])


def is_enabled(row):
    return (row.get("enabled") or "yes").lower() not in ("no", "n", "false", "0")


def is_manual(row):
    return (row.get("board") or "").lower() in MANUAL_BOARDS


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


def evaluate_company(row, state, rules, known, rejected, now, window_start, max_age, first_check):
    """Runs in a worker thread: fetch one board and pick out matching postings.
    Only reads shared state; the main thread applies the results.

    A posting is new only if it went up within max_age (default 24 hours). Postings with
    no date are treated as already up the first time a board is checked, and as new after
    that, since they were not there on the previous check."""
    try:
        loaded = load_company(entry_for(row), state)
    except Exception as exc:
        return {"row": row, "error": exc}
    if loaded is None:
        return {"row": row, "missing": True}
    provider, res = loaded
    matches, new_rejects, still_rejected = [], [], []

    def too_old(posted):
        return now - posted > max_age or bool(window_start and posted < window_start)

    for job in res["jobs"]:
        if not rules.title_ok(job["title"]):
            continue
        key = job["key"]
        if key in known:
            matches.append({"key": key, "known": True, "title": job["title"], "url": job["url"]})
            continue
        if key in rejected:
            still_rejected.append(key)
            continue
        posted = job.get("posted")
        if posted is not None and too_old(posted):
            continue
        if posted is None and first_check and not job.get("load_detail"):
            new_rejects.append(key)  # undated and already up when this board was first checked
            continue
        detail_loaded = False

        def load():
            nonlocal detail_loaded
            if detail_loaded or not job.get("load_detail"):
                return
            detail_loaded = True
            try:
                d = job["load_detail"]() or {}
            except Exception:
                d = {}
            if d.get("locations"):
                job["locations"] = d["locations"]
            job["text"] = d.get("text") or job.get("text") or ""
            job["url"] = d.get("url") or job["url"]
            job["posted"] = job.get("posted") or d.get("posted")

        def fails_age_after_detail():
            p = job.get("posted")
            return (p is not None and too_old(p)) or (p is None and first_check)

        region = rules.region_for(job["locations"])
        if region is None and job.get("ambiguous_location"):
            load()
            region = rules.region_for(job["locations"])
        if region is None:
            if detail_loaded:
                new_rejects.append(key)
            continue
        if job.get("text") is None:
            load()
        if detail_loaded and fails_age_after_detail():
            new_rejects.append(key)
            continue
        stack = rules.stack_for(job["title"], job.get("text"))
        if rules.require_stack and not stack:
            if detail_loaded:
                new_rejects.append(key)
            continue
        job.update(region=region, stack=stack)
        matches.append(job)
    return {"row": row, "provider": provider, "res": res, "matches": matches, "rejects": new_rejects,
            "still_rejected": still_rejected}


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
           ("Status", 14), ("Notes", 30), ("Posting", 9), ("Key", 10)]
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
        edits = {}
        for row in rows:
            if row and row[ki]:
                edits[row[ki]] = {"status": row[si] or "New", "notes": row[ni] or ""}
        wb.close()
        return edits
    except Exception as exc:
        log.warning("Could not read edits from %s (%s)", path.name, exc)
        return {}


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
            j["provider"].title(), j.get("status", "New"), j.get("notes", ""),
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
    result_colors = {"OK": "C6EFCE", "NOT FOUND": "F8CBAD", "MANUAL": "DDEBF7"}
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


def run_once(cfg, since_check=False, hours=None, verify=False, refresh=False):
    now = dt.datetime.now(dt.timezone.utc)
    SETTINGS["search_terms"] = cfg.get("search_terms") or SETTINGS["search_terms"]
    SETTINGS["max_pages"] = int(cfg.get("max_pages_per_search", 10))
    rules = Rules(cfg)
    excel_path = BASE / cfg.get("excel_file", "job_tracker.xlsx")
    state = load_json(STATE_PATH, {"jobs": {}, "resolved": {}, "companies": {}})
    for k in ("jobs", "resolved", "companies", "rejected"):
        state.setdefault(k, {})
    since_check = since_check or cfg.get("since_last_check_by_default", False)
    window_start = check_window(cfg, state, now, since_check, hours)
    if window_start:
        log.info("CHECK  looking for postings since %s (last check: %s)",
                 window_start.astimezone().strftime("%Y-%m-%d %H:%M"),
                 to_local_naive(state.get("last_check")) or "never")

    for key, edit in read_excel_edits(excel_path).items():
        if key in state["jobs"]:
            state["jobs"][key].update(edit)

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
            state["rejected"][key] = now.isoformat()
            pruned += 1
    if pruned:
        log.info("Removed %d older postings that did not meet the %d-hour limit when found.",
                 pruned, rules.max_age.total_seconds() // 3600)

    all_rows = load_company_rows(cfg)
    csv_dirty = refresh_metrics(cfg, all_rows, force=refresh)
    csv_dirty = rescore(all_rows) or csv_dirty
    scores = {r["company"]: (r.get("score", ""), r.get("tier", "")) for r in all_rows}

    rows = sorted((r for r in all_rows if is_enabled(r)),
                  key=lambda r: (priority_of(r), r["company"].lower()))
    names = {r["company"] for r in rows}
    for stale in [e for e in state["companies"] if e not in names]:
        del state["companies"][stale]

    # Companies confirmed missing recently are skipped until the recheck interval passes.
    recheck = dt.timedelta(days=cfg.get("recheck_not_found_days", 14))
    todo, skipped = [], 0
    for r in rows:
        base_rec = {"checked": now.isoformat(), "priority": priority_of(r), "region": r.get("region", ""),
                    "careers_url": r.get("careers_url", "")}
        if is_manual(r):
            state["companies"][r["company"]] = {**base_rec, "result": "MANUAL",
                                                "error": "Uses its own careers site; check it directly."}
            continue
        prev = state["companies"].get(r["company"], {})
        missing = prev.get("result") == "NOT FOUND" or (not prev and r.get("status") == "not_found")
        last = parse_date(prev.get("checked")) or parse_date(r.get("verified"))
        if missing and not verify and last and now - last < recheck:
            skipped += 1
            continue
        todo.append(r)

    # Forget rejected postings after 60 days so a re-posted role gets a fresh look.
    cutoff = now - dt.timedelta(days=60)
    state["rejected"] = {k: v for k, v in state["rejected"].items() if (parse_date(v) or now) > cutoff}
    known, rejected = set(state["jobs"]), set(state["rejected"])
    delay = cfg.get("request_delay_seconds", 1)

    checked_before = {name for name, c in state["companies"].items() if c.get("result") == "OK"}

    def work(row):
        try:
            return evaluate_company(row, state, rules, known, rejected, now, window_start, max_age,
                                    first_check=row["company"] not in checked_before)
        finally:
            time.sleep(delay)

    log.info("Checking %d company boards (%d skipped as not found recently)...", len(todo), skipped)
    with ThreadPoolExecutor(max_workers=max(1, int(cfg.get("parallel_requests", 8)))) as pool:
        results = list(pool.map(work, todo))

    new_jobs, today = [], now.astimezone().date().isoformat()
    for out in results:
        row = out["row"]
        company = row["company"]
        record = {"checked": now.isoformat(), "priority": priority_of(row),
                  "region": row.get("region", ""), "careers_url": row.get("careers_url", "")}
        if out.get("error"):
            record.update(result="ERROR", error=str(out["error"])[:200])
            state["companies"][company] = record
            log.warning("%s: %s", company, out["error"])
            continue
        if out.get("missing"):
            record.update(result="NOT FOUND",
                          error="No supported job board found. Add the careers URL or slug in "
                                "companies.csv, or set board to 'other'.")
            state["companies"][company] = record
            if row.get("status") != "not_found":
                row.update(status="not_found", verified=today)
                csv_dirty = True
            continue

        provider, res = out["provider"], out["res"]
        slug = res["slug"]
        if (row.get("board"), row.get("slug"), row.get("status")) != (provider, slug, "ok"):
            row.update(board=provider, slug=slug, status="ok", verified=today)
            csv_dirty = True
        for key in out["rejects"] + out["still_rejected"]:
            state["rejected"][key] = now.isoformat()  # refreshed while still posted
        seen = set()
        for job in out["matches"]:
            key = job["key"]
            seen.add(key)
            existing = state["jobs"].get(key)
            if job.get("known") and existing:
                existing.update(open=True, title=job["title"], company=company)
                continue
            if existing is None:
                existing = state["jobs"][key] = {
                    "key": key, "found": now.isoformat(), "status": "New", "notes": "",
                    "allowed_age_h": round(max_age.total_seconds() / 3600, 1)}
                new_jobs.append(f"{company}: {job['title']} ({job['region']})")
            existing.update({
                "company": company, "provider": provider, "title": job["title"],
                "locations": job["locations"], "region": job["region"], "stack": job["stack"],
                "level": level_for(job["title"]), "url": job["url"],
                "posted": job["posted"].isoformat() if job.get("posted") else None, "open": True,
            })
        if res.get("complete", True):  # partial searches cannot prove a posting was removed
            prefix = f"{provider}:{slug}:"
            for key, j in state["jobs"].items():
                if key.startswith(prefix) and key not in seen and j.get("open"):
                    j["open"] = False
                    j["closed"] = now.isoformat()
        record.update(result="OK", provider=provider, slug=slug, open=len(res["jobs"]),
                      matches=len(out["matches"]), error=res.get("note", ""))
        state["companies"][company] = record

    if csv_dirty:
        save_company_rows(cfg, all_rows)

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

    ok = sum(1 for c in state["companies"].values() if c["result"] == "OK")
    missing = sum(1 for c in state["companies"].values() if c["result"] in ("NOT FOUND", "ERROR"))
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
    parser.add_argument("--refresh-metrics", action="store_true",
                        help="refresh stock metrics and company scores now (needs yfinance)")
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
            run_once(cfg, since_check=args.since_last_check, hours=args.hours,
                     verify=args.verify, refresh=args.refresh_metrics)
            args.verify = args.refresh_metrics = False  # only the first cycle of --loop forces these
        except Exception:
            log.exception("Run failed")
        if not args.loop:
            break
        minutes = args.interval or cfg.get("interval_minutes", 30)
        time.sleep(max(minutes, 5) * 60)


if __name__ == "__main__":
    main()
