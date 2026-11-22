"""Job-board readers (Greenhouse, Ashby, Lever, SmartRecruiters, Workable, Workday), link recognition and board detection."""
from __future__ import annotations

from .base import *  # noqa: F401,F403


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



FETCHERS = {"greenhouse": fetch_greenhouse, "ashby": fetch_ashby, "lever": fetch_lever,
            "smartrecruiters": fetch_smartrecruiters, "workable": fetch_workable,
            "workday": fetch_workday}  # website.py adds "website"
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
        if auto and guessing_paused(provider):
            continue
        try:
            r = FETCHERS[provider](slug, None, auto)
        except RateLimited:
            if not auto:
                raise  # a known board: pause the company and read it next run
            continue  # a guess isn't worth waiting for; companies known to use this site keep its allowance
        if r is not None:
            if r.get("switch_to"):  # a careers website that is really a job board
                provider = r["switch_to"][0]
            cache[entry] = {"provider": provider, "slug": r["slug"], "host": r.get("host")}
            return provider, r
    return None


GUESS_SITES = {"greenhouse": "greenhouse.io", "lever": "lever.co", "ashby": "ashbyhq.com",
               "workday": "myworkdayjobs.com", "smartrecruiters": "smartrecruiters.com", "workable": "workable.com"}


def guessing_paused(provider):
    """Board-name guesses stop for a site the moment it asks to slow down, so its remaining
    allowance goes to companies known to use it. (Before this, guesses for unknown companies used
    up Workable's allowance and paused 205 companies in one run.)"""
    site = GUESS_SITES.get(provider)
    return bool(site) and (site in LIMITER.blocked or LIMITER.strikes[site] > 0)


def display_name(entry):
    name = entry.split(":", 1)[1] if ":" in entry else entry
    return name if any(c.isupper() for c in name) else name.replace("-", " ").title()


__all__ = [n for n in list(globals()) if not n.startswith("__")]
