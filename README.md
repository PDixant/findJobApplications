# Job Watcher setup

Checks company career sites on Greenhouse, Ashby, Lever, Workday, SmartRecruiters and Workable directly, filters by your titles, locations and stack, scores each company on stock, growth, layoff and funding signals, and keeps `job_tracker.xlsx` up to date.

Only postings that went up in the last 24 hours are added. Anything older, or already in your tracker, is ignored.

**Two lists.** Each run downloads every board's full list (normally one request per board) and keeps it locally:
- `postings.db` and `all_postings.csv`: every open posting from every board. Open the CSV in Excel to browse everything.
- `job_tracker.xlsx`: only the postings that pass your filters.

Filtering happens on your computer against the saved list, so changing your rules never costs extra requests. After editing `config.json`, run `python job_watcher.py --refilter` to re-apply your filters instantly with no requests at all.

## 1. Install (one time)

1. Install Python 3.10 or newer from python.org (on Windows, tick "Add Python to PATH").
2. Put `job_watcher.py`, `config.json` and `companies.csv` in one folder, for example `Documents\JobWatcher`.
3. In a terminal, run:
   ```
   pip install requests openpyxl yfinance plyer
   ```
   `yfinance` adds stock metrics and scores for public companies. `plyer` adds desktop pop-ups. Both are optional.

## 2. First run

```
cd path\to\JobWatcher
python job_watcher.py
```

**Finding each company's job board takes several runs.** Each `auto` company has to be tried on several boards, so the script detects up to 40 companies per run and leaves the rest marked PENDING. With the background task running every 30 minutes, the whole list is sorted out within a few hours. Detected boards are written back into `companies.csv`, after which each board costs one request per run.

It creates `job_tracker.xlsx`, `all_postings.csv`, `postings.db`, `job_watcher_state.json` and `job_watcher.log`. Open the **Companies** tab to see which boards were found.

**Being polite to the job boards.** Requests to each site are spaced out (Workable gets the most room, at one every 2 seconds). If a site answers "too many requests", the script waits as long as the site asks, and after three refusals it leaves that site alone for the rest of the run. Those companies show RETRY on the Companies tab and are picked up on the next run, with no errors in the log. The log ends each run with the number of requests made per site.

Workday and SmartRecruiters lists do not include job descriptions, so for those sites a description is downloaded only for postings that could match (right title, fresh, in your regions), and only once.

## 3. Run it in the background

**Windows (Task Scheduler, recommended)**
1. Open Task Scheduler, choose Create Task.
2. General: name it "Job Watcher", tick "Run only when user is logged on".
3. Triggers: New, "At log on", then tick "Repeat task every 30 minutes" for "Indefinitely".
4. Actions: New, Program = `pythonw` (runs with no window), Arguments = `job_watcher.py`, Start in = your JobWatcher folder.
5. Settings: tick "Do not start a new instance" if it is already running.

**Mac**
Run `crontab -e` and add (change the path):
```
*/30 * * * * cd /Users/you/JobWatcher && /usr/bin/python3 job_watcher.py
```

**Quick option on any OS:** leave a terminal open running `python job_watcher.py --loop`.

## 4. "What did I miss?" checks

```
python job_watcher.py --since-last-check
```

Each time you run this, the script saves a timestamp. The next time, it reports only postings that went up in between, including ones a background run already picked up. The first time you use it, it looks back 24 hours.

- Results open on the **Since Last Check** tab, and are also written to `job_watcher.log` with `CHECK` and `SINCE` lines.
- A posting shown in one check is never shown again in a later check.
- `--hours 72` looks back a fixed 72 hours instead, without moving your saved timestamp. It also raises the 24-hour limit for that run, which is useful if your computer was off for a few days.
- It uses the posting date from the job board. Workday only reports whole days ("Posted Today", "Posted Yesterday"), so "Yesterday" counts as within 24 hours; "30+ Days Ago" is always treated as old.
- A posting with no date is treated as already up the first time a company is checked, and as new if it appears on a later check.
- Set `"since_last_check_by_default": true` in `config.json` to make every run behave this way.

## 5. Company scores ("worth applying")

Every company gets a 0 to 100 score and a tier, shown in `companies.csv`, the **Jobs** tab and the **Companies** tab.

| Tier | Score | Meaning |
|---|---|---|
| A (green) | 65+ | Strong signals |
| B (yellow) | 50 to 64 | Mixed signals |
| C (red) | under 50 | Weak signals or recent layoffs |

**Public companies** start at 50, then:
- 12-month share price change: up to plus or minus 15 points
- Revenue growth: from minus 10 to plus 20 points
- Profit margin: up to plus or minus 7.5 points
- Size: plus 5 for over $50B market cap, minus 5 under $1B

Metrics come from Yahoo Finance through `yfinance` and refresh weekly on their own, or right away with `python job_watcher.py --refresh-metrics`. Without `yfinance` installed, public companies show no score.

**Private companies** start from their funding stage (Seed 45, Series A 50, Series B 55, Series C 60, Series D+ or Late 64, Unicorn 68). If you fill in `last_raise` (for example `2026-03`), a raise in the last 18 months adds 8 points and one more than 30 months ago subtracts 8.

**Layoffs in the last 12 months** subtract 12 points (4 if the entry contains the word `minor`). The list ships with 2026 layoffs from the tech.co layoff tracker. Add or clear entries in the `layoffs_12m` column as news comes out.

## 6. The company list (`companies.csv`)

543 companies, sorted by region (Vancouver/BC, Ottawa, Seattle, California, Toronto/GTA, rest of Canada, remote/other US) and alphabetically within each region. A company with offices in several of your regions is placed by the best one; the `hubs` column lists all of them.

| Column | Meaning |
|---|---|
| `priority`, `region` | Sort order; boards are checked in this order |
| `company` | Display name in the tracker |
| `score`, `tier` | Filled in by the script (see section 5) |
| `type` | `public`, `private` or `nonprofit` |
| `ticker` | Stock ticker for public companies (Yahoo Finance format, e.g. `SHOP`, `KXS.TO`) |
| `stage`, `last_raise` | Funding stage and last raise date for private companies |
| `layoffs_12m` | Layoffs in the last year; leave blank if none |
| `board` | `greenhouse`, `ashby`, `lever`, `workday`, `smartrecruiters`, `workable`, `auto` to detect it, or `other` for companies with their own careers site |
| `slug` | The company's ID on that board (optional with `auto`) |
| `careers_url` | Link shown on the Companies tab for `other` companies |
| `enabled` | `no` to stop watching without deleting the row |
| `status`, `verified` | Filled in by the script: `ok` or `not_found`, and when |
| metrics columns | Filled in by the script from Yahoo Finance |

Most rows are set to `auto`: the script detects the board on the first run and writes it back. These columns (`stage`, the hubs, board guesses) come from research and general knowledge as of mid-2026, so treat them as a starting point and correct anything you know better.

**Companies with their own careers site** (Amazon, Google, Microsoft, Apple, Meta, RBC and others) are marked `other`. They cannot be watched automatically, but they still get a score, and the Companies tab links to their careers page.

**Companies marked `not_found`** usually use a system the script does not support. They are skipped and retried every 14 days, or right away with `--verify`.

**Adding a company:** add a row. Copy the slug from its careers URL if you know it:
- `job-boards.greenhouse.io/acme` → board `greenhouse`, slug `acme`
- `jobs.ashbyhq.com/Rose Rocket` → board `ashby`, slug `Rose Rocket`
- `jobs.lever.co/acme` → board `lever`, slug `acme`
- `jobs.smartrecruiters.com/Acme` → board `smartrecruiters`, slug `Acme` (case-sensitive)
- `apply.workable.com/acme` → board `workable`, slug `acme`
- `acme.wd5.myworkdayjobs.com/en-US/External` → board `workday`, slug: paste the whole URL
- Not sure? Set board to `auto` and leave the slug blank.

**How Workday sites are read:** Workday has no way to sort by date, and its keyword search ranks by relevance, which buries today's postings under older ones. So the script reads each company's full list newest first and stops at the first postings older than your window, usually after one to three pages. The Companies tab notes how each site was read. If a site turns out not to be in date order, the script reads further and adds keyword searches as a backup, and the note says so. Large SmartRecruiters employers are read the same way.

For Workday, a slug with just the company name (like `amd`) makes the script try the most common site names. If the Companies tab shows NOT FOUND for a Workday company, open its careers page, click any job, and paste that URL into `slug`.

Close `companies.csv` before a run if you want the script to save updates into it. If it is open, the update waits until the next run.

## 7. Editing your rules (`config.json`)

Changes apply on the next run, no restart needed.

| Setting | What it does |
|---|---|
| `titles_include` | Title must contain one of these |
| `titles_exclude` | Title must not contain any of these (Staff, Intern, Junior, Manager...) |
| `stack_keywords` | Description must mention at least one (turn off with `require_stack_match: false`) |
| `regions` | Location buckets, their match terms and their Excel colour |
| `locations_exclude` | Locations ignored when deciding the region (India cities) |
| `max_posted_age_hours` | Only add postings that went up within this many hours (default 24) |
| `search_terms` | Backup keyword searches, used only on Workday or SmartRecruiters sites that do not list newest first |
| `max_pages_per_search` | How deep to page through those backup searches |
| `unsorted_scan_pages` | How far to read a list that is not in date order (default 25 pages) |
| `metrics_refresh_days` | How often stock metrics refresh (default 7) |
| `interval_minutes` | Used only with `--loop` |
| `parallel_requests` | How many boards are checked at once (default 8) |
| `recheck_not_found_days` | How long to skip a company with no board before trying again |
| `max_detections_per_run` | How many new companies to detect boards for per run (default 40) |
| `max_detail_requests_per_board` | Cap on description downloads per Workday or SmartRecruiters board per run |
| `host_min_interval_seconds` | Minimum seconds between requests to each site |
| `rate_limit_backoff_seconds` | How long to pause a site that says "too many requests" without saying for how long |
| `postings_db`, `all_postings_csv` | File names for the all-postings list |
| `companies` | Optional quick additions, e.g. `["greenhouse:acme"]` |

## 8. Using the tracker

- **Jobs tab:** click a title to open the posting. Set the Status dropdown and type in Notes; both are kept when the script refreshes the file. Filter or sort by the Score column to focus on stronger companies.
- **Close Excel to let a run update the file.** If it is open, that run's update waits until the next run. Nothing is lost.
- **Colours:** green Found cell = discovered in the last 24 hours; Score cells use the tier colours; Region and Level cells are colour-coded; Status has its own colours; grey italic rows are Rejected, Skipped, or no longer posted. The Legend tab lists every colour.
