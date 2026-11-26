"""The desktop app's local server: its guard, what it reads, and runs started, watched and
cancelled through it. Runs against a real server on localhost with fake job boards."""
import csv
import gc
import re
import os
import json
import shutil
import socket
import threading
from pathlib import Path
import time
from contextlib import closing

import pytest

fastapi = pytest.importorskip("fastapi")
httpx = pytest.importorskip("httpx")
uvicorn = pytest.importorskip("uvicorn")

import jobwatcher as jw  # noqa: E402
from jobwatcher.server import create_app  # noqa: E402

HOME = jw.BASE
TOKEN = "test-token"
FILES = ("companies.csv", "job_watcher_state.json", "postings.db", "postings.db-wal", "postings.db-shm",
         "job_tracker.xlsx", "all_postings.csv", "job_watcher_state.before-database.json")
FRESH = jw.dt.datetime.now(jw.dt.timezone.utc) - jw.dt.timedelta(hours=2)
SLOW = threading.Event()


def board(slug, n, title, loc, text):
    return [{"key": f"greenhouse:{slug}:{i}", "title": f"{title} {i}" if i else title, "locations": [loc],
             "url": f"https://x/{slug}/{i}", "posted": FRESH, "text": text} for i in range(n)]


def fake_greenhouse(slug, host=None, auto=False):
    if SLOW.is_set():
        time.sleep(0.4)
    if slug == "maple":
        return jw.result(slug, board("maple", 1, "Senior Frontend Developer", "Vancouver, BC",
                                     "React and TypeScript on the web app. The salary range is $120,000 to "
                                     "$150,000 CAD. Hybrid, 3 days a week in the office."))
    if slug.startswith("filler"):
        return jw.result(slug, board(slug, 1, "Accountant", "Toronto, ON", "Excel"))
    return None


def remove(path, tries=20):
    """Delete a test file. Windows refuses while anything has it open, so wait a moment for that."""
    for _ in range(tries):
        try:
            path.unlink(missing_ok=True)
            return
        except PermissionError:
            time.sleep(0.25)
    path.unlink(missing_ok=True)


@pytest.fixture(scope="module")
def client():
    saved = HOME / "saved-by-test-server"
    saved.mkdir(exist_ok=True)
    for name in FILES:
        if (HOME / name).exists():
            shutil.move(HOME / name, saved / name)
    rows = [dict(priority=1, region="Vancouver / BC", company="Maple Co", board="greenhouse", slug="maple",
                 enabled="yes", status="ok")]
    rows += [dict(priority=5, region="Toronto / GTA", company=f"Filler {i:02d}", board="greenhouse",
                  slug=f"filler{i:02d}", enabled="yes", status="ok") for i in range(24)]
    with open(HOME / "companies.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    patch = pytest.MonkeyPatch()
    for provider in list(jw.FETCHERS):  # no real job sites from these tests
        patch.setitem(jw.FETCHERS, provider, lambda slug, host=None, auto=False: None)
    patch.setitem(jw.FETCHERS, "greenhouse", fake_greenhouse)
    for key in ("SERPER_API_KEY", "BRAVE_API_KEY", "SERPAPI_API_KEY"):
        patch.delenv(key, raising=False)  # no web search from these tests

    app = create_app(TOKEN)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    serving = threading.Thread(target=server.run, daemon=True)
    serving.start()
    while not server.started:
        time.sleep(0.02)
    with httpx.Client(base_url=f"http://127.0.0.1:{port}", headers={"X-Token": TOKEN}, timeout=20) as c:
        c.app = app
        yield c
    app.state.runs.wait(30)
    server.should_exit = True
    serving.join(10)  # the server finishes its last request before its files go
    app.state.detach()
    patch.undo()
    gc.collect()  # any connection nobody closed is closed now, so Windows lets go of the file
    for name in FILES:
        remove(HOME / name)
        if (saved / name).exists():
            shutil.move(saved / name, HOME / name)
    saved.rmdir()


def wait_for_run(c, status=("finished", "cancelled", "failed", "skipped"), timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        run = c.get("/api/runs/current").json()
        if run and run["status"] in status:
            return run
        time.sleep(0.1)
    raise AssertionError(f"run did not reach {status}")


def test_guard(client):
    assert client.get("/api/summary", headers={"X-Token": "wrong"}).status_code == 401
    assert httpx.get(f"{client.base_url}/api/summary").status_code == 401
    assert client.get(f"/api/summary?token={TOKEN}", headers={"X-Token": ""}).status_code == 200
    # A web page that points a made-up host name at 127.0.0.1 (DNS rebinding) is refused.
    assert client.get("/api/summary", headers={"Host": "attacker.example"}).status_code == 403
    assert client.get("/api/nothing-here").status_code == 404


def test_full_run_and_reads(client):
    started = client.post("/api/runs", json={"mode": "full"})
    assert started.status_code == 202
    run = wait_for_run(client)
    assert run["status"] == "finished" and run["total"] == 25 and run["done"] == 25
    assert run["new"] == ["Maple Co: Senior Frontend Developer (Canada - BC)"]

    jobs = client.get("/api/jobs").json()
    assert [(j["company"], j["level"], j["region"], j["open"]) for j in jobs] == [
        ("Maple Co", "Senior/Lead", "Canada - BC", True)]
    text = client.get("/api/description", params={"key": jobs[0]["key"]}).json()["text"]
    assert "TypeScript" in text

    summary = client.get("/api/summary").json()
    assert summary["jobs"]["open"] == 1 and summary["companies"]["results"] == {"OK": 25}
    assert summary["postings"] == {"total": 25, "open": 25} and summary["busy_elsewhere"] is False

    companies = {c["company"]: c for c in client.get("/api/companies").json()}
    assert companies["Maple Co"]["result"] == "OK" and companies["Maple Co"]["matches"] == 1
    assert companies["Filler 03"]["open"] == 1

    page = client.get("/api/postings", params={"q": "frontend", "size": 10}).json()
    assert page["total"] == 1 and page["rows"][0]["tracked"] is True
    assert client.get("/api/postings", params={"where": "Toronto"}).json()["total"] == 24
    assert isinstance(client.get("/api/log").json()["lines"], list)  # app.py adds the log file
    assert "Canada - BC" in client.get("/api/settings/explain").json()["text"]


def test_events_stream_a_refilter(client):
    seen, started = [], []

    def listen():
        with httpx.stream("GET", f"{client.base_url}/api/events", params={"token": TOKEN}, timeout=20) as r:
            for line in r.iter_lines():
                if line.startswith("data: "):
                    message = json.loads(line[6:])
                    seen.append(message["kind"])
                    if message["kind"] == "run_started":
                        started.append(message.get("run"))
                    if seen[-1] == "run_finished":
                        return

    t = threading.Thread(target=listen, daemon=True)
    t.start()
    deadline = time.monotonic() + 5
    while "hello" not in seen and time.monotonic() < deadline:
        time.sleep(0.05)
    assert client.post("/api/runs", json={"mode": "refilter"}).status_code == 202
    t.join(20)
    assert seen[0] == "hello" and "run_started" in seen and "log" in seen and seen[-1] == "run_finished"
    assert "board_done" not in seen  # re-filtering makes no requests
    # run_started carries the run, so a window learns about runs it didn't start (the scheduler's)
    assert started and started[0]["status"] == "running" and started[0]["mode"] == "refilter"
    assert wait_for_run(client)["status"] == "finished"


def test_one_run_at_a_time(client):
    held = jw.RunLock()
    assert held.acquire()  # as if the scheduled task were running
    try:
        assert client.get("/api/summary").json()["busy_elsewhere"] is True
        refused = client.post("/api/runs", json={"mode": "full"})
        assert refused.status_code == 409 and "scheduled task" in refused.json()["detail"]
    finally:
        held.release()
    assert client.post("/api/runs", json={"mode": "bogus"}).status_code == 400


def test_cancel_stops_after_boards_in_flight(client):
    SLOW.set()
    try:
        assert client.post("/api/runs", json={"mode": "full"}).status_code == 202
        assert client.post("/api/runs", json={"mode": "full"}).status_code == 409
        deadline = time.monotonic() + 10
        while client.get("/api/runs/current").json()["done"] < 1 and time.monotonic() < deadline:
            time.sleep(0.05)
        assert client.delete("/api/runs/current").status_code == 200
        run = wait_for_run(client)
    finally:
        SLOW.clear()
    assert run["status"] == "cancelled" and run["done"] == run["total"] == 25
    results = json.loads((HOME / "job_watcher_state.json").read_text())["companies"]
    retried = [n for n, c in results.items() if c["result"] == "RETRY"]
    assert retried and len(retried) < 25  # boards already being read finished; the rest wait
    assert client.delete("/api/runs/current").status_code == 404
    assert client.get("/api/runs").json()[0]["status"] == "cancelled"


# ----------------------------------------------------------------- editing (Phase 2)
def test_app_is_where_you_edit(client):
    assert jw.edits_in(jw.load_config()) == "app"
    assert client.get("/api/summary").json()["edits_in"] == "app"


def test_edit_a_job(client):
    key = client.get("/api/jobs").json()[0]["key"]
    r = client.patch("/api/jobs", params={"key": key}, json={"status": "Applied", "notes": "Referred by Sam"})
    assert r.status_code == 200 and r.json()["status"] == "Applied"
    r = client.patch("/api/jobs", params={"key": key}, json={"outreach": "Sent"})
    assert r.json()["outreach_sent"]
    assert client.patch("/api/jobs", params={"key": key}, json={"status": "Maybe"}).status_code == 400
    assert client.patch("/api/jobs", params={"key": "nope"}, json={"status": "New"}).status_code == 404
    # A run keeps the edit, and the Excel copy shows it.
    client.post("/api/runs", json={"mode": "refilter"})
    wait_for_run(client)
    job = client.get("/api/jobs").json()[0]
    assert (job["status"], job["notes"], job["outreach"]) == ("Applied", "Referred by Sam", "Sent")
    assert jw.read_excel_edits(HOME / "job_tracker.xlsx")[key]["status"] == "Applied"


def test_edits_made_during_a_run_survive_it(client):
    key = client.get("/api/jobs").json()[0]["key"]
    SLOW.set()
    try:
        client.post("/api/runs", json={"mode": "full"})
        deadline = time.monotonic() + 10
        while client.get("/api/runs/current").json()["done"] < 1 and time.monotonic() < deadline:
            time.sleep(0.05)
        assert client.patch("/api/jobs", params={"key": key}, json={"status": "Interviewing"}).status_code == 200
        # companies.csv is held by the run, so a company edit is queued
        r = client.patch("/api/companies", params={"name": "Filler 05"}, json={"enabled": False, "notes": "Not hiring"})
        assert r.json()["queued"] is True
        assert client.post("/api/companies/add", json={"link": "https://jobs.lever.co/newco"}).status_code == 409
        shown = {c["company"]: c for c in client.get("/api/companies").json()}["Filler 05"]
        assert shown["pending"] and shown["enabled"] is False
    finally:
        SLOW.clear()
    wait_for_run(client)
    assert client.get("/api/jobs").json()[0]["status"] == "Interviewing"
    with open(HOME / "companies.csv", encoding="utf-8-sig") as f:
        row = {r["company"]: r for r in csv.DictReader(f)}["Filler 05"]
    assert (row["enabled"], row["notes"]) == ("no", "Not hiring")
    assert not jw.pending_company_edits(jw.load_config())


def test_edit_add_and_import_companies(client):
    r = client.patch("/api/companies", params={"name": "Filler 06"},
                     json={"board": "website", "careers_url": "careers.filler.example/jobs", "slug": ""})
    assert r.json() == {"saved": True, "queued": False}
    c = {c["company"]: c for c in client.get("/api/companies").json()}["Filler 06"]
    assert (c["board"], c["careers_url"], c["status"]) == ("website", "https://careers.filler.example/jobs", "")
    assert client.patch("/api/companies", params={"name": "Filler 06"}, json={"board": "monster"}).status_code == 400
    assert client.patch("/api/companies", params={"name": "Nobody"}, json={"notes": "x"}).status_code == 404

    added = client.post("/api/companies/add", json={"link": "https://jobs.lever.co/newco/abc", "name": "New Co"})
    assert added.status_code == 200 and "New Co" in added.json()["message"]

    sheet = "Company,Careers URL,Location\nMaple Co,,Vancouver\nPine Labs,https://jobs.ashbyhq.com/pinelabs,Toronto\n"
    preview = client.post("/api/companies/import", params={"filename": "list.csv"}, content=sheet.encode())
    body = preview.json()
    assert body["rows"] == 2 and [a["company"] for a in body["added"]] == ["Pine Labs"]
    names = [c["company"] for c in client.get("/api/companies").json()]
    assert "Pine Labs" not in names  # a preview saves nothing
    assert client.post(f"/api/companies/import/{body['id']}").json() == {"added": 1, "updated": 0}
    assert "Pine Labs" in [c["company"] for c in client.get("/api/companies").json()]
    assert client.post("/api/companies/import", params={"filename": "list.pdf"}, content=b"x").status_code == 400


def test_settings_round_trip(client):
    original = (HOME / "my_settings.toml").read_text(encoding="utf-8")
    try:
        got = client.get("/api/settings").json()
        jobs = next(s for s in got["sections"] if s["id"] == "jobs")
        assert next(f for f in jobs["fields"] if f["key"] == "max_age_hours")["value"] == 24
        preview = client.post("/api/settings/preview", json={"changes": {"jobs": {"max_age_hours": 48}}})
        assert "48" in preview.json()["explain"]
        summary = preview.json()["summary"]
        assert next(c for c in summary["checks"] if c["id"] == "age")["text"] == "Within the last 2 days"
        assert next(c for c in got["summary"]["checks"] if c["id"] == "age")["text"] == "Within the last 24 hours"
        assert {c["id"] for c in summary["checks"]} >= {"titles", "stack", "places", "age"}
        assert {"on", "provider", "key", "searches", "queries"} <= set(summary["search"])
        assert (HOME / "my_settings.toml").read_text(encoding="utf-8") == original  # preview saves nothing
        saved = client.put("/api/settings", json={"changes": {"jobs": {"max_age_hours": 48, "skip_titles": ["Staff"]},
                                                              "search": {"enabled": False}}})
        assert saved.status_code == 200
        text = (HOME / "my_settings.toml").read_text(encoding="utf-8")
        assert "max_age_hours = 48" in text and "# Only add jobs that were posted" in text
        cfg = jw.load_config()
        assert cfg["max_posted_age_hours"] == 48 and cfg["titles_exclude"] == ["Staff"] and not cfg["search"]["enabled"]
        bad = client.put("/api/settings", json={"changes": {"search": {"provider": "google"}}})
        assert bad.status_code == 400 and "serper" in bad.json()["detail"]
        bad = client.put("/api/settings", json={"changes": {"places": {"region": [{"name": "X", "match": []}]}}})
        assert bad.status_code == 400
    finally:
        (HOME / "my_settings.toml").write_text(original, encoding="utf-8")


class MemoryKeyring:
    """Stands in for Windows Credential Manager."""
    priority = 1

    def __init__(self):
        self.saved = {}

    def get_password(self, service, name):
        return self.saved.get((service, name))

    def set_password(self, service, name, value):
        self.saved[(service, name)] = value

    def delete_password(self, service, name):
        self.saved.pop((service, name), None)


def test_keys(client, monkeypatch):
    from jobwatcher import keys
    store = MemoryKeyring()
    monkeypatch.setattr(keys, "_keyring", lambda: store)
    monkeypatch.delenv("HUNTER_API_KEY", raising=False)
    monkeypatch.setitem(keys.SOURCES, "HUNTER_API_KEY", None)
    env = HOME / ".env"
    before = env.read_text() if env.exists() else None
    env.write_text("HUNTER_API_KEY=old-key-in-env\nOTHER=1\n")
    try:
        r = client.put("/api/keys/HUNTER_API_KEY", json={"value": "  new-key-1234 "})
        assert r.json()["saved"] == "password store"
        assert store.saved[("Job Watcher", "HUNTER_API_KEY")] == "new-key-1234"
        assert "HUNTER_API_KEY" not in env.read_text() and "OTHER=1" in env.read_text()  # moved out of .env
        status = {k["name"]: k for k in client.get("/api/keys").json()["keys"]}["HUNTER_API_KEY"]
        assert status["set"] and status["source"] == "password store" and status["preview"].endswith("1234")
        assert "new-key" not in json.dumps(client.get("/api/keys").json())  # never sent back whole
        assert client.put("/api/keys/NOT_A_KEY", json={"value": "x"}).status_code == 400
        client.delete("/api/keys/HUNTER_API_KEY")
        assert ("Job Watcher", "HUNTER_API_KEY") not in store.saved
        # With no password store on the computer, keys go to .env, but only once you agree to a plain file
        monkeypatch.setattr(keys, "_keyring", lambda: None)
        r = client.put("/api/keys/HUNTER_API_KEY", json={"value": "abc"})
        assert r.status_code == 400 and "plain text file" in r.json()["detail"]
        assert "HUNTER_API_KEY=abc" not in (env.read_text() if env.exists() else "")
        assert client.put("/api/keys/HUNTER_API_KEY", json={"value": "abc", "plain_file_ok": True}).json()["saved"] == ".env"
        assert "HUNTER_API_KEY=abc" in env.read_text()
        if os.name == "posix":
            assert env.stat().st_mode & 0o077 == 0  # your account only
        assert client.put("/api/keys/HUNTER_API_KEY", json={"value": "abc\nEVIL=1", "plain_file_ok": True}).status_code == 400
    finally:
        os.environ.pop("HUNTER_API_KEY", None)
        if before is None:
            env.unlink(missing_ok=True)
        else:
            env.write_text(before)


def test_second_launch_finds_the_open_app(client, monkeypatch):
    from jobwatcher import desktop
    focused = []
    client.app.state.focus = lambda: focused.append(True)
    port = int(str(client.base_url).rsplit(":", 1)[1])
    marker = desktop.instance_file()
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps({"port": port, "token": TOKEN, "pid": 1}))
    try:
        assert desktop.running_instance() == (port, TOKEN)
        assert client.post("/api/window/focus").json() == {"focused": True}
        time.sleep(0.2)
        assert focused
        marker.write_text(json.dumps({"port": port, "token": "stale", "pid": 1}))
        assert desktop.running_instance() is None  # a leftover file from a closed app is ignored
    finally:
        marker.unlink(missing_ok=True)
        client.app.state.focus = None


# ----------------------------------------------------------------- Phases 3 and 4
def test_run_history_and_targeted_runs(client):
    assert client.post("/api/runs", json={"mode": "check"}).status_code == 400  # which company?
    assert client.post("/api/runs", json={"mode": "check", "company": "Maple Co"}).status_code == 202
    run = wait_for_run(client)
    assert run["status"] == "finished" and run["total"] == 1  # read just that company
    history = client.get("/api/runs").json()
    assert history[0]["label"] == "Checked Maple Co" and history[0]["source"] == "app"
    assert history[0]["summary"]["boards_read"] == 1 and history[0]["summary"]["companies"] == ["Maple Co"]
    assert any(h["status"] == "cancelled" for h in history)  # earlier runs are kept

    client.post("/api/runs", json={"mode": "career_pages"})
    run = wait_for_run(client)
    assert run["status"] == "finished" and (run["total"] or 0) < 25  # only companies with no board
    from jobwatcher import runner
    real = runner.refresh_metrics
    runner.refresh_metrics = lambda cfg, rows, force=False: True
    try:
        client.post("/api/runs", json={"mode": "scores"})
        assert wait_for_run(client)["status"] == "finished"
    finally:
        runner.refresh_metrics = real
    assert client.get("/api/runs").json()[0]["label"] == "Refreshed company signals"


def test_applied_date_is_stamped(client):
    key = client.get("/api/jobs").json()[0]["key"]
    client.patch("/api/jobs", params={"key": key}, json={"status": "New"})
    client.patch("/api/jobs", params={"key": key}, json={"status": "Applied"})
    assert client.get("/api/jobs").json()[0]["applied"]


def test_outreach_from_contacts_to_follow_up(client, monkeypatch):
    import outreach
    from jobwatcher import outreach_app
    (HOME / "about_me.md").write_text("# About me\nName: Sam Lee\nSignature: Sam Lee\nBuilt React apps.\n")
    monkeypatch.setenv("HUNTER_API_KEY", "h-key")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "a-key")
    found = {"emails": [
        {"value": "ceo@maple.example", "first_name": "Ada", "last_name": "Boss", "position": "Chief Executive Officer",
         "type": "personal", "confidence": 99, "verification": {"status": "valid"}},
        {"value": "rita@maple.example", "first_name": "Rita", "last_name": "Recruit", "position": "Technical Recruiter",
         "type": "personal", "confidence": 90, "verification": {"status": "valid"}},
        {"value": "eli@maple.example", "first_name": "Eli", "last_name": "Lead", "position": "Engineering Manager",
         "type": "personal", "confidence": 80, "verification": {"status": "valid"}}], "domain": "maple.example"}
    monkeypatch.setattr(outreach.Hunter, "domain_search", lambda self, **kw: (self._spend(1), found)[1])
    prompts = []

    def fake_claude(key, payload):
        prompts.append(payload)
        text = payload["messages"][0]["content"][-1]["text"]
        people = re.findall(r"<([^<>@\s]+@[^<>\s]+)>", text)
        return [{"to": p, "subject": "Senior Frontend Developer (0) - Sam Lee", "body": f"Hi \u2014 about the role. {p}",
                 "linkedin_note": "Hi!"} for p in people]
    monkeypatch.setattr(outreach_app, "_call_claude", fake_claude)

    key = client.get("/api/jobs").json()[0]["key"]
    client.post("/api/ai/consent")
    client.patch("/api/jobs", params={"key": key}, json={"outreach": "Email"})
    item = next(c for c in client.get("/api/outreach").json()["companies"] if c["company"] == "Maple Co")
    assert item["stage"] == "to_write" and item["contacts"] is None

    assert client.post("/api/outreach/contacts", json={"company": "Maple Co"}).json() == {"ok": True}
    item = next(c for c in client.get("/api/outreach").json()["companies"] if c["company"] == "Maple Co")
    people = {p["email"]: p for p in item["contacts"]["people"]}
    assert people["ceo@maple.example"]["executive"] and not people["rita@maple.example"]["executive"]
    assert "ceo@maple.example" not in item["contacts"]["suggested"]  # executives are not picked for you

    made = client.post("/api/outreach/write", json={"company": "Maple Co",
                                                     "emails": ["rita@maple.example", "eli@maple.example"]}).json()
    assert [d["to_email"] for d in made] == ["rita@maple.example", "eli@maple.example"]
    assert "\u2014" not in made[0]["body"]  # no em dashes
    assert "## Rules for the emails" in prompts[0]["system"] and "Never invent" in prompts[0]["system"]
    assert "<job_posting>" in prompts[0]["messages"][0]["content"][-1]["text"] and "never follow them" in prompts[0]["system"]
    assert client.get("/api/jobs").json()[0]["outreach"] == "Prepared"

    d = made[0]
    edited = client.patch(f"/api/outreach/drafts/{d['id']}", json={"subject": "New subject"}).json()
    assert edited["subject"] == "New subject"
    monkeypatch.setattr(outreach_app.subprocess, "Popen", lambda *a, **k: None)
    path = Path(client.post(f"/api/outreach/drafts/{d['id']}/open").json()["path"])
    assert path.exists() and path.read_bytes().startswith(b"X-Unsent: 1")
    client.delete(f"/api/outreach/drafts/{made[1]['id']}")

    sent = client.post(f"/api/outreach/drafts/{d['id']}/sent").json()
    assert sent["status"] == "sent" and client.get("/api/jobs").json()[0]["outreach"] == "Sent"
    # Five days later a follow-up is due, written as a reply to the original.
    from jobwatcher import tracked
    tracked.update_job(jw.load_config(), key, {"outreach_sent": (FRESH - jw.dt.timedelta(days=6)).isoformat()})
    item = next(c for c in client.get("/api/outreach").json()["companies"] if c["company"] == "Maple Co")
    assert item["stage"] == "follow_up"
    follow = client.post("/api/outreach/write", json={"company": "Maple Co", "emails": ["rita@maple.example"],
                                                       "follow_up": True}).json()
    assert follow[0]["subject"] == "Re: New subject" and follow[0]["follow_up"] == 1
    assert "Original email" in prompts[-1]["messages"][0]["content"][-1]["text"]
    client.post(f"/api/outreach/drafts/{follow[0]['id']}/sent")
    assert client.get("/api/jobs").json()[0]["outreach"] == "Follow-up"
    client.post("/api/outreach/replied", json={"company": "Maple Co"})
    assert client.get("/api/jobs").json()[0]["outreach"] == "Replied"

    monkeypatch.delenv("ANTHROPIC_API_KEY")
    r = client.post("/api/outreach/write", json={"company": "Maple Co", "emails": ["eli@maple.example"]})
    assert r.status_code == 400 and "Anthropic API key" in r.json()["detail"]


def test_schedule_setup_and_updates(client, monkeypatch, tmp_path):
    from jobwatcher import updates
    s = client.get("/api/schedule").json()
    assert s["enabled"] and s["times"] == ["08:00", "12:00", "16:00", "20:00"] and s["next"]
    assert client.post("/api/schedule/pause", json={"paused": True}).json()["paused"] is True
    assert client.app.state.scheduler.tick() is False  # paused
    client.post("/api/schedule/pause", json={"paused": False})
    assert client.get("/api/setup").json()["needed"] is False  # runs have happened
    assert client.post("/api/setup/done").json() == {"needed": False}

    class Reply:
        status_code = 200

        def json(self):
            return {"tag_name": "v9.0.0", "html_url": "https://github.com/x/releases/v9.0.0"}
    monkeypatch.setattr(updates.requests, "get", lambda *a, **k: Reply())
    monkeypatch.setattr(updates, "kind", lambda: "installed")
    u = client.get("/api/update", params={"force": True}).json()
    assert u["newer"] is True and u["latest"] == "v9.0.0" and u["current"] == jw.__version__
    assert u["installed"] and not u["can_update"]
    r = client.post("/api/update/apply")
    assert r.status_code == 400 and "installer" in r.json()["detail"]
    monkeypatch.setattr(updates, "apply", lambda: {"from": "abc1234", "to": "def5678", "files": 3, "packages": False})
    assert client.post("/api/update/apply").json() == {"from": "abc1234", "to": "def5678", "files": 3,
                                                       "packages": False, "restart": False}
    # Run now's "Update, then run": the new version starts that run once it opens
    from jobwatcher.server import meta_value, set_meta_value
    cfg = jw.load_config()
    client.post("/api/update/apply", json={"then_run": "full"})
    assert meta_value(cfg, "run_after_update") == "full"
    set_meta_value(cfg, "run_after_update", "")
    client.post("/api/update/apply", json={"then_run": "scores"})  # only runs that read the boards
    assert meta_value(cfg, "run_after_update") == ""

    if jw.sys.platform.startswith("linux"):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        assert client.post("/api/startup", json={"on": True}).json() == {"startup": True}
        assert "--tray" in (tmp_path / "autostart" / "jobwatcher.desktop").read_text()
        assert client.post("/api/startup", json={"on": False}).json() == {"startup": False}
        # starting at sign-in only makes sense when Pathfinder stays in the tray; saving settings
        # with that off (the default) turns it off again
        assert client.get("/api/schedule").json()["keep_in_tray"] is False
        client.post("/api/startup", json={"on": True})
        original = (HOME / "my_settings.toml").read_text(encoding="utf-8")
        try:
            assert client.put("/api/settings", json={"changes": {"schedule": {"times": ["09:00"]}}}).status_code == 200
            assert client.get("/api/schedule").json()["startup"] is False
        finally:
            (HOME / "my_settings.toml").write_text(original, encoding="utf-8")
    assert client.get("/api/system/old-tasks").json()["tasks"] == []


def test_light_or_dark_and_paint(client):
    assert client.get("/api/appearance").json() == {"theme": "system", "paint": "screen"}
    assert 'data-theme-choice="system"' in client.get("/").text
    assert client.put("/api/appearance", json={"theme": "dark"}).json() == {"theme": "dark", "paint": "screen"}
    page = client.get("/jobs").text  # every screen's page arrives already dark, with no flash of light
    assert 'data-theme-choice="dark"' in page and "prefers-color-scheme" in page
    assert client.put("/api/appearance", json={"theme": "purple"}).status_code == 400
    # the paint is saved on its own, and the page arrives already wearing it
    assert client.put("/api/appearance", json={"paint": "tiger-eye"}).json() == {"theme": "dark", "paint": "tiger-eye"}
    assert 'data-paint-choice="tiger-eye"' in client.get("/").text
    assert client.put("/api/appearance", json={"paint": "chartreuse"}).status_code == 400
    assert client.put("/api/appearance", json={}).status_code == 400
    assert client.put("/api/appearance", json={"theme": "system", "paint": "screen"}).json() == {"theme": "system", "paint": "screen"}


def test_signals_fit_tailor_and_pipeline(client, monkeypatch):
    from jobwatcher import ai
    jobs = client.get("/api/jobs").json()
    maple = next(j for j in jobs if j["company"] == "Maple Co")
    assert maple["pay"]["min"] == 120000 and maple["pay"]["currency"] == "CAD"
    assert maple["workplace"] == "hybrid" and maple["fit"] is None and maple["connections"] == 0

    # Claude: no key, then a fake answer
    r = client.post("/api/jobs/fit", params={"key": maple["key"]})
    assert r.status_code == 400 and "Anthropic API key" in r.json()["detail"]
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    calls = []

    def fake(payload, timeout=180):
        calls.append(payload)
        if payload["tool_choice"]["name"] == "record_fit":
            return {"verdict": "APPLY", "score": 82, "summary": "Strong React match \u2014 apply.",
                    "strengths": ["React"], "gaps": [], "requirements": [{"text": "React", "status": "MET"}]}
        return {"tex": "\\documentclass{article}\\begin{document}Sam \u2014 Lee\\end{document}",
                "verdict": "APPLY", "numbers_to_find": ["users served"], "notes": "Moved React first."}
    monkeypatch.setattr(ai, "tool_call", fake)
    from jobwatcher.config import load_config
    from jobwatcher.tracked import connect, set_meta
    db = connect(load_config())
    with db:
        set_meta(db, "ai_consent", "")  # as if Claude had never been used here
    db.close()
    r = client.post("/api/jobs/fit", params={"key": maple["key"]})
    assert r.status_code == 400 and "your OK" in r.json()["detail"] and not calls  # nothing sent before you agree
    assert client.get("/api/ai").json()["consented"] is None
    assert client.post("/api/ai/consent").json()["consented"]
    fit = client.post("/api/jobs/fit", params={"key": maple["key"]}).json()
    sent = calls[-1]
    assert "<job_posting>" in sent["messages"][0]["content"][-1]["text"] and "never follow them" in sent["system"]
    assert fit["verdict"] == "APPLY" and fit["score"] == 82 and "\u2014" not in fit["summary"]
    assert next(j for j in client.get("/api/jobs").json() if j["key"] == maple["key"])["fit"]["score"] == 82
    assert client.get("/api/ai").json()["used"] == 1

    r = client.post("/api/jobs/tailor", params={"key": maple["key"]})
    assert r.status_code == 400 and "resume.tex" in r.json()["detail"]
    (HOME / "resume.tex").write_text("\\documentclass{article}\\begin{document}Base\\end{document}")
    monkeypatch.setattr(ai, "compiler", lambda: (None, None))
    t = client.post("/api/jobs/tailor", params={"key": maple["key"]}).json()
    assert t["tex"].startswith("resumes/") and t["pdf"] is None and t["numbers_to_find"] == ["users served"]
    made = HOME / t["tex"]
    assert "\u2014" not in made.read_text() and "Base" in calls[-1]["messages"][0]["content"][0]["text"]
    assert client.post("/api/files/open", json={"path": "config.toml"}).status_code == 404  # only tailored resumes
    assert client.post("/api/files/open", json={"path": "resumes/../config.toml"}).status_code == 404
    details = client.get("/api/jobs/details", params={"key": maple["key"]}).json()
    assert details["tailored"]["tex"] == t["tex"] and details["fit"]["requirements"][0]["status"] == "MET"

    # the monthly limit stops further requests
    assert client.put("/api/settings", json={"changes": {"ai": {"monthly_limit": 2}}}).status_code == 200
    r = client.post("/api/jobs/fit", params={"key": maple["key"]})
    assert r.status_code == 400 and "limit" in r.json()["detail"]
    client.put("/api/settings", json={"changes": {"ai": {"monthly_limit": 300}}})

    # a run keeps the fit and tailored resume
    client.post("/api/runs", json={"mode": "refilter"})
    wait_for_run(client)
    again = next(j for j in client.get("/api/jobs").json() if j["key"] == maple["key"])
    assert again["fit"]["verdict"] == "APPLY" and again["tailored"] is True

    # LinkedIn connections
    export = ("Notes:\n\"When exporting your connection data, you may notice...\"\n\n"
              "First Name,Last Name,URL,Email Address,Company,Position,Connected On\n"
              "Ava,Chen,https://www.linkedin.com/in/ava,,Maple Co Inc.,Engineering Manager,01 Mar 2025\n"
              "Raj,Patel,https://www.linkedin.com/in/raj,,Other Corp,Designer,02 Mar 2025\n")
    assert client.post("/api/connections/import", content=export.encode()).json() == {"saved": 2}
    assert client.post("/api/connections/import", content=b"name,email\n").status_code == 400
    assert client.get("/api/connections").json()["companies"] == {"Maple Co": 1}
    assert next(j for j in client.get("/api/jobs").json() if j["key"] == maple["key"])["connections"] == 1
    people = client.get("/api/jobs/details", params={"key": maple["key"]}).json()["people"]
    assert people[0]["first"] == "Ava" and people[0]["position"] == "Engineering Manager"
    client.delete("/api/connections")
    assert client.get("/api/connections").json()["total"] == 0

    # pipeline and follow-ups
    client.patch("/api/jobs", params={"key": maple["key"]}, json={"status": "Applied"})
    db = jw.connect(jw.load_config())
    with closing(db), db:  # sqlite3's own "with" commits but never closes; Windows keeps an open file locked
        row = db.execute("SELECT data FROM jobs WHERE key=?", (maple["key"],)).fetchone()
        job = json.loads(row["data"])
        old = (jw.dt.datetime.now(jw.dt.timezone.utc) - jw.dt.timedelta(days=12)).isoformat()
        job.update(applied=old, status_changed=old)
        db.execute("UPDATE jobs SET data=? WHERE key=?", (json.dumps(job), maple["key"]))
    pipe = client.get("/api/pipeline").json()
    assert pipe["by_status"]["Applied"] >= 1 and pipe["applied_total"] >= 1 and len(pipe["applied_weeks"]) == 8
    assert any(f["key"] == maple["key"] and f["kind"] == "application" for f in pipe["follow_ups"])
    assert pipe["fit"]["APPLY"] == 1
    client.post("/api/jobs/followed-up", params={"key": maple["key"]})
    assert not any(f["key"] == maple["key"] for f in client.get("/api/pipeline").json()["follow_ups"])

    # phone alerts
    r = client.post("/api/alerts/test")
    assert r.status_code == 400 and "topic" in r.json()["detail"]
    assert client.get("/api/alerts/topic").json()["topic"].startswith("jobwatcher-")


def test_feedback_details_leave_out_keys_and_your_name(client, monkeypatch):
    """A problem report can include app details and the end of the log. Pathfinder posts nothing
    itself; it only shows what GitHub's form would get, with keys and your home folder taken out."""
    from pathlib import Path
    from jobwatcher import base, feedback
    log = Path(base.LOG_PATH)
    before = log.read_text(encoding="utf-8") if log.exists() else None
    monkeypatch.setenv("SERPER_API_KEY", "serper-secret-123456")
    home = str(Path.home())
    try:
        log.write_text("older line\n" + f"Opened {home}/Pathfinder\nsearch key serper-secret-123456 failed\n"
                       "https://x.test/?api_key=abcdef123\n", encoding="utf-8")
        got = client.get("/api/feedback").json()
        assert len(feedback.recent_log(lines=1).splitlines()) == 1
    finally:
        if before is None:
            log.unlink(missing_ok=True)
        else:
            log.write_text(before, encoding="utf-8")
    assert got["repo"] == "PDixant/findJobApplications"
    assert got["details"].startswith("Pathfinder ") and "System:" in got["details"]
    assert "serper-secret-123456" not in got["log"] and "abcdef123" not in got["log"]
    assert home not in got["log"] and "~/Pathfinder" in got["log"]
