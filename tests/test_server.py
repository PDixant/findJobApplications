"""The desktop app's local server: its guard, what it reads, and runs started, watched and
cancelled through it. Runs against a real server on localhost with fake job boards."""
import csv
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
FILES = ("companies.csv", "job_watcher_state.json", "postings.db", "job_tracker.xlsx", "all_postings.csv")
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
