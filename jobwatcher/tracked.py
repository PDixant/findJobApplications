"""Your tracked jobs (the matches in your tracker) and queued company edits, kept in postings.db.

Runs, outreach.py and the desktop app all read and write jobs through load_state and
save_state. The database lets the app save an edit while a run is going: when the run saves,
any Status, Notes or Outreach you changed after it started is kept, not overwritten.

Where you edit: until the desktop app is opened, Status, Notes and Outreach typed into
job_tracker.xlsx are read on every run, as before. Once the app has been opened, the app is
where you edit and job_tracker.xlsx becomes a copy that is rewritten after every run.
"""
from __future__ import annotations

import shutil

from .base import *  # noqa: F401,F403
from .config import *  # noqa: F401,F403
from .tracker import read_excel_edits, apply_excel_edits

USER_FIELDS = ("status", "notes", "outreach", "outreach_sent", "applied", "outreach_prepared", "outreach_followup")
BACKUP_NAME = "job_watcher_state.before-database.json"


def db_path(cfg):
    return BASE / cfg.get("postings_db", "postings.db")


def connect(cfg):
    """A connection to postings.db in WAL mode, so the app and a run can write at the same time."""
    db = sqlite3.connect(db_path(cfg), timeout=30, check_same_thread=False)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA busy_timeout=30000")
    db.executescript("""
        CREATE TABLE IF NOT EXISTS jobs (key TEXT PRIMARY KEY, data TEXT NOT NULL, edited TEXT);
        CREATE TABLE IF NOT EXISTS meta (name TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE IF NOT EXISTS company_edits (
            id INTEGER PRIMARY KEY AUTOINCREMENT, company TEXT NOT NULL, fields TEXT NOT NULL, at TEXT);
        CREATE TABLE IF NOT EXISTS runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT, started TEXT, finished TEXT, label TEXT, source TEXT,
            status TEXT, summary TEXT);
        CREATE TABLE IF NOT EXISTS drafts (
            id INTEGER PRIMARY KEY AUTOINCREMENT, company TEXT NOT NULL, job_keys TEXT, to_email TEXT,
            to_name TEXT, position TEXT, kind TEXT, subject TEXT, body TEXT, linkedin_note TEXT,
            attach TEXT, follow_up INTEGER DEFAULT 0, status TEXT DEFAULT 'draft', created TEXT,
            updated TEXT, sent TEXT);""")
    return db


def get_meta(db, name, default=None):
    row = db.execute("SELECT value FROM meta WHERE name=?", (name,)).fetchone()
    return row[0] if row else default


def set_meta(db, name, value):
    db.execute("INSERT INTO meta (name, value) VALUES (?, ?) ON CONFLICT(name) DO UPDATE SET value=excluded.value",
               (name, value))


def edits_in(cfg):
    """'app' once the desktop app has been opened, otherwise 'excel'."""
    db = connect(cfg)
    try:
        return get_meta(db, "edits_in", "excel")
    finally:
        db.close()


def use_app_for_edits(cfg):
    """Called when the desktop app opens. Brings in your latest Excel edits one last time."""
    db = connect(cfg)
    try:
        _migrate(db, cfg)
        if get_meta(db, "edits_in", "excel") == "app":
            return False
        excel = BASE / cfg.get("excel_file", "job_tracker.xlsx")
        edits = read_excel_edits(excel)
        now = dt.datetime.now(dt.timezone.utc)
        with db:
            for key, edit in edits.items():
                row = db.execute("SELECT data FROM jobs WHERE key=?", (key,)).fetchone()
                if not row:
                    continue
                state = {"jobs": {key: json.loads(row["data"])}}
                apply_excel_edits(state, {key: dict(edit)}, now)
                db.execute("UPDATE jobs SET data=? WHERE key=?", (json.dumps(state["jobs"][key], default=str), key))
            set_meta(db, "edits_in", "app")
        log.info("The desktop app is now where you edit Status, Notes and Outreach. job_tracker.xlsx "
                 "is still rewritten after every run, as a copy.")
        return True
    finally:
        db.close()


def _migrate(db, cfg):
    """One time: move tracked jobs out of job_watcher_state.json into the database."""
    if get_meta(db, "jobs_migrated"):
        return
    with db:  # one transaction, so two processes starting at once cannot both migrate
        db.execute("BEGIN IMMEDIATE")
        if get_meta(db, "jobs_migrated"):
            return
        state = load_json(STATE_PATH, {})
        jobs = state.get("jobs") or {}
        if jobs:
            if not (BASE / BACKUP_NAME).exists():
                shutil.copy2(STATE_PATH, BASE / BACKUP_NAME)
            excel = BASE / cfg.get("excel_file", "job_tracker.xlsx")
            apply_excel_edits(state, read_excel_edits(excel), dt.datetime.now(dt.timezone.utc))
            db.executemany("INSERT OR REPLACE INTO jobs (key, data) VALUES (?, ?)",
                           [(k, json.dumps(j, default=str)) for k, j in state["jobs"].items()])
            log.info("Moved %d tracked jobs into %s (a copy of the old state is in %s).",
                     len(jobs), db_path(cfg).name, BACKUP_NAME)
        set_meta(db, "jobs_migrated", dt.datetime.now(dt.timezone.utc).isoformat())


def load_jobs(cfg):
    db = connect(cfg)
    try:
        _migrate(db, cfg)
        return {r["key"]: json.loads(r["data"]) for r in db.execute("SELECT key, data FROM jobs")}
    finally:
        db.close()


def load_state(cfg, excel_edits=True):
    """The saved state, with tracked jobs from the database. Applies Excel edits unless the app
    is where you edit (excel_edits=False skips them either way)."""
    state = load_json(STATE_PATH, {})
    for k in ("resolved", "companies"):
        state.setdefault(k, {})
    state["jobs"] = load_jobs(cfg)
    if excel_edits and edits_in(cfg) == "excel":
        excel = BASE / cfg.get("excel_file", "job_tracker.xlsx")
        apply_excel_edits(state, read_excel_edits(excel), dt.datetime.now(dt.timezone.utc))
    return state


def save_state(cfg, state, since):
    """Save the state. since = when this process loaded it: Status, Notes and Outreach edited in
    the app after that are kept (and copied into state, so the tracker shows them)."""
    since = since.isoformat() if hasattr(since, "isoformat") else since
    jobs = state.get("jobs") or {}
    db = connect(cfg)
    try:
        with db:
            db.execute("BEGIN IMMEDIATE")
            newer = {r["key"]: json.loads(r["data"]) for r in db.execute(
                "SELECT key, data FROM jobs WHERE edited IS NOT NULL AND edited > ?", (since,))}
            for key, mine in newer.items():
                if key in jobs:
                    jobs[key].update({f: mine.get(f) for f in USER_FIELDS if f in mine})
            db.executemany(
                "INSERT INTO jobs (key, data) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET data=excluded.data",
                [(k, json.dumps(j, default=str)) for k, j in jobs.items()])
            gone = [k for (k,) in db.execute("SELECT key FROM jobs") if k not in jobs and k not in newer]
            db.executemany("DELETE FROM jobs WHERE key=?", [(k,) for k in gone])
    finally:
        db.close()
    save_json(STATE_PATH, {k: v for k, v in state.items() if k != "jobs"})


def edit_job(cfg, key, changes):
    """The app's edit of one job: status, notes and outreach. Returns the job."""
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    db = connect(cfg)
    try:
        with db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT data FROM jobs WHERE key=?", (key,)).fetchone()
            if not row:
                raise KeyError(key)
            job = json.loads(row["data"])
            if "status" in changes:
                if changes["status"] not in STATUSES:
                    raise ValueError(f"Status must be one of {', '.join(STATUSES)}")
                if changes["status"] == "Applied" and not job.get("applied"):
                    job["applied"] = now  # when you applied, for follow-ups and stats
                job["status"] = changes["status"]
            if "notes" in changes:
                job["notes"] = str(changes["notes"] or "")[:5000]
            if "outreach" in changes:
                value = changes["outreach"] or ""
                if value and value not in OUTREACH:
                    raise ValueError(f"Outreach must be empty or one of {', '.join(OUTREACH)}")
                if value == "Sent" and job.get("outreach") != "Sent" and not job.get("outreach_sent"):
                    job["outreach_sent"] = now  # starts the follow-up clock
                job["outreach"] = value
            db.execute("UPDATE jobs SET data=?, edited=? WHERE key=?", (json.dumps(job, default=str), now, key))
        return job
    finally:
        db.close()


# ----------------------------------------------------------------- run history
def start_run_record(cfg, label, source):
    db = connect(cfg)
    try:
        with db:
            cur = db.execute("INSERT INTO runs (started, label, source, status) VALUES (?, ?, ?, 'running')",
                             (dt.datetime.now(dt.timezone.utc).isoformat(), label, source))
            return cur.lastrowid
    finally:
        db.close()


def finish_run_record(cfg, run_id, status, summary):
    db = connect(cfg)
    try:
        with db:
            db.execute("UPDATE runs SET finished=?, status=?, summary=? WHERE id=?",
                       (dt.datetime.now(dt.timezone.utc).isoformat(), status, json.dumps(summary, default=str), run_id))
            db.execute("DELETE FROM runs WHERE id <= (SELECT MAX(id) - 200 FROM runs)")  # keep the last 200
    finally:
        db.close()


def run_history(cfg, limit=50):
    db = connect(cfg)
    try:
        return [{**dict(r), "summary": json.loads(r["summary"] or "{}")}
                for r in db.execute("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,))]
    finally:
        db.close()


def update_job(cfg, key, fields):
    """Set fields on one tracked job (used by the Outreach screen). Counts as an app edit, so a run
    that is going keeps it."""
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    db = connect(cfg)
    try:
        with db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT data FROM jobs WHERE key=?", (key,)).fetchone()
            if not row:
                raise KeyError(key)
            job = {**json.loads(row["data"]), **fields}
            db.execute("UPDATE jobs SET data=?, edited=? WHERE key=?", (json.dumps(job, default=str), now, key))
        return job
    finally:
        db.close()


# ----------------------------------------------------------------- queued company edits
def queue_company_edit(cfg, company, fields):
    db = connect(cfg)
    try:
        with db:
            db.execute("INSERT INTO company_edits (company, fields, at) VALUES (?, ?, ?)",
                       (company, json.dumps(fields), dt.datetime.now(dt.timezone.utc).isoformat()))
    finally:
        db.close()


def pending_company_edits(cfg):
    db = connect(cfg)
    try:
        return [(r["id"], r["company"], json.loads(r["fields"]))
                for r in db.execute("SELECT id, company, fields FROM company_edits ORDER BY id")]
    finally:
        db.close()


def apply_company_edit(row, fields):
    """Change one companies.csv row. A new board, slug or careers page is checked fresh."""
    before = (row.get("board"), row.get("slug"), row.get("careers_url"))
    for k, v in fields.items():
        if k == "enabled":
            row[k] = "yes" if v in (True, "yes", "true", 1) else "no"
        else:
            row[k] = str(v if v is not None else "").strip()
    if (row.get("board"), row.get("slug"), row.get("careers_url")) != before:
        row.update(status="", verified="")


def take_pending_company_edits(cfg, rows):
    """Apply edits the app queued while a run held companies.csv. Call with the run lock held,
    before saving the rows. Returns True when something changed."""
    edits = pending_company_edits(cfg)
    if not edits:
        return False
    by_name = {r["company"]: r for r in rows}
    for _, company, fields in edits:
        if company in by_name:
            apply_company_edit(by_name[company], fields)
    db = connect(cfg)
    try:
        with db:
            db.execute("DELETE FROM company_edits WHERE id <= ?", (edits[-1][0],))
    finally:
        db.close()
    log.info("Applied %d company edit%s made in the app during the run.", len(edits), "" if len(edits) == 1 else "s")
    return True


__all__ = [n for n in list(globals()) if not n.startswith("__")]
