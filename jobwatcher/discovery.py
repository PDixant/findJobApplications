"""Finding new companies: web search, careers-page lookup, --add and --import."""
from __future__ import annotations

from .base import *  # noqa: F401,F403
from .config import *  # noqa: F401,F403
from .boards import *  # noqa: F401,F403
from .website import *  # noqa: F401,F403
from .companies import *  # noqa: F401,F403


# ----------------------------------------------------------------- finding new companies
# A web search for fresh postings on the job-board sites, the same as typing
#   site:jobs.ashbyhq.com (intitle:"Software Engineer" OR ...) ("Toronto" OR ...) -India
# into Google. Each result link names the company's board, and any board you are not
# already watching is added to companies.csv. From then on the whole board is read through
# its own feed, which gives exact posting dates and full descriptions.

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
    """--add: watch a company from any job posting, board or careers page link.
    Returns a sentence saying what changed (also printed by the command line)."""
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
            return f"Already watching that board ({b[0]}: {b[1]})."
        r = (added or updated)[0]
        message = f"{'Added' if added else 'Updated'} {r['company']}: {r['board']} board '{r['slug']}'."
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
            message = f"Updated {existing['company']}: careers page {existing['careers_url']}."
        else:
            rows.append({"priority": "8", "region": "Added by you", "company": label, "board": "website",
                         "careers_url": clean_url(link), "enabled": "yes", "notes": f"Added by you {today}"})
            message = (f"Added {label}: careers page {clean_url(link)}. "
                       "The next run checks whether it uses a job board and reads it.")
    save_company_rows(cfg, rows)
    save_json(STATE_PATH, state)
    return message


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


def import_companies(cfg, paths, save=True):
    """--import: add companies from spreadsheets to companies.csv. Returns (added, updated, rows
    read). save=False only previews: nothing is written."""
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
                           "notes": f"Imported {today}" + (f": {extra}" if extra else "")}
                    row["notes"] = row["notes"][:220]
                    rows.append(row)
                    added.append(row)
                    for k in name_keys(name):
                        by_key.setdefault(k, row)
                    if board:
                        by_board[board_key(*board)] = row
                    elif url:
                        by_site.setdefault(site_domain(url), row)
    if not save:
        return added, updated, seen
    save_company_rows(cfg, rows)
    print(f"Read {seen} company rows. Added {len(added)} new companies and filled in missing details "
          f"for {len(updated)} you already had. Saved to {companies_path(cfg).name}.")
    regions = Counter(r["region"] for r in added)
    if regions:
        print("New companies by region: " + ", ".join(f"{k} {v}" for k, v in regions.most_common()))
    return added, updated, seen


__all__ = [n for n in list(globals()) if not n.startswith("__")]
