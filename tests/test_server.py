"""The desktop app's local server: its guard, what it reads, and runs started, watched and
cancelled through it. Runs against a real server on localhost with fake job boards."""
import csv
import os
import json
import shutil
import socket
import threading
import time

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
                                     "React and TypeScript on the web app"))
    if slug.startswith("filler"):
        return jw.result(slug, board(slug, 1, "Accountant", "Toronto, ON", "Excel"))
    return None


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
    patch.setitem(jw.FETCHERS, "greenhouse", fake_greenhouse)
    for key in ("SERPER_API_KEY", "BRAVE_API_KEY", "SERPAPI_API_KEY"):
        patch.delenv(key, raising=False)  # no web search from these tests

    app = create_app(TOKEN)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    while not server.started:
        time.sleep(0.02)
    with httpx.Client(base_url=f"http://127.0.0.1:{port}", headers={"X-Token": TOKEN}, timeout=20) as c:
        c.app = app
        yield c
    app.state.runs.wait(30)
    server.should_exit = True
    app.state.detach()
    patch.undo()
    for name in FILES:
        (HOME / name).unlink(missing_ok=True)
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
    seen = []

    def listen():
        with httpx.stream("GET", f"{client.base_url}/api/events", params={"token": TOKEN}, timeout=20) as r:
            for line in r.iter_lines():
                if line.startswith("data: "):
                    seen.append(json.loads(line[6:])["kind"])
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
    original = (HOME / "config.toml").read_text(encoding="utf-8")
    try:
        got = client.get("/api/settings").json()
        jobs = next(s for s in got["sections"] if s["id"] == "jobs")
        assert next(f for f in jobs["fields"] if f["key"] == "max_age_hours")["value"] == 24
        preview = client.post("/api/settings/preview", json={"changes": {"jobs": {"max_age_hours": 48}}})
        assert "48" in preview.json()["explain"]
        assert (HOME / "config.toml").read_text(encoding="utf-8") == original  # preview saves nothing
        saved = client.put("/api/settings", json={"changes": {"jobs": {"max_age_hours": 48, "skip_titles": ["Staff"]},
                                                              "search": {"enabled": False}}})
        assert saved.status_code == 200
        text = (HOME / "config.toml").read_text(encoding="utf-8")
        assert "max_age_hours = 48" in text and "# Only add jobs that were posted" in text
        cfg = jw.load_config()
        assert cfg["max_posted_age_hours"] == 48 and cfg["titles_exclude"] == ["Staff"] and not cfg["search"]["enabled"]
        bad = client.put("/api/settings", json={"changes": {"search": {"provider": "google"}}})
        assert bad.status_code == 400 and "serper" in bad.json()["detail"]
        bad = client.put("/api/settings", json={"changes": {"places": {"region": [{"name": "X", "match": []}]}}})
        assert bad.status_code == 400
    finally:
        (HOME / "config.toml").write_text(original, encoding="utf-8")


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
        # With no password store on the computer, keys go to .env
        monkeypatch.setattr(keys, "_keyring", lambda: None)
        assert client.put("/api/keys/HUNTER_API_KEY", json={"value": "abc"}).json()["saved"] == ".env"
        assert "HUNTER_API_KEY=abc" in env.read_text()
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
