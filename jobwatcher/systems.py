"""Readers for big employers' own job systems, which load their jobs with JavaScript so the
careers page itself shows nothing readable: Amazon, Oracle Cloud (Oracle Recruiting), Eightfold
(Microsoft and others) and iCIMS. The careers-site reader tries these first; when one doesn't
fit a site, it carries on exactly as before.
"""
from __future__ import annotations

from .base import *  # noqa: F401,F403

ORACLE = re.compile(r"https://([\w.-]+\.oraclecloud\.com)/hcmUI/CandidateExperience/([a-z]{2}(?:-[A-Z]{2})?)/sites/([\w-]+)")
EIGHTFOLD_DOMAIN = re.compile(r"""["']?domain["']?\s*[:=]\s*["']([a-z0-9.-]+\.[a-z]{2,})["']""", re.I)
ICIMS = re.compile(r"https://([\w-]+(?:-[\w-]+)*\.icims\.com)", re.I)


def detect(careers, final, html):
    """(name, details) when the careers page belongs to a job system we can read, else None."""
    host = (urlparse(final).hostname or "").lower()
    if host.endswith("amazon.jobs"):
        return "amazon", {}
    m = ORACLE.search(final) or ORACLE.search(html or "")
    if m:
        return "oracle", {"host": m.group(1), "lang": m.group(2), "site": m.group(3)}
    if "eightfold" in (html or "").lower() or host.endswith("eightfold.ai"):
        q = parse_qs(urlparse(final).query).get("domain")
        dm = q[0] if q else (EIGHTFOLD_DOMAIN.search(html or "") or [None, None])[1]
        if dm:
            return "eightfold", {"base": f"{urlparse(final).scheme}://{urlparse(final).netloc}", "domain": dm}
    m = ICIMS.search(final) if host.endswith("icims.com") else ICIMS.search(html or "")
    if m:
        return "icims", {"search": f"https://{m.group(1)}/jobs/search?ss=1&in_iframe=1"}
    return None


def read(name, info, careers):
    """result() for the job system, or None to fall back to the ordinary careers-site reader."""
    try:
        if name == "amazon":
            return amazon(careers)
        if name == "oracle":
            return oracle(careers, **info)
        if name == "eightfold":
            return eightfold(careers, **info)
    except RateLimited:
        raise
    except Exception as exc:  # an unexpected answer: the ordinary reader takes over
        log.debug("%s reader could not read %s: %s", name, careers, exc)
    return None


def _date(value):
    if not value:
        return None
    if isinstance(value, (int, float)):
        return dt.datetime.fromtimestamp(value / (1000 if value > 1e11 else 1), dt.timezone.utc)
    for fmt in ("%B %d, %Y", "%Y-%m-%d"):
        try:
            return dt.datetime.strptime(str(value).strip(), fmt).replace(tzinfo=dt.timezone.utc)
        except ValueError:
            pass
    return parse_date(value)


# ----------------------------------------------------------------- Amazon
def amazon(careers):
    jobs = {}
    for term in SETTINGS["search_terms"][:6]:
        for offset in range(0, 300, 100):
            data = get_json("https://www.amazon.jobs/en/search.json",
                            {"base_query": term, "result_limit": 100, "offset": offset, "sort": "recent"})
            batch = (data or {}).get("jobs") or []
            for j in batch:
                jid = str(j.get("id_icims") or j.get("id") or "")
                if not jid:
                    continue
                place = j.get("normalized_location") or j.get("location") or ""
                text = "\n\n".join(html_to_text(j.get(f) or "") for f in
                                   ("description", "basic_qualifications", "preferred_qualifications") if j.get(f))
                jobs[jid] = {"key": f"amazon:{jid}", "title": j.get("title", ""), "locations": [place] if place else [],
                             "url": "https://www.amazon.jobs" + (j.get("job_path") or f"/en/jobs/{jid}"),
                             "posted": _date(j.get("posted_date")), "text": text or None}
            if len(batch) < 100:
                break
    if not jobs:
        return None
    return result(careers, list(jobs.values()), complete=False,
                  note=f"Read through Amazon's job search ({len(jobs)} recent postings for your search terms).")


# ----------------------------------------------------------------- Oracle Cloud
def oracle(careers, host, lang, site):
    api = f"https://{host}/hcmRestApi/resources/latest"
    jobs, total, offset = [], None, 0
    while offset < 1000:
        data = get_json(f"{api}/recruitingCEJobRequisitions", {
            "onlyData": "true", "expand": "requisitionList.secondaryLocations",
            "finder": f"findReqs;siteNumber={site},limit=200,offset={offset},sortBy=POSTING_DATES_DESC"})
        item = ((data or {}).get("items") or [{}])[0]
        reqs = item.get("requisitionList") or []
        total = item.get("TotalJobsCount", total)
        for r in reqs:
            rid = str(r.get("Id") or "")
            places = [r.get("PrimaryLocation") or ""] + [s.get("Name", "") for s in r.get("secondaryLocations") or []]
            jobs.append({"key": f"oracle:{host}:{rid}", "title": r.get("Title", ""),
                         "locations": [p for p in places if p], "posted": _date(r.get("PostedDate")),
                         "url": f"https://{host}/hcmUI/CandidateExperience/{lang}/sites/{site}/job/{rid}", "text": None,
                         "load_detail": (lambda rid=rid: _oracle_detail(api, site, rid))})
        offset += 200
        if len(reqs) < 200:
            break
    if not jobs:
        return None
    return result(careers, jobs, host=host, complete=total is not None and len(jobs) >= total,
                  note="Read through Oracle Recruiting.")


def _oracle_detail(api, site, rid):
    data = get_json(f"{api}/recruitingCEJobRequisitionDetails",
                    {"expand": "all", "onlyData": "true", "finder": f'ById;Id="{rid}",siteNumber={site}'})
    item = ((data or {}).get("items") or [{}])[0]
    parts = [item.get(f) for f in ("ExternalDescriptionStr", "ExternalResponsibilitiesStr", "ExternalQualificationsStr")]
    text = "\n\n".join(html_to_text(p) for p in parts if p)
    return {"text": text} if text else {}


# ----------------------------------------------------------------- Eightfold
def eightfold(careers, base, domain):
    api = f"{base}/api/apply/v2/jobs"
    jobs, start, count = [], 0, None
    while start < 1000:
        data = get_json(api, {"domain": domain, "start": start, "num": 100, "sort_by": "timestamp"})
        batch = (data or {}).get("positions") or []
        count = (data or {}).get("count", count)
        for p in batch:
            pid = str(p.get("id") or "")
            places = p.get("locations") or ([p["location"]] if p.get("location") else [])
            desc = p.get("job_description")
            jobs.append({"key": f"eightfold:{domain}:{pid}", "title": p.get("name", ""), "locations": places,
                         "posted": _date(p.get("t_create")),
                         "url": p.get("canonicalPositionUrl") or f"{base}/careers/job/{pid}",
                         "text": html_to_text(desc) if desc else None,
                         "load_detail": None if desc else (lambda pid=pid: _eightfold_detail(api, domain, pid))})
        start += 100
        if len(batch) < 100:
            break
    if not jobs:
        return None
    return result(careers, jobs, complete=count is not None and len(jobs) >= count, note="Read through Eightfold.")


def _eightfold_detail(api, domain, pid):
    data = get_json(f"{api}/{pid}", {"domain": domain}) or {}
    return {"text": html_to_text(data["job_description"])} if data.get("job_description") else {}


__all__ = ["detect", "read"]
