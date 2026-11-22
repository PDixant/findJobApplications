"""Unit checks for link recognition, search queries, company matching, page parsing and settings."""
import datetime as dt
import json

import pytest

import jobwatcher as jw
from jobwatcher import discovery

def test_board_from_url():
    cases = {
        "https://jobs.ashbyhq.com/ramp/1234-abcd": ("ashby", "ramp"),
        "https://jobs.ashbyhq.com/Rose%20Rocket/abc/application": ("ashby", "Rose Rocket"),
        "https://boards.greenhouse.io/stripe/jobs/123": ("greenhouse", "stripe"),
        "https://job-boards.greenhouse.io/figma/jobs/555?gh_src=x": ("greenhouse", "figma"),
        "https://boards.greenhouse.io/embed/job_app?for=acme&token=1": ("greenhouse", "acme"),
        "https://boards.greenhouse.io/embed/job_board/js?for=wealthsimple": ("greenhouse", "wealthsimple"),
        "https://boards-api.greenhouse.io/v1/boards/acme/jobs": ("greenhouse", "acme"),
        "https://jobs.lever.co/clio/uuid-1": ("lever", "clio"),
        "https://jobs.eu.lever.co/acme/uuid": ("lever", "acme"),
        "https://api.lever.co/v0/postings/acme?mode=json": ("lever", "acme"),
        "https://apply.workable.com/acme/j/ABC123/": ("workable", "acme"),
        "https://apply.workable.com/api/v1/widget/accounts/acme": ("workable", "acme"),
        "https://jobs.smartrecruiters.com/Visa/7438-software-engineer": ("smartrecruiters", "Visa"),
        "https://acme.wd5.myworkdayjobs.com/en-US/External/job/Toronto/SWE_R1": ("workday", "https://acme.wd5.myworkdayjobs.com/External"),
        "https://acme.wd1.myworkdayjobs.com/wday/cxs/acme/Careers/jobs": ("workday", "https://acme.wd1.myworkdayjobs.com/Careers"),
        "//boards.greenhouse.io/acme": ("greenhouse", "acme"),
        "https:\\/\\/jobs.lever.co\\/acme": ("lever", "acme"),
    }
    for url, want in cases.items():
        assert jw.board_from_url(url) == want, (url, jw.board_from_url(url))
    for url in ["https://www.greenhouse.io/careers", "https://jobs.ashbyhq.com/", "https://boards.greenhouse.io/embed/job_app",
                "https://www.workable.com/assets/embed.js", "https://linkedin.com/jobs/view/1", "",
                "https://myworkdayjobs.com/x", "https://jobs.lever.co/"]:
        assert jw.board_from_url(url) is None, url

def test_board_key_and_query():
    assert jw.board_key("ashby", "Ramp") == jw.board_key("ashby", "ramp")
    assert jw.board_key("workday", "https://acme.wd5.myworkdayjobs.com/External") == jw.board_key("workday", "acme/wd5/External")
    q = jw.build_query("jobs.ashbyhq.com", ["Software Engineer", "Web Developer"], ["Toronto", "Canada"], ["India", "New Grad"])
    assert q == 'site:jobs.ashbyhq.com (intitle:"Software Engineer" OR intitle:"Web Developer") ("Toronto" OR "Canada") -India -"New Grad"', q

def test_company_names():
    assert jw.company_name_for("greenhouse", "wealthsimple", "Job Application for Senior Software Engineer at Wealthsimple") == "Wealthsimple"
    assert jw.company_name_for("lever", "clio", "Clio - Software Developer") == "Clio"
    assert jw.company_name_for("ashby", "rose-rocket", "Software Engineer @ Rose Rocket") == "Rose Rocket"
    # title that doesn't agree with the slug falls back to the slug
    assert jw.company_name_for("greenhouse", "acme", "Job Application for Dev at Something Else") == "Acme"
    assert jw.company_name_for("workday", "https://bigco.wd5.myworkdayjobs.com/External", "x") == "Bigco"

def test_merge():
    rows = [
        {"company": "Ramp", "board": "ashby", "slug": "ramp", "status": "ok", "region": "Toronto / GTA"},
        {"company": "Clio", "board": "auto", "slug": "", "status": "not_found", "region": "Vancouver / BC"},
        {"company": "Shopify", "board": "other", "careers_url": "https://shopify.com/careers", "region": "Toronto / GTA"},
        {"company": "Stripe", "board": "greenhouse", "slug": "stripe", "status": "ok"},
    ]
    state = {"resolved": {"Benchling": {"provider": "greenhouse", "slug": "benchling"}}, "companies": {"Clio": {"result": "NOT FOUND"}}}
    found = [("ashby", "Ramp", "x", "u"), ("lever", "clio", "Clio - SWE", "u"), ("greenhouse", "benchling", "", "u"),
             ("greenhouse", "newco", "Job Application for SWE at NewCo", "u"), ("lever", "stripe", "Stripe - SWE", "u"),
             ("ashby", "newco", "Dev @ NewCo", "u")]
    added, updated = jw.merge_found_boards(state, rows, found, "2026-11-21")
    assert [r["company"] for r in updated] == ["Clio"] and rows[1]["board"] == "lever" and rows[1]["status"] == ""
    assert "Clio" not in state["companies"]
    names = [r["company"] for r in added]
    assert names == ["NewCo"], names  # same company on a second board is not added twice
    assert all(r["region"] == "Found by search" and r["enabled"] == "yes" for r in added)
    # stripe already has a working greenhouse board: untouched
    assert rows[3]["board"] == "greenhouse"
    # second time: nothing new
    a2, u2 = jw.merge_found_boards(state, rows, found, "2026-11-21")
    assert not a2 and not u2
    # review mode
    a3, _ = jw.merge_found_boards(state, rows, [("lever", "zeta", "", "u")], "d", mode="review")
    assert a3[0]["enabled"] == "no"

def test_posted_from():
    d = jw.posted_from("2026-01-05")
    assert d == dt.datetime(2026, 1, 5, 23, 59, tzinfo=dt.timezone.utc)
    assert jw.posted_from("2026-01-05T10:00:00-05:00").astimezone(dt.timezone.utc).hour == 15
    today = dt.datetime.now(dt.timezone.utc).date().isoformat()
    assert jw.posted_from(today) <= dt.datetime.now(dt.timezone.utc)

JSONLD_PAGE = """<html><head><script type="application/ld+json">
{"@context":"https://schema.org","@graph":[{"@type":"Organization","name":"X"},
{"@type":"JobPosting","title":"Senior Software Engineer, Frontend","datePosted":"2026-11-20T14:00:00Z",
"validThrough":"2099-01-01","description":"&lt;p&gt;We use React and TypeScript&lt;/p&gt;",
"jobLocation":[{"@type":"Place","address":{"@type":"PostalAddress","addressLocality":"Toronto","addressRegion":"ON","addressCountry":"CA"}}],
"hiringOrganization":{"name":"X"}}]}
</script></head><body><a href="/careers/jobs/123-software-engineer">Software Engineer</a></body></html>"""

MICRO_PAGE = """<html><body><div itemscope itemtype="http://schema.org/JobPosting">
<h1 itemprop="title">Full Stack Developer</h1><meta itemprop="datePosted" content="2026-11-21">
<div itemprop="jobLocation" itemscope itemtype="http://schema.org/Place"><span itemprop="addressLocality">Vancouver</span>
<span itemprop="addressRegion">BC</span><meta itemprop="addressCountry" content="CA"></div>
<div itemprop="description"><p>Node.js and <b>React</b> work</p><div>nested</div></div></div></body></html>"""

def test_jobs_on_page():
    jobs = jw.jobs_on_page(jw.parse_page(JSONLD_PAGE), "https://x.com/careers/jobs/1")
    assert len(jobs) == 1
    j = jobs[0]
    assert j["title"] == "Senior Software Engineer, Frontend" and j["locations"] == ["Toronto, ON, Canada"]
    assert "React and TypeScript" in j["text"] and j["posted"].day == 20
    m = jw.jobs_on_page(jw.parse_page(MICRO_PAGE), "https://y.com/jobs/2")
    assert m and m[0]["title"] == "Full Stack Developer" and m[0]["locations"] == ["Vancouver, BC, Canada"], m
    assert "React" in m[0]["text"] and "nested" in m[0]["text"]
    expired = JSONLD_PAGE.replace("2099-01-01", "2020-01-01")
    assert jw.jobs_on_page(jw.parse_page(expired), "u") == []
    remote = '<script type="application/ld+json">{"@type":"JobPosting","title":"Web Developer","jobLocationType":"TELECOMMUTE","applicantLocationRequirements":{"@type":"Country","name":"Canada"}}</script>'
    r = jw.jobs_on_page(jw.parse_page(remote), "u")
    assert r[0]["locations"] == ["Remote - Canada"]

def test_links_and_boards():
    c = "https://careers.acme.com/jobs"
    assert jw.looks_like_job_link("https://careers.acme.com/jobs/12345/software-engineer", c)
    assert jw.looks_like_job_link("https://www.acme.com/careers/job/senior-software-engineer-toronto", c)
    assert not jw.looks_like_job_link("https://careers.acme.com/jobs", c)
    assert not jw.looks_like_job_link("https://careers.acme.com/benefits", c)
    assert not jw.looks_like_job_link("https://other.com/jobs/123", c)
    assert jw.title_from_url("https://x.com/jobs/R12345/Senior-Software-Engineer") == "Senior Software Engineer"
    assert jw.title_from_url("https://x.com/job/software-engineer-ii/98765") == "software engineer ii"
    assert jw.title_from_url("https://x.com/jobs/98765") == ""
    html_ = '<iframe src="https://boards.greenhouse.io/embed/job_board?for=acme"></iframe><a href="https://boards.greenhouse.io/acme/jobs/1">x</a><a href="https://jobs.lever.co/other/1">y</a>'
    assert jw.find_boards_in_html(html_)[0] == ("greenhouse", "acme")
    assert jw.find_boards_in_html("<script>window.leverJobsOptions = {accountName: 'leverco', includeCss: true};</script> lever")[0] == ("lever", "leverco")
    assert jw.clean_url("https://a.com/jobs/1?utm_source=x&id=5#top") == "https://a.com/jobs/1?id=5"

def test_sitemap_parse():
    idx = '<?xml version="1.0"?><sitemapindex><sitemap><loc>https://a.com/sitemap-jobs.xml</loc></sitemap><sitemap><loc>https://a.com/sitemap-blog.xml</loc></sitemap></sitemapindex>'
    e = jw.sitemap_entries(idx)
    assert e[0][0] == "index" and e[0][1].endswith("jobs.xml")
    urls = '<urlset><url><loc>https://a.com/jobs/1-software-engineer</loc><lastmod>2026-11-20</lastmod></url><url><loc>https://a.com/about</loc></url></urlset>'
    e = jw.sitemap_entries(urls)
    assert e[0] == ("url", "https://a.com/jobs/1-software-engineer", jw.parse_date("2026-11-20"))

class FakeResp:
    def __init__(self, status, data): self.status_code, self._d, self.text = status, data, json.dumps(data)
    def json(self): return self._d

class FakeSession:
    def __init__(self, resp): self.resp, self.calls = resp, []
    def post(self, url, **kw): self.calls.append(("POST", url, kw)); return self.resp
    def get(self, url, **kw): self.calls.append(("GET", url, kw)); return self.resp

def test_search_page(monkeypatch):
    if True:
        fs = FakeSession(FakeResp(200, {"organic": [{"link": "https://jobs.lever.co/a/1", "title": "A &amp; B - SWE"}]}))
        monkeypatch.setattr(discovery, "session", lambda: fs)
        out = jw.search_page("serper", "k", "q", 2, "day", "ca")
        assert out == [("https://jobs.lever.co/a/1", "A & B - SWE")]
        assert fs.calls[0][2]["json"] == {"q": "q", "num": 10, "page": 2, "tbs": "qdr:d", "gl": "ca"}
        assert fs.calls[0][2]["headers"]["X-API-KEY"] == "k"
        fs = FakeSession(FakeResp(200, {"web": {"results": [{"url": "https://jobs.ashbyhq.com/x/1", "title": "t"}]}}))
        monkeypatch.setattr(discovery, "session", lambda: fs)
        assert jw.search_page("brave", "k", "q", 1, "week")[0][0].startswith("https://jobs.ashbyhq.com")
        assert fs.calls[0][2]["params"]["freshness"] == "pw"
        fs = FakeSession(FakeResp(200, {"organic_results": [{"link": "https://boards.greenhouse.io/z/jobs/1", "title": "t"}]}))
        monkeypatch.setattr(discovery, "session", lambda: fs)
        assert jw.search_page("serpapi", "k", "q", 3, "any")[0][0].endswith("/1")
        assert fs.calls[0][2]["params"]["start"] == 20 and "tbs" not in fs.calls[0][2]["params"]
        fs = FakeSession(FakeResp(200, {"error": "Google hasn't returned any results for this query."}))
        monkeypatch.setattr(discovery, "session", lambda: fs)
        assert jw.search_page("serpapi", "k", "q", 1, "day") == []
        for code in (401, 429, 500):
            monkeypatch.setattr(discovery, "session", lambda c=code: FakeSession(FakeResp(c, {"message": "x"})))
            with pytest.raises(jw.SearchError):
                jw.search_page("serper", "k", "q", 1, "day")

def test_config_coercion():
    tomllib = jw.tomllib
    doc = tomllib.loads('''
[jobs]
titles = "React Developer"
must_mention = []
max_age_hours = "48"
tittles = ["typo"]
[search]
enabled = "no"
[places]
[[places.region]]
name = "Anywhere"
match = "Canada"
[advanced]
parallel_requests = 4
''')
    cfg, unknown = jw._from_toml(doc)
    assert cfg["titles_include"] == ["React Developer"] and cfg["require_stack_match"] is False
    assert cfg["max_posted_age_hours"] == 48 and cfg["search"]["enabled"] is False
    assert cfg["regions"] == [{"name": "Anywhere", "color": None, "match": ["Canada"]}]
    assert cfg["parallel_requests"] == 4 and unknown == ["tittles in [jobs]"]
    try:
        jw._from_toml(tomllib.loads('[jobs]\nmax_age_hours = "soon"'))
        assert False
    except jw.ConfigError as e:
        assert "max_age_hours in [jobs]" in str(e)



def test_slug_variants_and_names():
    assert jw.slug_variants("Jane (Jane App)") == ["jane", "janeapp", "jane-app"]
    assert jw.slug_variants("Rose Rocket") == ["roserocket", "rose-rocket", "Rose Rocket"]
    assert {"salesforce", "slack"} <= jw.name_keys("Slack (Salesforce)")
    assert jw.guess_region("Burnaby, BC") == "Vancouver / BC"
    assert jw.guess_region("San Francisco, CA") == "California"


def test_run_lock_is_exclusive(tmp_path):
    first, second = jw.RunLock(tmp_path / "x.lock"), jw.RunLock(tmp_path / "x.lock")
    assert first.acquire()
    assert not second.acquire()
    with pytest.raises(jw.Busy):
        with jw.RunLock(tmp_path / "x.lock"):
            pass
    first.release()
    assert second.acquire()
    second.release()


def test_events_reach_listeners():
    seen = []
    listener = jw.on_event(lambda kind, data: seen.append((kind, data)))
    try:
        jw.emit("progress", done=1, total=2)
    finally:
        jw.off_event(listener)
    jw.emit("progress", done=2, total=2)
    assert seen == [("progress", {"done": 1, "total": 2})]


def test_guesses_back_off_when_a_site_asks_to_slow_down(monkeypatch):
    calls = []

    def workable(slug, host=None, auto=False):
        calls.append(("workable", slug))
        jw.LIMITER.refused("workable.com")
        raise jw.RateLimited("workable.com asked to slow down")

    def lever(slug, host=None, auto=False):
        calls.append(("lever", slug))
        return None

    for p in jw.AUTO_ORDER:
        monkeypatch.setitem(jw.FETCHERS, p, lambda slug, host=None, auto=False: None)
    monkeypatch.setitem(jw.FETCHERS, "workable", workable)
    monkeypatch.setitem(jw.FETCHERS, "lever", lever)
    jw.LIMITER.reset()
    try:
        # Guessing: the refusal skips Workable instead of pausing the company, and later guesses
        # leave Workable alone for the rest of the run.
        assert jw.load_company("Acme Robotics", {}) is None
        assert jw.load_company("Other Co", {}) is None
        assert [c for c in calls if c[0] == "workable"] == [("workable", "acmerobotics")]
        # A company known to use Workable is still paused and retried next run.
        with pytest.raises(jw.RateLimited):
            jw.load_company("workable:realco", {})
    finally:
        jw.LIMITER.reset()


def test_where_data_lives(tmp_path):
    source = tmp_path / "src"
    source.mkdir()
    (source / "job_watcher.py").write_text("")
    assert jw.find_home(env="", source=source, frozen=False) == source  # running from the folder
    assert jw.find_home(env="", source=source, frozen=True) == jw.user_data_dir()  # the installed app
    assert jw.find_home(env="", source=tmp_path / "site-packages", frozen=False) == jw.user_data_dir()
    assert jw.find_home(env=str(tmp_path / "x"), source=source, frozen=False) == tmp_path / "x"


def test_installed_app_starts_with_the_defaults(tmp_path, monkeypatch):
    defaults = tmp_path / "defaults"
    defaults.mkdir()
    (defaults / "config.toml").write_text("[jobs]\nmax_age_hours = 12\n")
    (defaults / "companies.csv").write_text("company\nAcme\n")
    monkeypatch.setattr(jw.base, "defaults_dir", lambda: defaults)
    home = tmp_path / "home" / "JobWatcher"
    jw.set_up_home(home)
    assert (home / "config.toml").read_text().startswith("[jobs]")
    (home / "config.toml").write_text("# mine\n")
    jw.set_up_home(home)  # never overwrites your settings
    assert (home / "config.toml").read_text() == "# mine\n"


def test_jobwatcher_folder_command(capsys, monkeypatch):
    from jobwatcher import desktop
    monkeypatch.setattr(desktop, "open_folder", lambda path: None)
    desktop.main(["folder"])
    assert capsys.readouterr().out.strip() == str(jw.BASE)


def test_schedule_runs_owed_after_sleep_but_not_stale_ones():
    from jobwatcher.scheduler import due_slot, next_slot
    tz = dt.datetime.now().astimezone().tzinfo
    at = lambda h, m=0, day=21: dt.datetime(2026, 11, day, h, m, tzinfo=tz)  # noqa: E731
    times = ["08:00", "12:00", "16:00", "20:00"]
    assert due_slot(times, at(8, 5), at(12, 1)) == at(12)  # the 12:00 run is owed
    assert due_slot(times, at(12, 2), at(12, 30)) is None  # already done
    assert due_slot(times, at(8, 5), at(13, 40)) == at(12)  # asleep at noon: catch up once
    assert due_slot(times, None, at(12, 1)) is None  # never before your first run
    assert due_slot(times, at(20, 1, day=19), at(9, 0)) == at(8)  # away for days: one catch-up only
    assert next_slot(times, at(12, 1)) == at(16)
    assert next_slot(times, at(21, 0)) == at(8, day=22)


def test_window_icon_is_an_ico_on_windows():
    """pywebview on Windows loads the window icon with .NET's Icon class, which rejects a .png
    (and PNG-compressed small frames) and closes the app at start."""
    import struct

    from jobwatcher.desktop import window_icon

    ico = window_icon("win32")
    assert ico.suffix == ".ico" and ico.exists()
    assert window_icon("darwin").suffix == ".png" and window_icon("linux").suffix == ".png"
    data = ico.read_bytes()
    reserved, kind, count = struct.unpack("<HHH", data[:6])
    assert (reserved, kind) == (0, 1) and count > 0
    for i in range(count):
        width, _h, _c, _r, _p, _bits, size, offset = struct.unpack("<BBBBHHII", data[6 + 16 * i:22 + 16 * i])
        if width != 0:  # every size under 256px must be a plain bitmap, not PNG
            assert data[offset:offset + 8] != b"\x89PNG\r\n\x1a\n", f"{width}px frame is PNG"


def test_app_starts_without_a_terminal(monkeypatch):
    """The .exe behind the Desktop and Start menu shortcuts has no stdout or stderr. uvicorn's
    console logging setup crashed on that before anything was logged, so clicking did nothing."""
    import sys

    import uvicorn

    from jobwatcher import desktop

    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    monkeypatch.setattr(sys, "platform", "linux")  # on Windows it would also redirect pytest's own stderr
    desktop.no_console_streams()
    assert sys.stdout is not None and sys.stderr is not None
    config = desktop.server_config(uvicorn, lambda scope, receive, send: None, 1)
    assert config.log_config is None
    # What a terminal would have shown is kept in job_watcher_errors.log instead of lost.
    print("a problem worth reading", file=sys.stderr)
    sys.stderr.flush()
    from jobwatcher.base import BASE
    text = (BASE / "job_watcher_errors.log").read_text(encoding="utf-8")
    assert "Opened without a terminal" in text and "a problem worth reading" in text


def test_a_failed_start_is_shown_not_silent(monkeypatch):
    from jobwatcher import desktop

    shown = []
    monkeypatch.setattr(desktop, "open_app", lambda argv: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(desktop, "fail", lambda text: shown.append(text))
    desktop.main([])
    assert shown and "boom" in shown[0] and "job_watcher.log" in shown[0]


def _load(path, name):
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location(name, Path(__file__).resolve().parent.parent / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


STORE = (r"C:\Users\me\AppData\Local\Microsoft\WindowsApps\PythonSoftwareFoundation.Python.3.13_qbz5n2kfra8p0"
         r"\python.exe")


def test_installer_skips_microsoft_store_python():
    """The Store build keeps files written under AppData in a private copy, so the jobwatcher
    command and the Start menu shortcut it made were invisible to the rest of Windows."""
    find = _load("install/find_python.py", "find_python")
    assert not find.usable(STORE, r"C:\Program Files\WindowsApps\PythonSoftwareFoundation.Python.3.13_x64", (3, 13))
    assert find.usable(r"C:\Users\me\AppData\Local\Programs\Python\Python312\python.exe",
                       r"C:\Users\me\AppData\Local\Programs\Python\Python312", (3, 12))
    assert not find.usable("/usr/bin/python3.9", "/usr", (3, 9))


def test_installer_remakes_a_venv_from_another_python():
    inst = _load("install/install.py", "jw_install")
    store_cfg = f"home = {STORE.rsplit(chr(92), 1)[0]}\ninclude-system-site-packages = false\nversion = 3.13.14\n"
    org = r"C:\Users\me\AppData\Local\Programs\Python\Python312"
    assert inst.store_python(STORE, "")
    assert inst.venv_needs_remaking(store_cfg, org)
    assert not inst.venv_needs_remaking(f"home = {org}\nversion = 3.12.10\n", org)
    assert inst.venv_needs_remaking(f"home = {org}\n", r"C:\Python313")
    assert inst.venv_needs_remaking("version = 3.12.10\n", org)  # no home line: can't tell, so remake


def test_a_click_hands_over_to_a_hidden_console(monkeypatch, tmp_path):
    """jobwatcher-app.exe (no terminal) starts jobwatcher.exe with its console hidden, the way a
    terminal runs it, and sends its errors to job_watcher_errors.log."""
    import subprocess
    import sys

    from jobwatcher import desktop

    scripts = tmp_path / "Scripts"
    scripts.mkdir()
    (scripts / "jobwatcher.exe").write_bytes(b"")
    started = {}

    class FakePopen:
        pid = 4321

        def __init__(self, cmd, **kw):
            started.update(cmd=cmd, **kw)

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(sys, "executable", str(scripts / "pythonw.exe"))
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(desktop, "RELAUNCHED", False)
    monkeypatch.setattr(desktop.subprocess, "Popen", FakePopen)
    assert desktop.hand_over_to_hidden_console(["--tray"]) is True
    assert started["cmd"] == [str(scripts / "jobwatcher.exe"), "--tray"]
    assert started["creationflags"] & desktop.CREATE_NO_WINDOW
    assert started["env"]["JOBWATCHER_RELAUNCHED"] == "1"
    assert started["stdout"] == subprocess.DEVNULL and started["stderr"] not in (None, subprocess.DEVNULL)

    monkeypatch.setattr(desktop, "RELAUNCHED", True)  # the copy it started doesn't hand over again
    assert desktop.hand_over_to_hidden_console([]) is False
    monkeypatch.setattr(desktop, "RELAUNCHED", False)
    monkeypatch.setattr(sys, "stdout", object())  # typed in a terminal: runs right there
    assert desktop.hand_over_to_hidden_console([]) is False


def test_installer_puts_a_shortcut_at_the_top_of_the_folder(monkeypatch, tmp_path):
    """Nobody should have to dig into .venv\\Scripts for the app: on Windows the installer adds a
    Job Watcher shortcut to the Job Watcher folder itself, as well as the Start menu and Desktop."""
    inst = _load("install/install.py", "jw_install_shortcuts")
    inst.configure(tmp_path)
    monkeypatch.setattr(inst, "windows_folder", lambda name: tmp_path / name)
    folders = inst.windows_shortcut_folders()
    assert tmp_path.resolve() in folders and tmp_path / "Programs" in folders and tmp_path / "Desktop" in folders


def test_uninstall_removes_a_link_to_the_app_but_not_the_app(tmp_path):
    inst = _load("install/install.py", "jw_install_remove")
    bundle = tmp_path / "Applications" / "Job Watcher.app"
    (bundle / "Contents").mkdir(parents=True)
    link = tmp_path / "Job Watcher.app"
    try:
        link.symlink_to(bundle, target_is_directory=True)
    except OSError:  # Windows without symlink rights; the Mac installer is the one that makes these
        pytest.skip("can't make symbolic links here")
    shortcut = tmp_path / "Job Watcher.lnk"
    shortcut.write_bytes(b"lnk")
    inst.remove_shortcut(link)
    inst.remove_shortcut(shortcut)
    assert not link.exists() and not link.is_symlink() and not shortcut.exists()
    assert (bundle / "Contents").is_dir()  # removing the link left the app itself alone
    inst.remove_shortcut(bundle)
    assert not bundle.exists()
