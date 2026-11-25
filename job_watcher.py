#!/usr/bin/env python3
"""
Job Watcher

Checks company career sites on Greenhouse, Ashby, Lever, Workday, SmartRecruiters and
Workable directly through their public JSON feeds, plus companies' own careers websites,
filters postings by title, location and tech stack, scores each company on stock, growth,
layoff and funding signals, and keeps a colour-coded Excel tracker up to date. A web search
step finds companies you are not watching yet and adds them to your list (my_companies.csv).

Your settings live in my_settings.toml, made from config.toml the first time (plain-English notes
inside); only yours ever changes. API keys go in .env or the app's Settings.

Usage:
  python job_watcher.py                 run once
  python job_watcher.py --loop          keep running, checking every check_every_minutes
  python job_watcher.py --since-last-check
                                        only report postings that appeared since the last
                                        time you ran with this flag (first time: past 24h)
  python job_watcher.py --since-last-check --hours 6
                                        same, but look back a fixed 6 hours this time
  python job_watcher.py --show-config   explain your current settings in plain English
  python job_watcher.py --search        run the new-company web search now
  python job_watcher.py --search --look-back week
                                        search a wider window once (day, week or month)
  python job_watcher.py --add URL       watch a company from any job or careers page link
  python job_watcher.py --import companies.xlsx
                                        add every company in a spreadsheet (needs a Company
                                        column; a careers page column is used when present)
  python job_watcher.py --find-career-pages
                                        use web search to find careers pages for companies
                                        with no job board found yet
  python job_watcher.py --verify        re-check every company in your list,
                                        including ones previously marked not_found
  python job_watcher.py --refresh-metrics
                                        refresh stock metrics and scores now, then exit
                                        (needs: pip install yfinance)
  python job_watcher.py --refilter      re-run your filters on the saved postings only,
                                        e.g. after editing my_settings.toml; makes no requests

Every run keeps two lists: postings.db / all_postings.csv hold every open posting from
every board, and job_tracker.xlsx holds the ones that pass your filters.

Your Status and Notes edits in the Excel file are preserved between runs.
Close the Excel file if you want a run to update it; otherwise the update is picked up
on the next run (nothing is lost, everything is kept in the state file).
"""
from jobwatcher import *  # noqa: F401,F403  (outreach.py and older scripts use these names)
from jobwatcher.cli import main

if __name__ == "__main__":
    main()
