"""A whole run against fake job boards, fake search results and real careers pages served from
localhost, then recruiter outreach on the result. The tests run in order and share one folder."""
import csv
import datetime as dt
import email
import email.policy
import http.server
import json
import socketserver
import sys
import threading
from collections import Counter

import pytest
from openpyxl import load_workbook

import jobwatcher as jw
from jobwatcher import cli, discovery

HOME = jw.BASE
NOW = dt.datetime.now(dt.timezone.utc)
ISO = lambda d: d.strftime("%Y-%m-%dT%H:%M:%SZ")  # noqa: E731
FRESH, OLD = ISO(NOW - dt.timedelta(hours=3)), ISO(NOW - dt.timedelta(days=10))
HITS, SITES = Counter(), {}


def jsonld(title, posted, city, region, desc):
    return ('<html><head><script type="application/ld+json">' + json.dumps({
        "@context": "https://schema.org", "@type": "JobPosting", "title": title, "datePosted": posted,
        "description": f"<p>{desc}</p>", "jobLocation": {"@type": "Place", "address": {
            "addressLocality": city, "addressRegion": region, "addressCountry": "CA"}}})
            + "</script></head><body>job</body></html>")


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        site = SITES[self.server.server_address[1]]
        HITS[(site["name"], self.path)] += 1
        body = site["pages"].get(self.path)
        if body is None:
            self.send_response(404)
            self.end_headers()
            return
        kind = ("application/xml" if self.path.endswith(".xml") else
                "text/plain" if self.path.endswith(".txt") else "text/html; charset=utf-8")
        self.send_response(200)
        self.send_header("Content-Type", kind)
        self.end_headers()
        self.wfile.write(body.encode())


def serve(name, pages):
    server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    SITES[server.server_address[1]] = {"name": name, "pages": pages}
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{server.server_address[1]}"


def job(slug, i, title, loc, text, prov="greenhouse"):
    return {"key": f"{prov}:{slug}:{i}", "title": title, "locations": [loc], "url": f"https://x/{slug}/{i}",
            "posted": jw.parse_date(FRESH), "text": text}


BOARDS = {
    ("greenhouse", "knowngh"): [job("knowngh", 1, "Software Engineer", "Toronto, ON", "React"),
                                job("knowngh", 2, "Senior Full Stack Engineer", "Vancouver, BC", "TypeScript, Node.js"),
                                job("knowngh", 3, "Staff Software Engineer", "Toronto, ON", "React")],
    ("greenhouse", "embedco"): [job("embedco", 1, "Frontend Developer", "Remote - Canada", "React")],
    ("greenhouse", "newgh"): [job("newgh", 1, "Senior Software Developer", "Ottawa, ON", "JavaScript")],
    ("lever", "clio"): [job("clio", 1, "Software Developer II", "Vancouver, BC", "Ruby, React", prov="lever")],
    ("ashby", "fresh-startup"): [job("fresh-startup", 1, "Web Developer", "Toronto", "Next.js", prov="ashby")],
}


def fake_board(provider):
    def fetch(slug, host=None, auto=False):
        jobs = BOARDS.get((provider, slug))
        return None if jobs is None else jw.result(slug, [dict(j) for j in jobs])
    return fetch


def fake_search(provider, key, query, page, look_back, country=""):
    if "greenhouse.io" in query:
        return [("https://job-boards.greenhouse.io/newgh/jobs/1", "Job Application for Senior Software Developer at NewGH"),
                ("https://boards.greenhouse.io/knowngh/jobs/1", "Job Application for Software Engineer at Known GH")]
    if "lever.co" in query:
        return [("https://jobs.lever.co/clio/abc", "Clio - Software Developer II")]
    if "ashbyhq" in query:
        return [("https://jobs.ashbyhq.com/fresh-startup/uuid", "Web Developer @ Fresh Startup")]
    return []


@pytest.fixture(scope="module", autouse=True)
def world():
    """Careers sites on localhost, a small companies.csv, fake boards and fake search."""
    a = serve("embed", {"/careers": '<script src="https://boards.greenhouse.io/embed/job_board/js?for=embedco"></script>'})
    b = serve("jsonld", {
        "/robots.txt": "User-agent: *\nDisallow: /private/\n",
        "/careers": """<a href="/careers/benefits">Benefits</a>
          <a href="/careers/jobs/1001-senior-software-engineer?utm_source=x">Senior Software Engineer, Frontend</a>
          <a href="/careers/jobs/1002-software-developer">Software Developer</a>
          <a href="/careers/jobs/1003-sales-manager">Sales Manager</a>
          <a href="/private/jobs/1004-software-engineer">Software Engineer (private)</a>
          <a href="/careers/jobs/1005-web-developer">Web Developer</a>
          <a href="/careers/jobs/1006-frontend-engineer-vancouver">View job</a>""",
        "/careers/jobs/1001-senior-software-engineer": jsonld("Senior Software Engineer, Frontend", FRESH, "Toronto", "ON", "React"),
        "/careers/jobs/1002-software-developer": jsonld("Software Developer", OLD, "Toronto", "ON", "React"),
        "/careers/jobs/1003-sales-manager": jsonld("Sales Manager", FRESH, "Toronto", "ON", "React"),
        "/private/jobs/1004-software-engineer": jsonld("Software Engineer", FRESH, "Toronto", "ON", "React"),
        "/careers/jobs/1005-web-developer": "<html><body>Web Developer. React.</body></html>",
        "/careers/jobs/1006-frontend-engineer-vancouver": jsonld("Frontend Engineer", FRESH, "Vancouver", "BC", "Next.js")})
    c_pages = {"/careers": '<div id="root"></div>'}
    c = serve("sitemap", c_pages)
    c_pages.update({
        "/robots.txt": f"User-agent: *\nAllow: /\nSitemap: {c}/sitemap_index.xml\n",
        "/sitemap_index.xml": f"<sitemapindex><sitemap><loc>{c}/sitemap-blog.xml</loc></sitemap>"
                              f"<sitemap><loc>{c}/sitemap-jobs.xml</loc></sitemap></sitemapindex>",
        "/sitemap-jobs.xml": (f"<urlset><url><loc>{c}/jobs/2001-full-stack-developer</loc><lastmod>{NOW.date()}</lastmod></url>"
                              f"<url><loc>{c}/jobs/2002-full-stack-developer-old</loc>"
                              f"<lastmod>{(NOW - dt.timedelta(days=30)).date()}</lastmod></url></urlset>"),
        "/jobs/2001-full-stack-developer": ('<div itemscope itemtype="https://schema.org/JobPosting"><h1 itemprop="title">'
                                            f'Full Stack Developer</h1><meta itemprop="datePosted" content="{FRESH}">'
                                            '<span itemprop="addressLocality">Vancouver</span><span itemprop="addressRegion">BC</span>'
                                            '<div itemprop="description">Node.js, React</div></div>')})
    d = serve("blocked", {"/robots.txt": "User-agent: *\nDisallow: /\n", "/careers": "<a href='/jobs/1-software-engineer'>x</a>"})
    e = serve("jsonly", {"/careers": "<div id=app></div>"})
    rows = [
        dict(priority=1, region="Toronto / GTA", company="Known GH", board="greenhouse", slug="knowngh", enabled="yes", status="ok"),
        dict(priority=1, region="Vancouver / BC", company="Clio", board="auto", enabled="yes", status="not_found",
             verified=NOW.date().isoformat()),
        dict(priority=2, region="Toronto / GTA", company="EmbedCo", board="other", careers_url=f"{a}/careers", enabled="yes"),
        dict(priority=2, region="Toronto / GTA", company="JsonLD Co", board="other", careers_url=f"{b}/careers", enabled="yes"),
        dict(priority=2, region="Vancouver / BC", company="Sitemap Co", board="website", careers_url=f"{c}/careers", enabled="yes"),
        dict(priority=3, region="Ottawa", company="Blocked Co", board="other", careers_url=f"{d}/careers", enabled="yes"),
        dict(priority=3, region="Ottawa", company="JS Co", board="other", careers_url=f"{e}/careers", enabled="yes"),
        dict(priority=3, region="Ottawa", company="Old Manual", board="other", enabled="yes"),
        dict(priority=4, region="Seattle", company="Disabled", board="greenhouse", slug="x", enabled="no"),
    ]
    with open(HOME / "companies.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["priority", "region", "company", "board", "slug", "careers_url",
                                          "enabled", "status", "verified"])
        w.writeheader()
        w.writerows(rows)
    (HOME / ".env").write_text("SERPER_API_KEY=test-key\nHUNTER_API_KEY=hunter-test\n"
                               "GMAIL_ADDRESS=me@gmail.com\nGMAIL_APP_PASSWORD=abcd efgh ijkl mnop\n")
    jw.load_env(HOME / ".env")
    patch = pytest.MonkeyPatch()
    for p in ("greenhouse", "ashby", "lever", "smartrecruiters", "workable", "workday"):
        patch.setitem(jw.FETCHERS, p, fake_board(p))
    patch.setattr(discovery, "search_page", fake_search)
    yield
    patch.undo()


def state():
    """The saved state with tracked jobs, which live in postings.db."""
    return jw.load_state(jw.load_config(), excel_edits=False)


def companies():
    with open(HOME / "companies.csv", encoding="utf-8-sig") as f:
        return {r["company"]: r for r in csv.DictReader(f)}


def test_first_run_finds_everything():
    events, outcomes, phases = Counter(), Counter(), []

    def listen(kind, data):
        events.update([kind])
        if kind == "phase":
            phases.append(data["name"])
        if kind == "board_done":
            outcomes.update([data["outcome"]])
            assert data["ok"] == (data["outcome"] == "read")
    listener = jw.on_event(listen)
    try:
        jw.run_once(jw.load_config())
    finally:
        jw.off_event(listener)
    got = {(j["company"], j["title"]) for j in state()["jobs"].values()}
    assert got == {("Known GH", "Software Engineer"), ("Known GH", "Senior Full Stack Engineer"),
                   ("Clio", "Software Developer II"), ("EmbedCo", "Frontend Developer"),
                   ("JsonLD Co", "Senior Software Engineer, Frontend"), ("JsonLD Co", "Frontend Engineer"),
                   ("Sitemap Co", "Full Stack Developer"), ("NewGH", "Senior Software Developer"),
                   ("Fresh Startup", "Web Developer")}
    rows = companies()
    assert rows["Clio"]["board"] == "lever" and rows["Clio"]["status"] == "ok"
    assert rows["EmbedCo"]["board"] == "greenhouse" and rows["EmbedCo"]["slug"] == "embedco"
    assert rows["Fresh Startup"]["region"] == "Found by search"
    results = {k: v["result"] for k, v in state()["companies"].items()}
    assert results["JS Co"] == results["Blocked Co"] == "NO JOBS READ" and results["Old Manual"] == "MANUAL"
    assert ("jsonld", "/private/jobs/1004-software-engineer") not in HITS
    assert ("jsonld", "/careers/jobs/1003-sales-manager") not in HITS
    assert ("blocked", "/careers") not in HITS
    assert events["run_started"] == events["run_finished"] == 1
    assert events["board_done"] == events["boards_started"] * 9 and events["new_match"] == 9
    assert set(outcomes) <= {"read", "missing", "paused", "error"} and outcomes["read"] and not outcomes["error"]
    # after the boards, every step that takes time says so, in order
    after = phases[phases.index("postings"):]
    assert after == ["postings", "filter", "export", "save"]


def test_second_run_reads_no_job_page_twice():
    before = Counter(HITS)
    jw.run_once(jw.load_config())
    assert not [k for k in HITS if "/jobs/" in k[1] and HITS[k] > before.get(k, 0)]
    assert len(state()["jobs"]) == 9


def test_refilter_and_tracker_columns():
    jw.run_once(jw.load_config(), offline=True)
    ws = load_workbook(HOME / "job_tracker.xlsx")["Jobs"]
    assert [c.value for c in ws[1]][11:13] == ["Notes", "Outreach"] and ws.max_row == 10


def test_a_second_run_waits_for_the_first():
    with jw.RunLock():
        assert jw.run_once(jw.load_config(), offline=True) == []


def test_cancel_leaves_boards_for_next_run():
    listener = jw.on_event(lambda kind, data: jw.CANCEL.set() if kind == "boards_started" else None)
    try:
        jw.run_once(jw.load_config(), search=False)
    finally:
        jw.off_event(listener)
        jw.CANCEL.clear()
    assert {c["result"] for c in state()["companies"].values()} <= {"RETRY", "MANUAL"}


def test_refresh_metrics_only(monkeypatch):
    called = []
    from jobwatcher import runner
    monkeypatch.setattr(runner, "refresh_metrics", lambda cfg, rows, force=False: called.append(force) or True)
    monkeypatch.setattr(cli, "run_once", lambda *a, **k: pytest.fail("--refresh-metrics must not start a run"))
    monkeypatch.setattr(sys, "argv", ["job_watcher.py", "--refresh-metrics"])
    cli.main()
    assert called == [True]


def test_add_company_from_links():
    jw.add_company(jw.load_config(), "https://jobs.lever.co/brandnew/123")
    jw.add_company(jw.load_config(), "https://careers.someco.com/jobs")
    rows = companies()
    assert rows["Brandnew"]["board"] == "lever" and rows["Someco"]["board"] == "website"


# ----------------------------------------------------------------- outreach
class Reply:
    def __init__(self, code, data):
        self.status_code, self._data = code, data

    def json(self):
        return self._data


def fake_hunter(url, params=None, timeout=None):
    if url.endswith("domain-search"):
        if params.get("company") == "Known GH":
            return Reply(200, {"data": {"domain": "knowngh.com", "pattern": "{first}", "emails": [
                {"value": "sam@knowngh.com", "type": "personal", "confidence": 95, "first_name": "Sam",
                 "last_name": "Lee", "position": "Senior Technical Recruiter", "verification": {"status": "valid"}},
                {"value": "ria@knowngh.com", "type": "personal", "confidence": 88, "first_name": "Ria",
                 "last_name": "Shah", "position": "Engineering Manager, Frontend", "verification": {"status": "accept_all"}},
                {"value": "bob@knowngh.com", "type": "personal", "confidence": 99, "first_name": "Bob",
                 "last_name": "X", "position": "Account Executive"}]}})
        if params.get("company") == "Clio" and "department" not in params:
            return Reply(200, {"data": {"domain": "clio.com", "emails": [
                {"value": "careers@clio.com", "type": "generic", "confidence": 90}]}})
        return Reply(200, {"data": {"emails": []}})
    if url.endswith("email-verifier"):
        return Reply(200, {"data": {"status": "valid"}})
    raise AssertionError(url)


def test_outreach_packet(monkeypatch):
    import outreach
    (HOME / "about_me.md").write_text((HOME / "about_me.example.md").read_text().replace("Your Name", "Test Person"))
    (HOME / "resume.pdf").write_bytes(b"%PDF-1.4 test")
    wb = load_workbook(HOME / "job_tracker.xlsx")
    ws = wb["Jobs"]
    header = [c.value for c in ws[1]]
    for r in range(2, ws.max_row + 1):
        if ws.cell(r, header.index("Company") + 1).value in ("Known GH", "Clio"):
            ws.cell(r, header.index("Outreach") + 1).value = "Email"
    wb.save(HOME / "job_tracker.xlsx")
    monkeypatch.setattr(outreach.requests, "get", fake_hunter)
    folder = outreach.prepare(jw.load_config())
    known = next(p for p in folder.iterdir() if "Known-GH" in p.name).read_text()
    assert known.index("sam@") < known.index("ria@") and "bob@" not in known
    assert "careers@clio.com" in next(p for p in folder.iterdir() if "Clio" in p.name).read_text()
    assert "Test Person" in (folder / "INSTRUCTIONS.md").read_text()
    outs = {j["company"]: j.get("outreach") for j in state()["jobs"].values() if j.get("outreach")}
    assert outs == {"Known GH": "Prepared", "Clio": "Prepared"}
    assert companies()["Known GH"]["domain"] == "knowngh.com"

    (folder / "emails.json").write_text(json.dumps([
        {"to": "sam@knowngh.com", "to_name": "Sam Lee", "company": "Known GH", "subject": "Software Engineer (1) - Test",
         "body": "Hi Sam,\n\nShort note.\n\nTest", "attach": "resume.pdf", "linkedin_note": "Hi Sam"}]))
    out = outreach.export_drafts(jw.load_config(), folder)
    eml = email.message_from_bytes((out / "01_Known-GH_Sam-Lee.eml").read_bytes(), policy=email.policy.default)
    assert list(eml.keys())[0] == "X-Unsent" and [a.get_filename() for a in eml.iter_attachments()] == ["resume.pdf"]

    appended = []

    class FakeIMAP:
        def __init__(self, host):
            pass

        def login(self, user, password):
            assert password == "abcdefghijklmnop"

        def list(self):
            return "OK", [b'(\\Drafts) "/" "[Gmail]/Drafts"']

        def append(self, box, flags, date, data):
            appended.append(box)
            return "OK", [b""]

        def logout(self):
            pass

    monkeypatch.setattr(outreach.imaplib, "IMAP4_SSL", FakeIMAP)
    assert outreach.make_drafts(jw.load_config(), folder) == ["sam@knowngh.com"]
    assert appended == ['"[Gmail]/Drafts"']


def test_follow_ups():
    import outreach
    data = state()
    for j in data["jobs"].values():
        if j["company"] == "Known GH":
            j["outreach"] = "Sent"
            j["outreach_sent"] = (NOW - dt.timedelta(days=6)).isoformat()
    jw.save_state(jw.load_config(), data, since=NOW)
    wb = load_workbook(HOME / "job_tracker.xlsx")
    ws = wb["Jobs"]
    header = [c.value for c in ws[1]]
    for r in range(2, ws.max_row + 1):
        if ws.cell(r, header.index("Company") + 1).value == "Known GH":
            ws.cell(r, header.index("Outreach") + 1).value = "Sent"
    wb.save(HOME / "job_tracker.xlsx")
    folder = outreach.prepare(jw.load_config(), follow_ups=True)
    assert "sam@knowngh.com" in next(p for p in folder.iterdir() if "Known-GH" in p.name).read_text()
    assert outreach.prepare(jw.load_config(), follow_ups=True) is None
