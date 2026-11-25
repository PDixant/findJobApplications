"""The local API behind the desktop app (app.py).

It listens on 127.0.0.1 only. Every /api call must carry the token that app.py hands to its own
window, and requests addressed to any other host name are refused, so other programs and web
pages cannot drive it. Runs started here use the same run_once as the command line, under the
same lock, so the app, a terminal and the scheduled task never run at the same time.

Opening the app makes it the place you edit Status, Notes and Outreach (they live in
postings.db); job_tracker.xlsx is still rewritten after every run, as a copy. Company edits made
while a run holds companies.csv are queued and applied when the run saves.
"""
from __future__ import annotations

import asyncio
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
from .tracked import *  # noqa: F401,F403
from .discovery import add_company, import_companies
from .runner import run_once, refresh_scores
from .scheduler import Scheduler
from .filters import Rules
from . import ai, alerts, outreach_app, referrals, signals, system, updates
from . import __version__
from . import keys as keystore
from . import settings as settings_module

UI_DIR = Path(__file__).resolve().parent / "ui_dist"
ALLOWED_HOSTS = {"127.0.0.1", "localhost"}
RUN_MODES = {
    "full": "Check every company",
    "refilter": "Re-filter saved postings",
    "since": "Since your last check",
    "search": "Search the web, then check every company",
    "career_pages": "Find careers pages for companies with no board",
    "check": "Check one company",
    "scores": "Refresh company scores",
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
        """Tracked jobs, from postings.db (cheap to read every time)."""
        return load_jobs(cfg)

    def forget(self, key):
        with self._lock:
            self._cache.pop(key, None)


def postings_db(cfg):
    if not db_path(cfg).exists():
        return None
    return connect(cfg)


class CompaniesFile:
    """Changes to companies.csv happen only while holding the run lock. If a run has it, an edit
    is queued for the run to apply when it saves."""

    def __init__(self):
        self.lock = threading.Lock()

    def try_change(self, change):
        with self.lock:
            held = RunLock()
            if not held.acquire():
                return False
            try:
                change()
                return True
            finally:
                held.release()


EDITABLE_COMPANY = {"board", "slug", "careers_url", "domain", "ats", "enabled", "region", "priority", "notes", "hubs"}
BOARD_CHOICES = ["auto", "greenhouse", "ashby", "lever", "workday", "smartrecruiters", "workable", "website", "other"]


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
        self.notify = None  # set by the desktop app when it has a tray icon
        self.scoring = None
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

    def start(self, mode, search=None, company=None, source="app"):
        if mode not in RUN_MODES:
            raise HTTPException(400, f"Unknown run mode {mode!r}. Use one of: {', '.join(RUN_MODES)}.")
        if mode == "check" and not company:
            raise HTTPException(400, "Say which company to check.")
        with self.lock:
            if self.active():
                raise HTTPException(409, "A run is already going in this window.")
            if lock_held_elsewhere():
                raise HTTPException(409, "A run is going in a terminal or the scheduled task. "
                                         "Try again when it finishes.")
            self.current = {
                "id": uuid.uuid4().hex[:8], "mode": mode, "source": source,
                "label": f"Check {company}" if mode == "check" else RUN_MODES[mode], "status": "running",
                "phase": "starting", "started": dt.datetime.now(dt.timezone.utc).isoformat(), "finished": None,
                "done": 0, "total": None, "last_company": "", "failed_boards": 0, "new": [],
                "rate_limited": [], "error": "", "cancelled": False}
            # Not a daemon: closing the window waits for a run to save before the app exits.
            self.thread = threading.Thread(target=self._work, args=(mode, search, company, source),
                                           name="jobwatcher-run")
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

    def _work(self, mode, search, company, source):
        try:
            cfg = load_config()
        except ConfigError as exc:
            emit("run_failed", error=f"Settings problem: {exc}")
            return
        SETTINGS["career_sites"] = bool(cfg["career_sites"]["enabled"])
        if self.notify:
            cfg["desktop_notifications"] = False  # the tray says it instead
        try:
            if mode == "scores":
                refresh_scores(cfg, source=source)
                return
            found = run_once(
                cfg, offline=(mode == "refilter"), since_check=(mode == "since"),
                search=True if mode == "search" else False if mode in ("refilter", "check", "career_pages") else search,
                find_pages=(mode == "career_pages"), verify=(mode == "check"),
                only=[company] if mode == "check" else "missing" if mode == "career_pages" else None,
                label=f"Checked {company}" if mode == "check" else None, source=source)
            if found and self.notify:
                self.notify(f"{len(found)} new {'match' if len(found) == 1 else 'matches'}",
                            "\n".join(f"{m['company']}: {m['title']}" if isinstance(m, dict) else str(m) for m in found[:4]))
            if found and mode != "refilter":
                started = self.current["started"] if self.current else ""
                keys = [k for k, j in load_jobs(cfg).items() if j.get("open") and (j.get("found") or "") >= started]
                self.score(cfg, keys)
        except Busy:
            pass
        except Exception:
            log.exception("Run failed")

    def score(self, cfg, keys):
        """Claude fit scores for these matches on a side thread (a new run can start meanwhile)."""
        if not keys or not cfg["ai"]["score_new_matches"] or not os.environ.get("ANTHROPIC_API_KEY"):
            return
        with self.lock:
            if self.scoring and self.scoring.is_alive():
                return
            def work():
                ai.score_new(cfg, keys, on_each=lambda key: self.hub.publish("job_changed", {"key": key}))
            self.scoring = threading.Thread(target=work, name="jobwatcher-fit", daemon=True)
            self.scoring.start()

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
        if kind == "run_started":
            # The window only knows about runs it started itself; send the rest (the scheduler's)
            # so their progress shows too.
            data = {**data, "run": self.snapshot()}
        self.hub.publish(kind, data)


# ----------------------------------------------------------------- signals from descriptions
class Signals:
    """Pay, work authorization, workplace and relocation read from each saved description, kept
    until the description changes."""

    def __init__(self):
        self._cache = {}
        self._lock = threading.Lock()

    def for_jobs(self, cfg, jobs):
        db = postings_db(cfg)
        if db is None:
            return {}, {}
        out = {}
        try:
            keys = list(jobs)
            for i in range(0, len(keys), 500):
                chunk = keys[i:i + 500]
                for r in db.execute(f"SELECT key, text FROM postings WHERE key IN ({','.join('?' * len(chunk))})", chunk):
                    blob = r["text"]
                    stamp = zlib.crc32(blob) if blob is not None else None
                    with self._lock:
                        hit = self._cache.get(r["key"])
                    if hit and hit[0] == stamp:
                        out[r["key"]] = hit[1]
                        continue
                    text = zlib.decompress(blob).decode("utf-8", "replace") if blob is not None else ""
                    value = signals.read(jobs[r["key"]], text)
                    with self._lock:
                        self._cache[r["key"]] = (stamp, value)
                    out[r["key"]] = value
            history = signals.posting_history(db, list(jobs.values()))
        finally:
            db.close()
        return out, history


def follow_ups(cfg, jobs, now=None):
    """Applications and emails that are due a nudge."""
    now = now or dt.datetime.now(dt.timezone.utc)
    days = int(cfg["outreach"]["follow_up_after_days"])
    due = []
    for j in jobs.values():
        if not j.get("open"):
            continue
        applied = parse_date(j.get("applied"))
        if j.get("status") == "Applied" and applied and now - applied >= dt.timedelta(days=days):
            changed = parse_date(j.get("status_changed")) or applied
            if now - changed >= dt.timedelta(days=days):
                due.append({"key": j["key"], "company": j.get("company", ""), "title": j.get("title", ""),
                            "kind": "application", "days": (now - applied).days})
        sent = parse_date(j.get("outreach_sent"))
        if j.get("outreach") == "Sent" and sent and not j.get("outreach_followup") and now - sent >= dt.timedelta(days=days):
            due.append({"key": j["key"], "company": j.get("company", ""), "title": j.get("title", ""),
                        "kind": "email", "days": (now - sent).days})
    return sorted(due, key=lambda d: -d["days"])


def pipeline_stats(jobs, now=None):
    now = now or dt.datetime.now(dt.timezone.utc)
    by_status = Counter(j.get("status") or "New" for j in jobs.values())
    applied = [parse_date(j.get("applied")) for j in jobs.values() if j.get("applied")]
    applied = [a for a in applied if a]
    weeks = []
    start = (now - dt.timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    for back in range(7, -1, -1):
        lo = start - dt.timedelta(weeks=back)
        weeks.append({"week": lo.date().isoformat(), "applied": sum(1 for a in applied if lo <= a < lo + dt.timedelta(weeks=1))})
    heard = sum(by_status.get(s, 0) for s in ("Interviewing", "Offer", "Rejected"))
    sent = len(applied)
    fits = Counter((j.get("fit") or {}).get("verdict") for j in jobs.values() if j.get("open") and j.get("fit"))
    return {"by_status": {s: by_status.get(s, 0) for s in STATUSES}, "applied_total": sent,
            "applied_weeks": weeks, "heard_back": heard,
            "response_rate": round(heard / sent * 100) if sent else None,
            "fit": {"APPLY": fits.get("APPLY", 0), "STRETCH": fits.get("STRETCH", 0), "SKIP": fits.get("SKIP", 0)}}


def _inside(folder, name):
    target = (BASE / name).resolve()
    return target if target.is_file() and folder.resolve() in target.parents else None


# ----------------------------------------------------------------- the app
def create_app(token, dev=False, schedule=False):
    hub, files, companies_file = EventHub(), Files(), CompaniesFile()
    runs, job_signals = Runs(hub), Signals()
    imports = {}  # spreadsheet previews waiting for confirmation
    try:
        use_app_for_edits(load_config())  # from now on the app is where you edit
    except ConfigError:
        pass  # the window shows the settings problem
    log_handler = LogToEvents(hub)
    guard_logs(log_handler)
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
    scheduler = Scheduler(runs, lambda: not runs.active() and not lock_held_elsewhere())
    app.state.scheduler = scheduler
    if schedule:
        scheduler.start()

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
    @app.get("/api/ping")
    def ping():
        return {"app": "jobwatcher", "folder": str(BASE), "version": __version__}

    @app.post("/api/window/focus")
    def focus():
        """Another launch of the app asks this one to come to the front instead of opening twice."""
        bring = getattr(app.state, "focus", None)
        if bring:
            threading.Thread(target=bring, daemon=True).start()
        return {"focused": bool(bring)}

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
            "last_run": state.get("last_run"), "last_check": state.get("last_check"), "edits_in": edits_in(cfg),
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
        tracked = files.jobs(cfg)
        years_rules = Rules(cfg)
        found, history = job_signals.for_jobs(cfg, tracked)
        known = referrals.counts(cfg, {j.get("company", "") for j in tracked.values()})
        out = []
        for j in tracked.values():
            score, tier = scores.get(j.get("company"), ("", ""))
            sig = found.get(j["key"]) or {}
            fit = j.get("fit") or {}
            out.append({**{k: sig.get(k) for k in ("pay", "work_auth", "workplace", "relocation", "years")},
                "years_over": years_rules.too_many_years(sig.get("years")),
                **(history.get(j["key"]) or {}), "connections": known.get(j.get("company", ""), 0),
                "fit": {k: fit.get(k) for k in ("verdict", "score", "summary")} if fit else None,
                "tailored": bool(j.get("tailored")),
                "key": j["key"], "company": j.get("company", ""), "title": j.get("title", ""),
                "url": j.get("url", ""), "locations": j.get("locations") or [], "region": j.get("region", ""),
                "level": j.get("level", ""), "stack": j.get("stack") or [], "provider": j.get("provider", ""),
                "found": j.get("found"), "posted": j.get("posted"), "open": bool(j.get("open")),
                "closed": j.get("closed"), "status": j.get("status") or "New", "notes": j.get("notes") or "",
                "outreach": j.get("outreach") or "", "score": score, "tier": tier, "applied": j.get("applied")})
        out.sort(key=lambda j: (j["open"], j["found"] or "", j["posted"] or ""), reverse=True)
        return out

    @app.patch("/api/jobs")
    def edit(key: str, body: dict = Body(...)):
        cfg = config_or_fail()
        changes = {k: body[k] for k in ("status", "notes", "outreach") if k in body}
        if not changes:
            raise HTTPException(400, "Send status, notes or outreach.")
        try:
            job = edit_job(cfg, key, changes)
        except KeyError:
            raise HTTPException(404, "That job is not in your tracker.") from None
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        hub.publish("job_changed", {"key": key})
        return {k: job.get(k) for k in ("key", "status", "notes", "outreach", "outreach_sent")}

    def ai_problem(fn, *args):
        try:
            return fn(*args)
        except ai.AIProblem as exc:
            raise HTTPException(400, str(exc)) from None

    @app.get("/api/jobs/details")
    def job_details(key: str):
        cfg = config_or_fail()
        job = files.jobs(cfg).get(key)
        if not job:
            raise HTTPException(404, "That job is not in your tracker.")
        return {"key": key, "fit": job.get("fit"), "tailored": job.get("tailored"),
                "people": referrals.people_at(cfg, job.get("company", ""))[:25]}

    @app.post("/api/jobs/fit")
    def job_fit(key: str):
        result = ai_problem(ai.fit, config_or_fail(), key)
        hub.publish("job_changed", {"key": key})
        return result

    @app.post("/api/jobs/fit/unscored")
    def fit_unscored():
        cfg = config_or_fail()
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise HTTPException(400, "Add your Anthropic API key in Settings, Keys and accounts.")
        keys = [k for k, j in files.jobs(cfg).items() if j.get("open") and not j.get("fit")]
        left = max(0, int(cfg["ai"]["monthly_limit"]) - ai.used_this_month(cfg)) if int(cfg["ai"]["monthly_limit"]) else len(keys)
        keys = keys[:left]
        if keys:
            runs.score({**cfg, "ai": {**cfg["ai"], "score_new_matches": True}}, keys)
        return {"queued": len(keys)}

    @app.post("/api/jobs/followed-up")
    def followed_up(key: str):
        """You nudged them: the follow-up clock starts again."""
        cfg = config_or_fail()
        if key not in files.jobs(cfg):
            raise HTTPException(404, "That job is not in your tracker.")
        update_job(cfg, key, {"status_changed": dt.datetime.now(dt.timezone.utc).isoformat()})
        hub.publish("job_changed", {"key": key})
        return {"ok": True}

    @app.post("/api/jobs/tailor")
    def job_tailor(key: str):
        result = ai_problem(ai.tailor, config_or_fail(), key)
        hub.publish("job_changed", {"key": key})
        return result

    @app.post("/api/files/open")
    def open_file(body: dict = Body(...)):
        """Open a tailored resume (only files inside the tailored resumes folder)."""
        cfg = config_or_fail()
        folder = BASE / (cfg["outreach"]["tailored_resumes_folder"] or "resumes")
        target = _inside(folder, str(body.get("path") or ""))
        if not target:
            raise HTTPException(404, "That file is no longer there.")
        from .desktop import open_folder
        open_folder(target if not body.get("folder") else target.parent)
        return {"opened": target.name}

    @app.get("/api/ai")
    def ai_status():
        cfg = config_or_fail()
        a = cfg["ai"]
        tex = Path(a["resume_tex"])
        tex = tex if tex.is_absolute() else BASE / tex
        name, _exe = ai.compiler()
        jobs_ = files.jobs(cfg)
        return {"key_set": bool(os.environ.get("ANTHROPIC_API_KEY")), "used": ai.used_this_month(cfg),
                "limit": a["monthly_limit"], "model": a["model"], "score_new_matches": a["score_new_matches"],
                "resume_tex": tex.name, "resume_tex_found": tex.exists(), "latex": name,
                "rules_found": (BASE / a["resume_rules"]).exists(),
                "unscored": sum(1 for j in jobs_.values() if j.get("open") and not j.get("fit")),
                "scoring": bool(runs.scoring and runs.scoring.is_alive())}

    @app.get("/api/connections")
    def connections():
        cfg = config_or_fail()
        rows = files.company_rows(cfg)
        return {"total": referrals.total(cfg),
                "companies": referrals.counts(cfg, [r["company"] for r in rows])}

    @app.post("/api/connections/import")
    async def connections_import(request: Request):
        cfg = config_or_fail()
        data = await request.body()
        if not data or len(data) > 30_000_000:
            raise HTTPException(400, "The file is empty or larger than 30 MB.")
        try:
            saved = referrals.import_csv(cfg, data)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        hub.publish("job_changed", {})
        return {"saved": saved}

    @app.delete("/api/connections")
    def connections_clear():
        referrals.clear(config_or_fail())
        hub.publish("job_changed", {})
        return {"saved": 0}

    @app.post("/api/alerts/test")
    def alerts_test():
        cfg = config_or_fail()
        if not os.environ.get("NTFY_TOPIC", "").strip():
            raise HTTPException(400, "Save your ntfy topic first (Settings, Keys and accounts).")
        if not alerts.send(cfg, "Job Watcher test", "Phone alerts work. New matches will show up here."):
            raise HTTPException(400, "ntfy didn't accept the alert. Check the topic and server.")
        return {"sent": True}

    @app.get("/api/alerts/topic")
    def alerts_topic():
        return {"topic": alerts.suggest_topic()}

    @app.get("/api/pipeline")
    def pipeline():
        cfg = config_or_fail()
        jobs_ = files.jobs(cfg)
        return {**pipeline_stats(jobs_), "follow_ups": follow_ups(cfg, jobs_)[:30],
                "follow_up_after_days": cfg["outreach"]["follow_up_after_days"]}

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
        tracked = set(files.jobs(cfg))
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
        pending = {}
        for _, name, fields in pending_company_edits(cfg):
            pending.setdefault(name, {}).update(fields)
        keep = ("priority", "region", "company", "score", "tier", "type", "ticker", "stage", "board", "slug",
                "careers_url", "domain", "ats", "hubs", "status", "verified", "market_cap_b", "stock_12m_pct",
                "revenue_growth_pct", "notes")
        out = []
        for r in files.company_rows(cfg):
            if r["company"] in pending:
                r = dict(r)
                apply_company_edit(r, pending[r["company"]])
            c = checked.get(r["company"], {})
            out.append({**{k: r.get(k, "") for k in keep}, "enabled": is_enabled(r),
                        "result": c.get("result", "" if is_enabled(r) else "OFF"), "why": c.get("error", ""),
                        "checked": c.get("checked"), "found_on": c.get("provider") or "",
                        "found_slug": c.get("slug") or "", "open": c.get("open"), "matches": c.get("matches", 0),
                        "pending": r["company"] in pending, "from_config": r.get("region") == "From config file"})
        return out

    @app.patch("/api/companies")
    def edit_company(name: str, body: dict = Body(...)):
        cfg = config_or_fail()
        fields = {k: v for k, v in body.items() if k in EDITABLE_COMPANY}
        if not fields:
            raise HTTPException(400, f"Send any of: {', '.join(sorted(EDITABLE_COMPANY))}.")
        if "board" in fields and str(fields["board"]).lower() not in BOARD_CHOICES:
            raise HTTPException(400, f"Board must be one of {', '.join(BOARD_CHOICES)}.")
        if "board" in fields:
            fields["board"] = str(fields["board"]).lower()
        if "priority" in fields and not str(fields["priority"]).strip().isdigit():
            raise HTTPException(400, "Priority must be a whole number (1 is checked first).")
        if "careers_url" in fields and fields["careers_url"] and not str(fields["careers_url"]).startswith("http"):
            fields["careers_url"] = "https://" + str(fields["careers_url"]).strip()
        rows = files.company_rows(cfg)
        if not any(r["company"] == name and r.get("region") != "From config file" for r in rows):
            raise HTTPException(404, "That company is not in companies.csv.")

        def change():
            fresh = load_company_rows(cfg)
            row = next(r for r in fresh if r["company"] == name)
            apply_company_edit(row, fields)
            save_company_rows(cfg, fresh)
        if companies_file.try_change(change):
            files.forget("companies")
            return {"saved": True, "queued": False}
        queue_company_edit(cfg, name, fields)
        return {"saved": True, "queued": True,
                "detail": "A run is using companies.csv, so this change is applied when the run saves."}

    @app.post("/api/companies/add")
    def add(body: dict = Body(...)):
        cfg = config_or_fail()
        link = str(body.get("link") or "").strip()
        if not link:
            raise HTTPException(400, "Paste a job posting, job board or careers page link.")
        out = {}

        def change():
            SETTINGS["career_sites"] = bool(cfg["career_sites"]["enabled"])
            out["message"] = add_company(cfg, link, (body.get("name") or "").strip() or None)
        if not companies_file.try_change(change):
            raise HTTPException(409, "A run is going. Add the company when it finishes.")
        files.forget("companies")
        return out

    @app.post("/api/companies/import")
    async def import_preview(request: Request, filename: str):
        cfg = config_or_fail()
        suffix = Path(filename).suffix.lower()
        if suffix not in (".xlsx", ".csv"):
            raise HTTPException(400, "Choose an .xlsx or .csv file.")
        data = await request.body()
        if not data or len(data) > 20_000_000:
            raise HTTPException(400, "The file is empty or larger than 20 MB.")
        folder = BASE / ".app_window" / "imports"
        folder.mkdir(parents=True, exist_ok=True)
        upload_id = uuid.uuid4().hex[:12]
        path = folder / f"{upload_id}{suffix}"
        path.write_bytes(data)
        try:
            added, updated, seen = import_companies(cfg, [path], save=False)
        except Exception as exc:
            path.unlink(missing_ok=True)
            raise HTTPException(400, f"Could not read that file: {exc}") from None
        imports[upload_id] = path
        show = lambda r: {k: r.get(k, "") for k in ("company", "region", "board", "slug", "careers_url", "notes")}  # noqa: E731
        return {"id": upload_id, "filename": filename, "rows": seen,
                "added": [show(r) for r in added], "updated": [show(r) for r in updated]}

    @app.post("/api/companies/import/{upload_id}")
    def import_confirm(upload_id: str):
        cfg = config_or_fail()
        path = imports.get(upload_id)
        if not path or not path.exists():
            raise HTTPException(404, "That preview has expired. Choose the file again.")
        out = {}

        def change():
            added, updated, _ = import_companies(cfg, [path])
            out.update(added=len(added), updated=len(updated))
        if not companies_file.try_change(change):
            raise HTTPException(409, "A run is going. Import when it finishes.")
        path.unlink(missing_ok=True)
        imports.pop(upload_id, None)
        files.forget("companies")
        return out

    # ------------------------------------------------------------- settings and keys
    @app.get("/api/settings")
    def get_settings():
        cfg = config_or_fail()
        return {**settings_module.current(cfg), "explain": describe_config(cfg)}

    @app.post("/api/settings/preview")
    def preview_settings(body: dict = Body(...)):
        text = CONFIG_PATH.read_text(encoding="utf-8-sig") if CONFIG_PATH.exists() else ""
        try:
            _, cfg = settings_module.apply_changes(text, body.get("changes") or {})
        except ConfigError as exc:
            raise HTTPException(400, str(exc)) from None
        return {"explain": describe_config(cfg), "searches": search_queries(cfg),
                "pages_per_query": cfg["search"]["pages_per_query"]}

    @app.get("/api/search/queries")
    def search_list():
        cfg = config_or_fail()
        return {"searches": search_queries(cfg), "pages_per_query": cfg["search"]["pages_per_query"]}

    @app.put("/api/settings")
    def put_settings(body: dict = Body(...)):
        try:
            cfg = settings_module.save(body.get("changes") or {})
        except ConfigError as exc:
            raise HTTPException(400, str(exc)) from None
        files.forget("config")
        if not cfg["schedule"].get("keep_in_tray") and system.startup_supported():
            try:
                if system.startup_enabled():
                    system.set_startup(False)  # it only opens at sign-in to sit in the tray
            except (RuntimeError, OSError):
                pass
        hub.publish("settings_changed", {})
        return {**settings_module.current(cfg), "explain": describe_config(cfg)}

    @app.get("/api/keys")
    def get_keys():
        cfg, _ = files.config()
        provider = ((cfg or {}).get("search") or {}).get("provider", "serper")
        return {"store": keystore.store_available(), "search_provider": provider, "keys": keystore.status()}

    @app.put("/api/keys/{name}")
    def put_key(name: str, body: dict = Body(...)):
        try:
            where = keystore.save_key(name, str(body.get("value") or ""))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        return {"saved": where, "keys": keystore.status()}

    @app.delete("/api/keys/{name}")
    def delete_key(name: str):
        try:
            keystore.delete_key(name)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        return {"keys": keystore.status()}

    @app.post("/api/keys/{name}/test")
    def test_key(name: str):
        if name not in keystore.KEYS:
            raise HTTPException(400, f"Unknown key {name}")
        cfg, _ = files.config()
        ok, message = keystore.test_key(name, cfg)
        return {"ok": ok, "message": message}

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
        return runs.start(body.get("mode", "full"), body.get("search"), body.get("company"))

    @app.get("/api/runs/current")
    def current_run():
        return runs.snapshot()

    @app.get("/api/runs")
    def past_runs(limit: int = 50):
        return run_history(config_or_fail(), max(1, min(limit, 200)))

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

    # ------------------------------------------------------------- outreach
    def problem(fn, *args):
        try:
            return fn(*args)
        except outreach_app.OutreachProblem as exc:
            raise HTTPException(400, str(exc)) from None
        except KeyError:
            raise HTTPException(404, "That draft no longer exists.") from None

    @app.get("/api/outreach")
    def outreach_overview():
        cfg = config_or_fail()
        month = dt.datetime.now().strftime("%Y-%m")
        used = files.state().get("hunter", {}).get("credits_by_month", {}).get(month, 0)
        return {"companies": problem(outreach_app.overview, cfg),
                "hunter": {"used": used, "limit": cfg["outreach"]["monthly_hunter_credit_limit"],
                           "key_set": bool(os.environ.get("HUNTER_API_KEY"))},
                "writer": {"key_set": bool(os.environ.get("ANTHROPIC_API_KEY")), "model": cfg["outreach"]["model"]},
                "gmail": bool(os.environ.get("GMAIL_ADDRESS") and os.environ.get("GMAIL_APP_PASSWORD")),
                "follow_up_after_days": cfg["outreach"]["follow_up_after_days"],
                "packets": outreach_app.packets(cfg)}

    @app.post("/api/outreach/contacts")
    def outreach_contacts(body: dict = Body(...)):
        problem(outreach_app.find_contacts, config_or_fail(), str(body.get("company") or ""))
        files.forget("state")
        return {"ok": True}

    @app.post("/api/outreach/write")
    def outreach_write(body: dict = Body(...)):
        cfg = config_or_fail()
        return problem(outreach_app.write_drafts, cfg, str(body.get("company") or ""),
                       [str(e) for e in body.get("emails") or []], bool(body.get("follow_up")))

    @app.patch("/api/outreach/drafts/{draft_id}")
    def outreach_edit(draft_id: int, body: dict = Body(...)):
        return problem(outreach_app.edit_draft, config_or_fail(), draft_id, body)

    @app.delete("/api/outreach/drafts/{draft_id}")
    def outreach_delete(draft_id: int):
        problem(outreach_app.delete_draft, config_or_fail(), draft_id)
        return {"deleted": draft_id}

    @app.post("/api/outreach/drafts/{draft_id}/open")
    def outreach_open(draft_id: int):
        path = problem(outreach_app.open_in_mail_app, config_or_fail(), draft_id)
        return {"path": str(path)}

    @app.post("/api/outreach/drafts/{draft_id}/gmail")
    def outreach_gmail(draft_id: int):
        return {"mailbox": problem(outreach_app.gmail_draft, config_or_fail(), draft_id)}

    @app.post("/api/outreach/drafts/{draft_id}/sent")
    def outreach_sent(draft_id: int):
        return problem(outreach_app.mark_sent, config_or_fail(), draft_id)

    @app.post("/api/outreach/replied")
    def outreach_replied(body: dict = Body(...)):
        problem(outreach_app.mark_replied, config_or_fail(), str(body.get("company") or ""))
        return {"ok": True}

    @app.post("/api/outreach/packet")
    def outreach_packet():
        """Without an Anthropic key: the packet for your Claude agent, like `python outreach.py`."""
        import outreach
        cfg = config_or_fail()
        held = RunLock()
        if not held.acquire():
            raise HTTPException(409, "A run is going. Make the packet when it finishes.")
        try:
            folder = outreach.prepare(cfg)
        except outreach.OutreachError as exc:
            raise HTTPException(400, str(exc)) from None
        finally:
            held.release()
        if not folder:
            raise HTTPException(400, "Nothing to prepare: set Outreach to Email on the jobs you want first.")
        return {"folder": Path(folder).relative_to(BASE).as_posix()}

    @app.post("/api/outreach/import")
    def outreach_import(body: dict = Body(...)):
        return {"imported": problem(outreach_app.import_packet, config_or_fail(), str(body.get("folder") or ""))}

    # ------------------------------------------------------------- schedule, startup, setup, updates
    @app.get("/api/schedule")
    def schedule_status():
        cfg = config_or_fail()
        return {**scheduler.status(cfg), "startup_supported": system.startup_supported(),
                "startup": system.startup_enabled(), "tray": bool(getattr(app.state, "tray", False)),
                "keep_in_tray": bool(cfg["schedule"].get("keep_in_tray"))}

    @app.post("/api/schedule/pause")
    def schedule_pause(body: dict = Body(...)):
        cfg = config_or_fail()
        scheduler.set_paused(cfg, bool(body.get("paused")))
        hub.publish("schedule_changed", {})
        return scheduler.status(cfg)

    @app.post("/api/startup")
    def startup(body: dict = Body(...)):
        try:
            return {"startup": system.set_startup(bool(body.get("on")))}
        except (RuntimeError, OSError) as exc:
            raise HTTPException(400, str(exc)) from None

    @app.get("/api/system/old-tasks")
    def old_tasks():
        try:
            return {"tasks": system.old_tasks()}
        except Exception as exc:  # PowerShell missing or blocked
            return {"tasks": [], "error": str(exc)}

    @app.post("/api/system/old-tasks/disable")
    def disable_old_task(body: dict = Body(...)):
        try:
            system.disable_task(str(body.get("name") or ""), str(body.get("path") or "\\"))
        except (RuntimeError, OSError) as exc:
            raise HTTPException(400, f"Could not turn it off: {exc}") from None
        return {"tasks": system.old_tasks()}

    @app.get("/api/setup")
    def setup_status():
        cfg = config_or_fail()
        db = connect(cfg)
        try:
            done = get_meta(db, "setup_done")
        finally:
            db.close()
        never_run = not files.state().get("last_run")
        return {"needed": not done and never_run, "companies": len(files.company_rows(cfg))}

    @app.post("/api/setup/done")
    def setup_done():
        cfg = config_or_fail()
        db = connect(cfg)
        try:
            with db:
                set_meta(db, "setup_done", dt.datetime.now(dt.timezone.utc).isoformat())
        finally:
            db.close()
        return {"needed": False}

    @app.get("/api/update")
    def update(force: bool = False):
        return updates.check(__version__, force=force)

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
            fonts = path.startswith("assets/") and path.endswith((".woff", ".woff2"))
            cache = "public, max-age=31536000, immutable" if fonts else "no-cache"
            return FileResponse(target, headers={"Cache-Control": cache})
        return FileResponse(UI_DIR / "index.html", headers={"Cache-Control": "no-cache"})

    return app


__all__ = ["create_app", "UI_DIR", "Runs", "EventHub", "Files"]
