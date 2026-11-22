"""One run: read every board and careers page, filter, update the tracker."""
from __future__ import annotations

from .base import *  # noqa: F401,F403
from .config import *  # noqa: F401,F403
from .boards import *  # noqa: F401,F403
from .website import *  # noqa: F401,F403
from .companies import *  # noqa: F401,F403
from .store import *  # noqa: F401,F403
from .filters import *  # noqa: F401,F403
from .discovery import *  # noqa: F401,F403
from .tracker import *  # noqa: F401,F403
from .tracked import *  # noqa: F401,F403


def fetch_board(row, state, rules, has_text, now, max_age, window_start, known_boards, detail_cap):
    """Runs in a worker thread. Downloads one board's full list (normally a single request)
    and returns every posting; filtering happens later, locally, against the saved list.

    Workday and SmartRecruiters lists carry no descriptions, so a description is fetched
    only for postings that could match (right title, fresh enough, in one of your regions)
    and only once: it is saved and never requested again."""
    entry = entry_for(row)
    careers = row.get("careers_url") if SETTINGS["career_sites"] else ""
    hint = (row.get("ats") or "").strip().lower()
    only = ATS_HINTS.get(hint.split()[0]) if hint else None  # unknown hint: guess every board
    try:
        loaded = site = None
        if (careers and not entry.startswith("website:") and entry not in state["resolved"]
                and (row.get("board") or "auto").lower() in ("auto", "")):
            # A company with no known board: its careers page usually names the board, which
            # is far cheaper than guessing board names. A careers page with readable jobs
            # needs no guessing at all.
            site = load_company(f"website:{careers}", state)
            if site and (site[0] != "website" or site[1].get("jobs")):
                loaded = site
        guessing = entry not in state["resolved"] and (
            ":" not in entry or (entry.startswith("workday:") and not parse_workday(entry[8:])[2]))
        guesses = state.setdefault("board_guesses", {})
        last_guess = parse_date(guesses.get(row["company"]))
        may_guess = (not guessing or SETTINGS["guess_every"] is None or last_guess is None
                     or now - last_guess >= SETTINGS["guess_every"])
        if loaded is None and may_guess:
            loaded = load_company(entry, state, only)
            if guessing:
                # Spread the daily retries over the day instead of doing them all in one run.
                spread = (zlib.crc32(row["company"].encode()) % 600) / 1000 * (SETTINGS["guess_every"] or dt.timedelta(0))
                guesses[row["company"]] = (now - spread).isoformat()
            if loaded is not None:
                guesses.pop(row["company"], None)
        if loaded is None and careers and not entry.startswith("website:"):
            loaded = site or load_company(f"website:{careers}", state)  # no board: read the website
    except RateLimited as exc:
        return {"row": row, "deferred": str(exc)}
    except Exception as exc:
        return {"row": row, "error": exc}
    if loaded is None:
        return {"row": row, "missing": True}
    provider, res = loaded
    first_check = f"{provider}:{res['slug']}" not in known_boards

    def worth_a_detail_request(job):
        if not rules.title_ok(job["title"]):
            return False
        posted = job.get("posted")
        if posted is None and first_check:
            return False  # undated and already up the first time this board was read
        if posted is not None and (now - posted > max_age or (window_start and posted < window_start)):
            return False
        return job.get("ambiguous_location") or rules.region_for(job["locations"]) is not None

    details = 0
    postings = []
    for job in res["jobs"]:
        if (job.get("text") is None and job.get("load_detail") and job["key"] not in has_text
                and details < detail_cap and worth_a_detail_request(job)):
            details += 1
            try:
                d = job["load_detail"]() or {}
            except RateLimited:
                d, detail_cap = {}, 0  # stop asking this site for details until next run
            except Exception:
                d = {}
            if d.get("locations"):
                job["locations"] = d["locations"]
            if "text" in d:
                job["text"] = d["text"]
            job["url"] = d.get("url") or job["url"]
            job["posted"] = job.get("posted") or d.get("posted")
        postings.append({k: job.get(k) for k in ("key", "title", "locations", "url", "posted", "text")})
    meta = {k: v for k, v in res.items() if k != "jobs"}
    return {"row": row, "provider": provider, "res": meta, "postings": postings, "details": details}



# ----------------------------------------------------------------- main loop
def notify(count, sample):
    try:
        from plyer import notification  # optional: pip install plyer
        notification.notify(title=f"Job Watcher: {count} new match{'es' if count != 1 else ''}",
                            message=sample[:250], timeout=10)
    except Exception:
        pass


def check_window(cfg, state, now, since_check, hours):
    """Start of the look-back window for a since-last-check run, or None for a normal run."""
    if not since_check:
        return None
    if hours:
        return now - dt.timedelta(hours=hours)
    last = parse_date(state.get("last_check"))
    if last:
        # small overlap so postings published while the last check was running are not missed
        return last - dt.timedelta(minutes=cfg.get("check_overlap_minutes", 15))
    return now - dt.timedelta(hours=cfg.get("first_check_lookback_hours", 24))


def fetch_all(cfg, state, store, rows, rules, now, max_age, window_start, verify):
    """Step 1: download each board's full list into the local store, one board per request
    wherever possible. Returns (companies.csv changed?, stats)."""
    check_all = cfg.get("check_all_every_run", True)
    # check_all: every company is read on every run, including ones with no board found yet
    recheck = dt.timedelta(0) if check_all else dt.timedelta(days=cfg.get("recheck_not_found_days", 14))
    cap = int(cfg.get("max_detections_per_run", 40))
    detections_left = float("inf") if check_all or cap <= 0 else cap
    stats = Counter()
    todo = []
    for r in rows:
        prev = state["companies"].get(r["company"], {})
        base = {"checked": now.isoformat(), "priority": priority_of(r), "region": r.get("region", ""),
                "careers_url": r.get("careers_url", "")}
        if is_manual(r):
            state["companies"][r["company"]] = {**base, "result": "MANUAL",
                                                "error": "Uses its own careers site; check it directly."}
            continue
        missing = prev.get("result") == "NOT FOUND" or (not prev and r.get("status") == "not_found")
        last = parse_date(prev.get("checked")) or parse_date(r.get("verified"))
        if missing and not verify and last and now - last < recheck:
            stats["skipped"] += 1
            continue
        if (prev.get("result") == "NO JOBS READ" and not verify and not check_all and last
                and now - last < dt.timedelta(hours=20) and reads_website(r)):
            stats["unreadable"] += 1  # a careers site with nothing readable is tried once a day
            continue
        entry = entry_for(r)
        explicit = ":" in entry and entry.split(":", 1)[0] in FETCHERS
        needs_detection = entry not in state["resolved"] and (
            not explicit or (entry.startswith("workday:") and not parse_workday(entry.split(":", 1)[1])[2]))
        if needs_detection:
            if detections_left <= 0:  # finding a board takes many guesses; spread them over runs
                state["companies"][r["company"]] = {
                    **base, "result": "PENDING",
                    "error": "Board detection is spread over several runs to avoid rate limits."}
                stats["waiting"] += 1
                continue
            detections_left -= 1
        todo.append(r)

    has_text, known_boards = store.keys_with_text(), store.boards()
    detail_cap = int(cfg.get("max_detail_requests_per_board", 25))
    delay = cfg.get("request_delay_seconds", 0)

    def work(row):
        if CANCEL.is_set():
            return {"row": row, "deferred": "the run was cancelled"}
        try:
            return fetch_board(row, state, rules, has_text, now, max_age, window_start, known_boards, detail_cap)
        finally:
            if delay:
                time.sleep(delay)

    log.info("Reading %d company boards (%d skipped as not found recently, %d waiting for detection%s)...",
             len(todo), stats["skipped"], stats["waiting"],
             f", {stats['unreadable']} careers sites with nothing readable rechecked daily"
             if stats["unreadable"] else "")
    emit("boards_started", total=len(todo))
    results, started, last_note = [None] * len(todo), time.monotonic(), time.monotonic()
    with ThreadPoolExecutor(max_workers=max(1, int(cfg.get("parallel_requests", 8)))) as pool:
        futures = {pool.submit(work, row): i for i, row in enumerate(todo)}
        for done, future in enumerate(as_completed(futures), start=1):
            results[futures[future]] = out = future.result()
            emit("board_done", done=done, total=len(todo), company=out["row"]["company"],
                 ok=not (out.get("error") or out.get("missing") or out.get("deferred")))
            if done == len(todo) or time.monotonic() - last_note >= 30:
                last_note = time.monotonic()
                log.info("  ...%d of %d boards read (%.1f min)", done, len(todo), (last_note - started) / 60)
    if CANCEL.is_set():
        log.info("Run cancelled: boards not read yet were left for the next run.")

    csv_dirty, today = False, now.astimezone().date().isoformat()
    for out in results:
        row = out["row"]
        company = row["company"]
        prev = state["companies"].get(company, {})
        record = {"checked": now.isoformat(), "priority": priority_of(row),
                  "region": row.get("region", ""), "careers_url": row.get("careers_url", "")}
        if out.get("deferred"):
            record.update({k: prev[k] for k in ("provider", "slug", "open") if k in prev})
            record.update(result="RETRY", error=f"Paused: {out['deferred']}. Retried next run.")
            state["companies"][company] = record
            stats["deferred"] += 1
            continue
        if out.get("error"):
            record.update(result="ERROR", error=str(out["error"])[:200])
            state["companies"][company] = record
            log.warning("%s: %s", company, out["error"])
            continue
        if out.get("missing"):
            if reads_website(row) or (SETTINGS["career_sites"] and row.get("careers_url")):
                why = ("The careers page could not be loaded (it may be down or block scripts). "
                       "Retried in a few days.")
            else:
                why = ("No job board found yet (board names are guessed again once a day). Add the "
                       "careers_url or slug in companies.csv, or run --find-career-pages.")
            record.update(result="NOT FOUND", error=why)
            state["companies"][company] = record
            if row.get("status") != "not_found":
                row.update(status="not_found", verified=today)
                csv_dirty = True
            continue

        provider, res = out["provider"], out["res"]
        slug = res["slug"]
        # A website is identified by its careers_url, so the slug column stays empty for it.
        csv_slug = "" if provider == "website" and row.get("careers_url") else slug
        if (row.get("board"), row.get("slug"), row.get("status")) != (provider, csv_slug, "ok"):
            if res.get("switch_to"):
                log.info("  BOARD  %s: careers page uses %s (%s); reading it directly from now on.",
                         company, provider.title(), slug)
            row.update(board=provider, slug=csv_slug, status="ok", verified=today)
            csv_dirty = True
        board = f"{provider}:{slug}"
        stats["new_postings"] += store.save_board(board, company, provider, out["postings"], now,
                                                  res.get("complete", True), board not in known_boards)
        stats["fetched"] += 1
        stats["details"] += out["details"]
        nothing_read = provider == "website" and not out["postings"]
        record.update(result="NO JOBS READ" if nothing_read else "OK", provider=provider, slug=slug,
                      open=len(out["postings"]), error=res.get("note", ""))
        state["companies"][company] = record
    return csv_dirty, stats


RUN_LABELS = {"full": "Checked every company", "refilter": "Re-filtered saved postings",
              "since": "Checked every company, listed since last check", "search": "Searched the web, checked every company",
              "career_pages": "Looked up careers pages", "check": "Checked one company", "scores": "Refreshed company scores"}


def run_once(cfg, since_check=False, hours=None, verify=False, refresh=False, offline=False,
             search=None, look_back=None, find_pages=False, only=None, label=None, source="command line"):
    """search: True runs the web search now, False skips it, None runs it when it is due.
    only: read just these companies (names), or "missing" for the ones with no board or nothing
    readable. Only one run happens at a time across the app, terminals and the scheduled task.
    Every run is recorded for the app's Activity screen."""
    lock = RunLock()
    if not lock.acquire():
        log.warning("Another Job Watcher run is in progress (the app, a terminal or the scheduled "
                    "task), so this one was skipped.")
        emit("run_skipped", reason="busy")
        return []
    CANCEL.clear()
    label = label or (RUN_LABELS["refilter"] if offline else RUN_LABELS["search"] if search
                      else RUN_LABELS["since"] if since_check else RUN_LABELS["full"])
    record = _try(start_run_record, cfg, label, source)
    emit("run_started", offline=offline, label=label)
    summary = {}
    try:
        found = _run_once(cfg, since_check, hours, verify, refresh, offline, search, look_back, find_pages,
                          only, summary)
        status = "cancelled" if CANCEL.is_set() else "finished"
        if record:
            _try(finish_run_record, cfg, record, status, summary)
        emit("run_finished", new=len(found))
        return found
    except Exception as exc:
        if record:
            _try(finish_run_record, cfg, record, "failed", {**summary, "error": str(exc)})
        emit("run_failed", error=str(exc))
        raise
    finally:
        lock.release()


def _try(fn, *args):
    """Run history is a convenience: never let it stop a run."""
    try:
        return fn(*args)
    except Exception as exc:
        log.debug("Could not record the run: %s", exc)
        return None


def refresh_scores(cfg, source="app"):
    """Refresh stock metrics and company scores now (the app's Refresh scores, --refresh-metrics)."""
    lock = RunLock()
    if not lock.acquire():
        emit("run_skipped", reason="busy")
        raise Busy("Another Job Watcher run is in progress. Try again when it finishes.")
    CANCEL.clear()
    record = _try(start_run_record, cfg, RUN_LABELS["scores"], source)
    emit("run_started", offline=True, label=RUN_LABELS["scores"])
    try:
        emit("phase", name="metrics")
        rows = load_company_rows(cfg)
        refresh_metrics(cfg, rows, force=True)
        rescore(rows)
        save_company_rows(cfg, rows)
        if record:
            _try(finish_run_record, cfg, record, "finished", {"companies": len(rows)})
        emit("run_finished", new=0)
    except Exception as exc:
        if record:
            _try(finish_run_record, cfg, record, "failed", {"error": str(exc)})
        emit("run_failed", error=str(exc))
        raise
    finally:
        lock.release()


def _run_once(cfg, since_check=False, hours=None, verify=False, refresh=False, offline=False,
              search=None, look_back=None, find_pages=False, only=None, summary=None):
    now = dt.datetime.now(dt.timezone.utc)
    started = time.monotonic()
    SETTINGS["search_terms"] = cfg.get("search_terms") or SETTINGS["search_terms"]
    SETTINGS["max_pages"] = int(cfg.get("max_pages_per_search", 10))
    SETTINGS["backoff"] = float(cfg.get("rate_limit_backoff_seconds", 30))
    SETTINGS["host_intervals"] = {**SETTINGS["host_intervals"], **cfg.get("host_min_interval_seconds", {})}
    SETTINGS["website_interval"] = float(cfg.get("website_seconds_between_requests", 1.0))
    SETTINGS["career_sites"] = bool(cfg["career_sites"]["enabled"])
    SETTINGS["site_pages_per_run"] = int(cfg["career_sites"]["max_new_pages_per_site"])
    retry = float(cfg.get("retry_board_search_hours", 24))
    SETTINGS["guess_every"] = (dt.timedelta(hours=retry) if cfg.get("check_all_every_run", True) and retry > 0
                               else None)
    LIMITER.reset()
    with _ROBOTS_LOCK:
        _ROBOTS.clear()
    rules = Rules(cfg)
    SETTINGS["rules"] = rules
    excel_path = BASE / cfg.get("excel_file", "job_tracker.xlsx")
    state = load_state(cfg)  # tracked jobs come from postings.db; Excel edits apply until the app is used
    SETTINGS["website_pages"] = state.setdefault("website_pages", {})
    state.pop("rejected", None)  # replaced by the local postings store
    since_check = since_check or cfg.get("since_last_check_by_default", False)
    window_start = check_window(cfg, state, now, since_check, hours)
    if window_start:
        log.info("CHECK  looking for postings since %s (last check: %s)",
                 window_start.astimezone().strftime("%Y-%m-%d %H:%M"),
                 to_local_naive(state.get("last_check")) or "never")


    # How old a posting may be when first found. --hours widens it for that run only.
    max_age = rules.max_age
    if hours:
        max_age = max(max_age, dt.timedelta(hours=hours))
    SETTINGS["window_days"] = max_age.total_seconds() / 86400
    SETTINGS["unsorted_pages"] = int(cfg.get("unsorted_scan_pages", 25))

    # Clear out postings left over from older, looser settings: ones that were already
    # older than the limit when found. Anything you have given a status or note is kept.
    pruned = 0
    for key, j in list(state["jobs"].items()):
        posted, found = parse_date(j.get("posted")), parse_date(j.get("found"))
        untouched = (j.get("status") or "New") == "New" and not j.get("notes")
        if ("allowed_age_h" not in j and untouched and posted and found
                and found - posted > rules.max_age + dt.timedelta(hours=1)):
            del state["jobs"][key]
            pruned += 1
    if pruned:
        log.info("Removed %d older postings that did not meet the %d-hour limit when found.",
                 pruned, rules.max_age.total_seconds() // 3600)

    all_rows = load_company_rows(cfg)
    found_dirty = False
    summary = {} if summary is None else summary
    targeted = only is not None and only != "missing"
    if targeted:
        search = False  # checking one company: no web search, no metrics refresh
    if only is not None:
        SETTINGS["guess_every"] = None  # asked for by name: guess board names now, not once a day
    if not offline:  # step 0: look for companies you are not watching yet
        if search is True or (search is None and search_due(cfg, state, now)):
            emit("phase", name="search")
            found_dirty = run_search(cfg, state, all_rows, now, look_back)
        if find_pages:
            emit("phase", name="career_pages")
            found_dirty = find_career_pages(cfg, state, all_rows, now) or found_dirty
        emit("phase", name="metrics")
    csv_dirty = False if offline or only is not None else refresh_metrics(cfg, all_rows, force=refresh)
    csv_dirty = csv_dirty or found_dirty
    csv_dirty = rescore(all_rows) or csv_dirty
    scores = {r["company"]: (r.get("score", ""), r.get("tier", "")) for r in all_rows}
    rows = sorted((r for r in all_rows if is_enabled(r)),
                  key=lambda r: (priority_of(r), r["company"].lower()))
    names = {r["company"] for r in rows}
    for stale in [e for e in state["companies"] if e not in names]:
        del state["companies"][stale]
    if only == "missing":  # just the companies with no board found or nothing readable
        rows = [r for r in rows if state["companies"].get(r["company"], {}).get("result")
                in (None, "NOT FOUND", "NO JOBS READ", "PENDING", "RETRY") or r.get("status") in ("", "not_found")]
    elif targeted:
        rows = [r for r in rows if r["company"] in set(only)]

    store = PostingStore(BASE / cfg.get("postings_db", "postings.db"))
    try:
        stats = Counter()
        if not offline:  # step 1: download
            changed, stats = fetch_all(cfg, state, store, rows, rules, now, max_age, window_start, verify)
            csv_dirty = changed or csv_dirty
        csv_dirty = take_pending_company_edits(cfg, all_rows) or csv_dirty  # edits made in the app meanwhile
        if csv_dirty:
            save_company_rows(cfg, all_rows)

        # Step 2: filter locally against everything saved.
        emit("phase", name="filter")
        new_jobs = []
        for m in find_new_matches(store, state, rules, now, max_age, window_start):
            state["jobs"][m["key"]] = {
                **m, "found": now.isoformat(), "status": "New", "notes": "", "open": True,
                "level": level_for(m["title"]), "posted": m["posted"].isoformat() if m["posted"] else None,
                "allowed_age_h": round(max_age.total_seconds() / 3600, 1)}
            new_jobs.append(f"{m['company']}: {m['title']} ({m['region']})")
            summary.setdefault("new_keys", []).append(m["key"])
        status = store.status_of(state["jobs"].keys())  # keep open/closed in step with the boards
        for key, j in state["jobs"].items():
            if key in status:
                if j.get("open") and not status[key]:
                    j["closed"] = now.isoformat()
                j["open"] = status[key]
        tracked_open = Counter(j["company"] for j in state["jobs"].values() if j.get("open"))
        for name, c in state["companies"].items():
            c["matches"] = tracked_open.get(name, 0)
        store.export_csv(BASE / cfg.get("all_postings_csv", "all_postings.csv"), set(state["jobs"]))
        saved_total, saved_open = store.counts()
    finally:
        store.close()

    report = []
    if window_start:
        # Everything that appeared in the gap, including jobs a background run already picked up.
        # Uses the board's publish time when it has one, otherwise when this script first saw it.
        # A posting shown in any earlier check is never shown again (except in an --hours look).
        for j in state["jobs"].values():
            appeared = parse_date(j.get("posted")) or parse_date(j["found"])
            if j.get("open") and appeared >= window_start and (hours or not j.get("reported")):
                report.append(j)
        report.sort(key=lambda j: j.get("posted") or j["found"], reverse=True)
        state["last_check_report"] = {"from": window_start.isoformat(), "to": now.isoformat(),
                                      "keys": [j["key"] for j in report]}
        if not hours:
            state["last_check"] = now.isoformat()
            for j in report:
                j["reported"] = now.isoformat()
    state["last_run"] = now.isoformat()

    emit("phase", name="save")
    save_state(cfg, state, since=now)  # keeps edits made in the app during the run
    write_excel(excel_path, state, cfg, scores)

    if offline:
        log.info("Re-filtered %d saved open postings locally (no requests made).", saved_open)
    else:
        sites = ", ".join(f"{s} {n}" for s, n in LIMITER.counts.most_common())
        log.info("Read %d boards with %d requests (%s). %d paused by rate limits and retried next run.",
                 stats["fetched"], sum(LIMITER.counts.values()), sites or "none", stats["deferred"])
        log.info("All postings: %d open saved locally, %d new this run, %d descriptions downloaded.",
                 saved_open, stats["new_postings"], stats["details"])
    open_total = sum(1 for j in state["jobs"].values() if j.get("open"))
    summary.update(
        minutes=round((time.monotonic() - started) / 60, 1), boards_read=stats["fetched"],
        boards_paused=stats["deferred"], requests=sum(LIMITER.counts.values()),
        by_site=dict(LIMITER.counts.most_common(8)), new_postings=stats["new_postings"],
        new_matches=new_jobs, open_matches=open_total, saved_open=saved_open, offline=offline,
        companies=sorted(only) if only is not None and only != "missing" else None)
    log.info("%d new matches, %d open matches in your tracker. Run took %.1f minutes.",
             len(new_jobs), open_total, (time.monotonic() - started) / 60)
    for line in new_jobs:
        log.info("  NEW  %s", line)
        emit("new_match", text=line)
    if new_jobs and not offline:
        from .alerts import new_matches as phone_alert
        phone_alert(cfg, [state["jobs"][k] for k in summary.get("new_keys", [])] or new_jobs)
    if window_start:
        log.info("CHECK  %d posting%s since your last check:", len(report), "" if len(report) == 1 else "s")
        for j in report:
            log.info("  SINCE  %s: %s (%s)  %s", j["company"], j["title"], j["region"], j.get("url", ""))
        if report and cfg.get("desktop_notifications", True):
            notify(len(report), "\n".join(f"{j['company']}: {j['title']}" for j in report[:5]))
    elif new_jobs and cfg.get("desktop_notifications", True):
        notify(len(new_jobs), "\n".join(new_jobs[:5]))
    return report if window_start else new_jobs


__all__ = [n for n in list(globals()) if not n.startswith("__")]
