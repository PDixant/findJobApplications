"""Command-line entry point for job_watcher.py."""
from __future__ import annotations

from .base import *  # noqa: F401,F403
from .config import *  # noqa: F401,F403
from .companies import *  # noqa: F401,F403
from .discovery import *  # noqa: F401,F403
from .runner import *  # noqa: F401,F403


def main():
    parser = argparse.ArgumentParser(description="Watch job boards for matching postings.")
    parser.add_argument("--loop", action="store_true", help="keep running in the background")
    parser.add_argument("--interval", type=float, help="minutes between checks (overrides config)")
    parser.add_argument("--since-last-check", action="store_true",
                        help="report only postings that appeared since your last check")
    parser.add_argument("--hours", type=float,
                        help="with --since-last-check: look back this many hours instead "
                             "(does not move your last-check timestamp)")
    parser.add_argument("--verify", action="store_true",
                        help="re-check every company in companies.csv, including ones marked not_found")
    parser.add_argument("--refresh-metrics", action="store_true",
                        help="refresh stock metrics and company scores now, then exit (needs yfinance)")
    parser.add_argument("--refilter", action="store_true",
                        help="re-run your filters on the saved postings only; makes no requests")
    parser.add_argument("--show-config", action="store_true",
                        help="explain your current settings in plain English, then exit")
    parser.add_argument("--search", action="store_true",
                        help="run the new-company web search now, even if it is not due")
    parser.add_argument("--no-search", action="store_true", help="skip the web search this run")
    parser.add_argument("--look-back", choices=["day", "week", "month"],
                        help="with --search: how far back to search this time")
    parser.add_argument("--find-career-pages", action="store_true",
                        help="use web search to find careers pages for companies with no board found")
    parser.add_argument("--add", metavar="URL",
                        help="watch a company from a job posting, job board or careers page link")
    parser.add_argument("--name", help="with --add: the company name to show")
    parser.add_argument("--import", dest="import_files", nargs="+", metavar="FILE",
                        help="add the companies in these .xlsx or .csv files to companies.csv")
    args = parser.parse_args()
    if args.hours and not args.since_last_check:
        args.since_last_check = True

    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s",
                        datefmt="%Y-%m-%d %H:%M",
                        handlers=[logging.FileHandler(LOG_PATH, encoding="utf-8"),
                                  logging.StreamHandler(sys.stdout)])
    load_env()

    def settings():
        try:
            return load_config()  # reloaded every cycle so edits apply without a restart
        except ConfigError as exc:
            log.error("Settings problem: %s", exc)
            return None

    if args.refresh_metrics and not args.loop:
        cfg = settings()
        if cfg is None:
            sys.exit(1)
        try:
            refresh_scores(cfg, source="command line")
        except Busy as exc:
            log.error("%s", exc)
            sys.exit(1)
        log.info("Stock metrics and company scores refreshed. Run the watcher to see them in the tracker.")
        return

    if args.show_config or args.add or args.import_files:
        cfg = settings()
        if cfg is None:
            sys.exit(1)
        SETTINGS["career_sites"] = bool(cfg["career_sites"]["enabled"])
        if args.show_config:
            print(describe_config(cfg))
        else:
            try:
                with RunLock():  # never change companies.csv while a run has it open
                    if args.import_files:
                        import_companies(cfg, args.import_files)
                    else:
                        message = add_company(cfg, args.add, args.name)
                        print(message)
                        if not message.startswith("Already"):
                            print(f"Saved to {companies_path(cfg).name}. Run the watcher to read it now.")
            except (ConfigError, Busy) as exc:
                log.error("%s", exc)
                sys.exit(1)
        return

    search = True if args.search else (False if args.no_search else None)
    while True:
        cfg = settings()
        if cfg is None and not args.loop:
            sys.exit(1)
        if cfg is not None:
            try:
                run_once(cfg, since_check=args.since_last_check, hours=args.hours,
                         verify=args.verify, refresh=args.refresh_metrics, offline=args.refilter,
                         search=search, look_back=args.look_back, find_pages=args.find_career_pages)
                # only the first cycle of --loop forces these
                args.verify = args.refresh_metrics = args.find_career_pages = False
                search, args.look_back = (False if args.no_search else None), None
            except Exception:
                log.exception("Run failed")
        if not args.loop:
            break
        minutes = args.interval or (cfg or {}).get("interval_minutes", 30)
        time.sleep(max(minutes, 5) * 60)


__all__ = [n for n in list(globals()) if not n.startswith("__")]
