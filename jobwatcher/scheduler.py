"""Automatic runs while the app is open or in the tray, at the times set under [schedule].

A run that was missed because the computer was asleep or off happens once when the app is
next running, if the missed time was within the last 12 hours. Nothing runs before your
first run, so a new setup finishes the welcome steps first.
"""
from __future__ import annotations

from .base import *  # noqa: F401,F403
from .config import ConfigError, load_config
from .tracked import connect, get_meta, set_meta

CATCH_UP = dt.timedelta(hours=12)


def slots(times, now):
    """Scheduled times around now (yesterday, today and tomorrow), as local datetimes."""
    local = now.astimezone()
    out = []
    for days in (-1, 0, 1):
        day = (local + dt.timedelta(days=days)).date()
        for t in times:
            h, m = (int(x) for x in t.split(":"))
            out.append(dt.datetime.combine(day, dt.time(h, m), tzinfo=local.tzinfo))
    return sorted(out)


def due_slot(times, last_run, now):
    """The scheduled time a run is owed for, or None."""
    if not times or last_run is None:
        return None
    past = [s for s in slots(times, now) if s <= now]
    if not past:
        return None
    latest = past[-1]
    if now - latest > CATCH_UP or last_run >= latest:
        return None
    return latest


def next_slot(times, now):
    upcoming = [s for s in slots(times, now) if s > now]
    return upcoming[0] if upcoming else None


class Scheduler:
    def __init__(self, runs, can_start):
        self.runs, self.can_start = runs, can_start
        self.stop = threading.Event()
        self.thread = None

    def paused(self, cfg):
        db = connect(cfg)
        try:
            return get_meta(db, "schedule_paused") == "1"
        finally:
            db.close()

    def set_paused(self, cfg, paused):
        db = connect(cfg)
        try:
            with db:
                set_meta(db, "schedule_paused", "1" if paused else "0")
        finally:
            db.close()

    def status(self, cfg, now=None):
        now = now or dt.datetime.now(dt.timezone.utc)
        s = cfg["schedule"]
        nxt = next_slot(s["times"], now) if s["enabled"] else None
        return {"enabled": bool(s["enabled"]), "times": s["times"], "paused": self.paused(cfg),
                "next": nxt.isoformat() if nxt else None, "running_in_app": bool(self.thread and self.thread.is_alive())}

    def tick(self, now=None):
        """Start a run if one is owed. Returns True when it started one."""
        try:
            cfg = load_config()
        except ConfigError:
            return False
        if not cfg["schedule"]["enabled"] or self.paused(cfg):
            return False
        now = now or dt.datetime.now(dt.timezone.utc)
        last = parse_date(load_json(STATE_PATH, {}).get("last_run"))
        if not due_slot(cfg["schedule"]["times"], last, now) or not self.can_start():
            return False
        log.info("Scheduled run starting (set under Settings, Automatic runs).")
        self.runs.start("full", source="schedule")
        return True

    def start(self, every=30.0):
        def loop():
            self.stop.wait(20)  # let the window open first
            while not self.stop.is_set():
                try:
                    self.tick()
                except Exception:
                    log.exception("Scheduled run check failed")
                self.stop.wait(every)
        self.thread = threading.Thread(target=loop, name="jobwatcher-schedule", daemon=True)
        self.thread.start()


__all__ = ["Scheduler", "due_slot", "next_slot", "slots"]
