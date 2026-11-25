"""Companies' own careers websites: robots.txt, page parsing, JSON-LD and microdata job postings."""
from __future__ import annotations

from .base import *  # noqa: F401,F403
from .boards import *  # noqa: F401,F403


# --- Reading web pages (companies' own careers sites)
_ROBOTS = {}
_ROBOTS_LOCK = threading.Lock()
_PAGES_LOCK = threading.Lock()  # guards the cache of job pages already read


def get_page(url, accept="text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
             max_bytes=MAX_PAGE_BYTES, retries=2):
    """(final_url, text) for an ordinary web page, or None if it cannot be read.
    Shares the polite per-site spacing and 429 handling used for the job boards.
    Raises BlockedAddress for an address that isn't a public website (see safeweb.py)."""
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
                    break  # a page this big is cut short; what was read is still used
            r.close()
        except requests.RequestException:
            if last:
                return None
            time.sleep(2)
            continue
        raw = bytes(body)
        if url.lower().endswith(".gz") or raw[:2] == b"\x1f\x8b":
            try:
                raw = gunzip_capped(raw, max_bytes * 4)
            except (OSError, zlib.error, TooBig):
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
    rules = RobotFileParser()
    try:
        got = get_page(origin + "/robots.txt", accept="text/plain,*/*", max_bytes=500_000)
        rules.parse(got[1].splitlines() if got and "<html" not in got[1][:500].lower() else [])
    except BlockedAddress:
        rules.parse(["User-agent: *", "Disallow: /"])  # not a public website: nothing there is read
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
        try:
            got = get_page(url, accept="application/xml,text/xml,*/*", max_bytes=10_000_000)
        except BlockedAddress as exc:
            log.info("Sitemap skipped: %s", exc)
            continue
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
    problem = address_problem(careers)
    if problem:
        raise BlockedAddress(f"Careers page not read: {problem}")
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

    from .systems import detect, read as read_system
    system = detect(careers, final, text)
    if system and system[0] == "icims":  # the jobs are in an iCIMS frame; read the frame's search page
        page = get_page(system[1]["search"])
        if page is not None:
            final, text = page
    elif system:
        r = read_system(system[0], system[1], careers)
        if r is not None:
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
    read = no_data = left = 0
    for _, link, key, title in to_read:
        if read >= SETTINGS["site_pages_per_run"]:
            jobs[key] = {"key": key, "title": title, "posted": None, "locations": [], "url": link,
                         "text": None}
            left += 1
            continue
        if not robots_allow(link):
            continue
        try:
            got = get_page(link)
        except BlockedAddress as exc:
            log.info("Job page skipped: %s", exc)
            continue
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
                + (f", {no_data} without job details" if no_data else "")
                + (f", {left} more left for the next runs" if left else "") + ".")
    out = [{k: j.get(k) for k in ("key", "title", "locations", "url", "posted", "text")}
           for j in jobs.values()]
    return result(careers, out, complete=False, note=note, partial=left > 0)




FETCHERS["website"] = fetch_website


__all__ = [n for n in list(globals()) if not n.startswith("__")]
