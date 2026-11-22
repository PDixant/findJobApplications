"""The local API behind the desktop app (app.py).

It listens on 127.0.0.1 only. Every /api call must carry the token that app.py hands to its own
window, and requests addressed to any other host name are refused, so other programs and web
pages cannot drive it. Runs started here use the same run_once as the command line, under the
same lock, so the app, a terminal and the scheduled task never run at the same time.

Phase 1 is read-only apart from starting and cancelling runs: your tracker edits still happen in
job_tracker.xlsx, and the app shows them.
"""
from __future__ import annotations

import asyncio
import copy
import hmac
import sqlite3
import uuid
from contextlib import asynccontextmanager

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

from .base import *  # noqa: F401,F403
from .config import *  # noqa: F401,F403
from .companies import *  # noqa: F401,F403
from .tracker import *  # noqa: F401,F403
from .runner import run_once

UI_DIR = Path(__file__).resolve().parent / "ui_dist"
ALLOWED_HOSTS = {"127.0.0.1", "localhost"}
RUN_MODES = {
    "full": "Check every company",
    "refilter": "Re-filter saved postings",
    "since": "Since your last check",
}


# ----------------------------------------------------------------- reading files safely
class Files:
    """Reads the state, companies list and tracker, re-reading a file only when it changes."""

    def __init__(self):
        self._cache = {}
        self._lock = threading.Lock()

    def _cached(self, key, paths, loader):
        stamp = []
        for p in paths:
            try:
                stamp.append(p.stat().st_mtime_ns)
            except FileNotFoundError:
                stamp.append(None)
        with self._lock:
            hit = self._cache.get(key)
            if hit and hit[0] == stamp:
                return hit[1]
        value = loader()
        with self._lock:
            self._cache[key] = (stamp, value)
        return value

    def config(self):
        def load():
            try:
                return load_config(), None
            except ConfigError as exc:
                return None, str(exc)
        return self._cached("config", [CONFIG_PATH], load)

    def state(self):
        return self._cached("state", [STATE_PATH], lambda: load_json(STATE_PATH, {}))

    def company_rows(self, cfg):
        path = companies_path(cfg)
        return self._cached("companies", [path, CONFIG_PATH], lambda: load_company_rows(cfg))

    def jobs(self, cfg):
        """Tracked jobs as the tracker shows them: saved state plus edits typed into Excel
        since the last run (the next run copies those edits into the state)."""
        excel = BASE / cfg.get("excel_file", "job_tracker.xlsx")

        def load():
            state = {"jobs": copy.deepcopy(self.state().get("jobs", {}))}
            apply_excel_edits(state, read_excel_edits(excel), dt.datetime.now(dt.timezone.utc))
            return state["jobs"]
        return self._cached("jobs", [STATE_PATH, excel], load)


def postings_db(cfg):
    path = BASE / cfg.get("postings_db", "postings.db")
    if not path.exists():
        return None
    db = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True, timeout=10, check_same_thread=False)
    db.row_factory = sqlite3.Row
    return db


def lock_held_elsewhere():
    """True when another process (a terminal or the scheduled task) is running."""
    probe = RunLock()
    if probe.acquire():
        probe.release()
        return False
    return True


# ----------------------------------------------------------------- live events
class EventHub:
    """Fans run events and log lines out to every open window (Server-Sent Events)."""

    def __init__(self):
        self.clients = set()
        self.lock = threading.Lock()

    def add(self, loop, queue):
        with self.lock:
            self.clients.add((loop, queue))

    def remove(self, loop, queue):
        with self.lock:
            self.clients.discard((loop, queue))

    def publish(self, kind, data):
        message = {"kind": kind, "at": dt.datetime.now(dt.timezone.utc).isoformat(), **data}
        with self.lock:
            clients = list(self.clients)
        for loop, queue in clients:
            try:
                loop.call_soon_threadsafe(_offer, queue, message)
            except RuntimeError:  # that window's loop has closed
                self.remove(loop, queue)


def _offer(queue, message):
    if queue.full():  # a window that stopped reading; drop its oldest message
        try:
            queue.get_nowait()
        except asyncio.QueueEmpty:
            pass
    queue.put_nowait(message)


class LogToEvents(logging.Handler):
    def __init__(self, hub):
        super().__init__(level=logging.INFO)
        self.hub = hub
        self.setFormatter(logging.Formatter("%(asctime)s  %(message)s", "%Y-%m-%d %H:%M"))

    def emit(self, record):
        try:
            self.hub.publish("log", {"line": self.format(record), "level": record.levelname})
        except Exception:
            pass


# ----------------------------------------------------------------- runs
class Runs:
    """Starts runs on a worker thread and keeps a live summary of the current one."""

    def __init__(self, hub):
        self.hub = hub
        self.lock = threading.Lock()
        self.thread = None
        self.current = None
        self.history = []  # runs started from this window since it opened
        on_event(self._on_event)

    def active(self):
        return bool(self.thread and self.thread.is_alive())

    def snapshot(self):
        with self.lock:
            run = dict(self.current) if self.current else None
            if run:
                run["new"] = list(run["new"])
                run["requests"] = dict(LIMITER.counts.most_common(6)) if run["status"] == "running" else run.get(
                    "requests", {})
        return run

    def start(self, mode, search=None):
        if mode not in RUN_MODES:
            raise HTTPException(400, f"Unknown run mode {mode!r}. Use one of: {', '.join(RUN_MODES)}.")
        with self.lock:
            if self.active():
                raise HTTPException(409, "A run is already going in this window.")
            if lock_held_elsewhere():
                raise HTTPException(409, "A run is going in a terminal or the scheduled task. "
                                         "Try again when it finishes.")
            self.current = {
                "id": uuid.uuid4().hex[:8], "mode": mode, "label": RUN_MODES[mode], "status": "running",
                "phase": "starting", "started": dt.datetime.now(dt.timezone.utc).isoformat(), "finished": None,
                "done": 0, "total": None, "last_company": "", "failed_boards": 0, "new": [],
                "rate_limited": [], "error": "", "cancelled": False}
            # Not a daemon: closing the window waits for a run to save before the app exits.
            self.thread = threading.Thread(target=self._work, args=(mode, search), name="jobwatcher-run")
            self.thread.start()
            return dict(self.current)

    def cancel(self):
        with self.lock:
            if not self.active():
                raise HTTPException(404, "No run is going in this window.")
            CANCEL.set()
            self.current["cancelled"] = True
            self.current["phase"] = "cancelling"
        self.hub.publish("run_cancelling", {})
        return self.snapshot()

    def wait(self, timeout=None):
        if self.thread:
            self.thread.join(timeout)

    def _work(self, mode, search):
        try:
            cfg = load_config()
        except ConfigError as exc:
            emit("run_failed", error=f"Settings problem: {exc}")
            return
        SETTINGS["career_sites"] = bool(cfg["career_sites"]["enabled"])
        try:
            run_once(cfg, offline=(mode == "refilter"), since_check=(mode == "since"),
                     search=False if mode == "refilter" else search)
        except Exception:
            log.exception("Run failed")

    def _on_event(self, kind, data):
        with self.lock:
            run = self.current
            if run and run["status"] == "running":
                if kind == "phase":
                    run["phase"] = data.get("name", "")
                elif kind == "boards_started":
                    run.update(phase="boards", total=data.get("total", 0), done=0)
                elif kind == "board_done":
                    run.update(done=data.get("done", 0), total=data.get("total"),
                               last_company=data.get("company", ""))
                    if not data.get("ok"):
                        run["failed_boards"] += 1
                elif kind == "rate_limited":
                    run["rate_limited"] = (run["rate_limited"] + [data.get("site", "")])[-20:]
                elif kind == "new_match":
                    run["new"].append(data.get("text", ""))
                elif kind in ("run_finished", "run_failed", "run_skipped"):
                    run.update(status={"run_finished": "cancelled" if run["cancelled"] else "finished",
                                       "run_failed": "failed", "run_skipped": "skipped"}[kind],
                               finished=dt.datetime.now(dt.timezone.utc).isoformat(),
                               error=data.get("error", "") or (
                                   "Another run was going, so this one did not start."
                                   if kind == "run_skipped" else ""),
                               requests=dict(LIMITER.counts.most_common(6)))
                    self.history.insert(0, {k: v for k, v in run.items() if k != "new"} | {"new": len(run["new"])})
                    del self.history[20:]
        self.hub.publish(kind, data)


# ----------------------------------------------------------------- the app
def create_app(token, dev=False):
    hub, files = EventHub(), Files()
    runs = Runs(hub)
    log_handler = LogToEvents(hub)
    log.addHandler(log_handler)
    if log.getEffectiveLevel() > logging.INFO:  # the window shows progress lines even if nothing else logs
        log.setLevel(logging.INFO)

    def detach():
        log.removeHandler(log_handler)
        off_event(runs._on_event)

    @asynccontextmanager
    async def lifespan(_app):
        yield
        detach()

    app = FastAPI(title="Job Watcher", docs_url="/api/docs" if dev else None, redoc_url=None,
                  openapi_url="/api/openapi.json" if dev else None, lifespan=lifespan)
    app.state.runs, app.state.hub, app.state.files, app.state.detach = runs, hub, files, detach

    @app.middleware("http")
    async def guard(request: Request, call_next):
        host = (request.headers.get("host") or "").rsplit(":", 1)[0].strip("[]")
        if host not in ALLOWED_HOSTS:
            return JSONResponse({"detail": "This server only answers the Job Watcher window."}, 403)
        if request.url.path.startswith("/api/"):
            given = request.headers.get("x-token") or request.query_params.get("token") or ""
            if not hmac.compare_digest(given.encode(), token.encode()):
                return JSONResponse({"detail": "Missing or wrong app token. Reopen the app."}, 401)
        response = await call_next(request)
        response.headers["Cache-Control"] = response.headers.get("Cache-Control", "no-store")
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    def config_or_fail():
        cfg, problem = files.config()
        if cfg is None:
            raise HTTPException(422, f"Settings problem in config.toml: {problem}")
        return cfg

    # ------------------------------------------------------------- reading
    @app.get("/api/meta")
    def meta():
        cfg, problem = files.config()
        regions = [{"name": r["name"], "color": r.get("color")} for r in (cfg or {}).get("regions", [])]
        return {"folder": str(BASE), "statuses": STATUSES, "outreach": OUTREACH, "regions": regions,
                "colors": {"status": STATUS_COLORS, "outreach": OUTREACH_COLORS, "level": LEVEL_COLORS,
                           "tier": TIER_COLORS},
                "run_modes": RUN_MODES, "settings_problem": problem}

    @app.get("/api/summary")
    def summary():
        cfg = config_or_fail()
        state = files.state()
        jobs = files.jobs(cfg)
        now = dt.datetime.now(dt.timezone.utc)
        month = now.astimezone().strftime("%Y-%m")
        results = Counter(c.get("result", "") for c in state.get("companies", {}).values())
        rows = files.company_rows(cfg)
        enabled = sum(1 for r in rows if is_enabled(r))
        saved = {"total": 0, "open": 0}
        db = postings_db(cfg)
        if db is not None:
            try:
                total, open_ = db.execute("SELECT COUNT(*), SUM(open) FROM postings").fetchone()
                saved = {"total": total or 0, "open": open_ or 0}
            except sqlite3.Error as exc:
                log.debug("postings count failed: %s", exc)
            finally:
                db.close()
        s, o = cfg["search"], cfg["outreach"]
        provider = s.get("provider", "serper")
        key_names = {"serper": "SERPER_API_KEY", "brave": "BRAVE_API_KEY", "serpapi": "SERPAPI_API_KEY"}
        return {
            "last_run": state.get("last_run"), "last_check": state.get("last_check"),
            "jobs": {"tracked": len(jobs), "open": sum(1 for j in jobs.values() if j.get("open")),
                     "new_24h": sum(1 for j in jobs.values()
                                    if (parse_date(j.get("found")) or now) > now - dt.timedelta(hours=24))},
            "companies": {"listed": len(rows), "enabled": enabled, "results": dict(results)},
            "postings": saved,
            "usage": {
                "month": month,
                "search": {"provider": provider, "used": state.get("search", {}).get("queries_by_month", {})
                           .get(month, 0), "limit": s.get("monthly_query_limit"),
                           "key_set": bool(os.environ.get(key_names.get(provider, ""), ""))},
                "hunter": {"used": state.get("hunter", {}).get("credits_by_month", {}).get(month, 0),
                           "limit": o.get("monthly_hunter_credit_limit"),
                           "key_set": bool(os.environ.get("HUNTER_API_KEY"))},
            },
            "schedule": {"runs_per_day": s.get("runs_per_day")},
            "run": runs.snapshot(),
            "busy_elsewhere": not runs.active() and lock_held_elsewhere(),
        }

    @app.get("/api/jobs")
    def jobs():
        cfg = config_or_fail()
        scores = {r["company"]: (r.get("score", ""), r.get("tier", "")) for r in files.company_rows(cfg)}
        out = []
        for j in files.jobs(cfg).values():
            score, tier = scores.get(j.get("company"), ("", ""))
            out.append({
                "key": j["key"], "company": j.get("company", ""), "title": j.get("title", ""),
                "url": j.get("url", ""), "locations": j.get("locations") or [], "region": j.get("region", ""),
                "level": j.get("level", ""), "stack": j.get("stack") or [], "provider": j.get("provider", ""),
                "found": j.get("found"), "posted": j.get("posted"), "open": bool(j.get("open")),
                "closed": j.get("closed"), "status": j.get("status") or "New", "notes": j.get("notes") or "",
                "outreach": j.get("outreach") or "", "score": score, "tier": tier})
        out.sort(key=lambda j: (j["open"], j["found"] or "", j["posted"] or ""), reverse=True)
        return out

    @app.get("/api/description")
    def description(key: str):
        cfg = config_or_fail()
        db = postings_db(cfg)
        if db is None:
            raise HTTPException(404, "No postings saved yet.")
        try:
            row = db.execute("SELECT text, url FROM postings WHERE key=?", (key,)).fetchone()
        finally:
            db.close()
        if not row:
            raise HTTPException(404, "That posting is not in your saved postings.")
        text = zlib.decompress(row["text"]).decode("utf-8") if row["text"] is not None else None
        return {"key": key, "text": text, "url": row["url"]}

    @app.get("/api/postings")
    def postings(q: str = "", where: str = "", status: str = "open", page: int = 1, size: int = 100):
        cfg = config_or_fail()
        size = max(10, min(size, 500))
        page = max(1, page)
        clauses, params = [], []
        if status == "open":
            clauses.append("open=1")
        elif status == "closed":
            clauses.append("open=0")
        for term in q.split():
            clauses.append("(title LIKE ? OR company LIKE ?)")
            params += [f"%{term}%"] * 2
        if where.strip():
            clauses.append("locations LIKE ?")
            params.append(f"%{where.strip()}%")
        sql_where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        db = postings_db(cfg)
        if db is None:
            return {"total": 0, "page": 1, "size": size, "rows": []}
        tracked = set(files.state().get("jobs", {}))
        try:
            total = db.execute(f"SELECT COUNT(*) FROM postings {sql_where}", params).fetchone()[0]
            rows = db.execute(
                f"SELECT key, company, provider, title, locations, url, posted, first_seen, open, closed"
                f" FROM postings {sql_where} ORDER BY COALESCE(posted, first_seen) DESC LIMIT ? OFFSET ?",
                params + [size, (page - 1) * size]).fetchall()
        finally:
            db.close()
        return {"total": total, "page": page, "size": size, "rows": [
            {"key": r["key"], "company": r["company"], "provider": r["provider"], "title": r["title"],
             "locations": json.loads(r["locations"] or "[]"), "url": r["url"], "posted": r["posted"],
             "first_seen": r["first_seen"], "open": bool(r["open"]), "closed": r["closed"],
             "tracked": r["key"] in tracked} for r in rows]}

    @app.get("/api/companies")
    def companies():
        cfg = config_or_fail()
        checked = files.state().get("companies", {})
        keep = ("priority", "region", "company", "score", "tier", "type", "ticker", "stage", "board", "slug",
                "careers_url", "domain", "ats", "hubs", "status", "verified", "market_cap_b", "stock_12m_pct",
                "revenue_growth_pct", "notes")
        out = []
        for r in files.company_rows(cfg):
            c = checked.get(r["company"], {})
            out.append({**{k: r.get(k, "") for k in keep}, "enabled": is_enabled(r),
                        "result": c.get("result", "" if is_enabled(r) else "OFF"), "why": c.get("error", ""),
                        "checked": c.get("checked"), "found_on": c.get("provider") or "",
                        "found_slug": c.get("slug") or "", "open": c.get("open"), "matches": c.get("matches", 0)})
        return out

    @app.get("/api/log")
    def log_tail(lines: int = 300):
        lines = max(10, min(lines, 2000))
        try:
            with open(LOG_PATH, "rb") as f:
                f.seek(0, os.SEEK_END)
                f.seek(max(0, f.tell() - lines * 220))
                text = f.read().decode("utf-8", "replace")
        except FileNotFoundError:
            return {"lines": []}
        return {"lines": text.splitlines()[-lines:]}

    @app.get("/api/settings/explain")
    def explain():
        return {"text": describe_config(config_or_fail())}

    # ------------------------------------------------------------- runs
    @app.post("/api/runs", status_code=202)
    def start_run(body: dict = Body(default={})):
        return runs.start(body.get("mode", "full"), body.get("search"))

    @app.get("/api/runs/current")
    def current_run():
        return runs.snapshot()

    @app.get("/api/runs")
    def run_history():
        return runs.history

    @app.delete("/api/runs/current")
    def cancel_run():
        return runs.cancel()

    @app.get("/api/events")
    async def events(request: Request):
        loop, queue = asyncio.get_running_loop(), asyncio.Queue(maxsize=2000)
        hub.add(loop, queue)

        async def stream():
            try:
                yield "retry: 2000\n\n"
                yield f"data: {json.dumps({'kind': 'hello', 'run': runs.snapshot()})}\n\n"
                while not await request.is_disconnected():
                    try:
                        message = await asyncio.wait_for(queue.get(), timeout=15)
                    except asyncio.TimeoutError:
                        yield ": still here\n\n"
                        continue
                    yield f"data: {json.dumps(message, default=str)}\n\n"
            finally:
                hub.remove(loop, queue)
        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.api_route("/api/{rest:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    def unknown(rest: str):
        raise HTTPException(404, f"No such endpoint: /api/{rest}")

    # ------------------------------------------------------------- the window's files
    @app.get("/{path:path}")
    def ui(path: str):
        if not UI_DIR.exists():
            return JSONResponse({"detail": "The app's interface files are missing (jobwatcher/ui_dist). "
                                           "Build them with: cd ui && npm install && npm run build"}, 500)
        target = (UI_DIR / path).resolve()
        if path and target.is_file() and UI_DIR in target.parents:
            cache = "public, max-age=31536000, immutable" if path.startswith("assets/") else "no-cache"
            return FileResponse(target, headers={"Cache-Control": cache})
        return FileResponse(UI_DIR / "index.html", headers={"Cache-Control": "no-cache"})

    return app


__all__ = ["create_app", "UI_DIR", "Runs", "EventHub", "Files"]
