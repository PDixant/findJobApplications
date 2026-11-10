# Job Watcher setup

Checks Greenhouse, Ashby and Lever job boards directly, filters by your titles, locations and stack, and keeps `job_tracker.xlsx` up to date.

## 1. Install (one time)

1. Install Python 3.10 or newer from python.org (on Windows, tick "Add Python to PATH").
2. Put `job_watcher.py` and `config.json` in one folder, for example `Documents\JobWatcher`.
3. In a terminal, run:
   ```
   pip install requests openpyxl plyer
   ```
   `plyer` is optional and only adds desktop pop-ups for new matches.

## 2. First run

```
cd path\to\JobWatcher
python job_watcher.py
```

This creates `job_tracker.xlsx`, `job_watcher_state.json` and `job_watcher.log`. Open the **Companies** tab: anything marked NOT FOUND needs its slug fixed in `config.json` (see step 4).

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
- Running it again right away shows nothing, since nothing new appeared.
- `--hours 6` looks back a fixed 6 hours instead, without moving your saved timestamp, so it is safe for a quick look.
- It uses the posting date from the job board. If a board does not give one, it uses the time the script first saw the posting.
- A 15 minute overlap catches postings published while the last check was running, without repeating anything the last check already showed you.
- Set `"since_last_check_by_default": true` in `config.json` to make every run behave this way.

Background runs and since-last-check runs work together: keep the scheduled task running so nothing is missed, then run `--since-last-check` whenever you sit down to apply.

## 5. Editing your rules (`config.json`)

Changes apply on the next run, no restart needed.

| Setting | What it does |
|---|---|
| `titles_include` | Title must contain one of these |
| `titles_exclude` | Title must not contain any of these (Staff, Intern, Junior, Manager...) |
| `stack_keywords` | Description must mention at least one (turn off with `require_stack_match: false`) |
| `regions` | Location buckets, their match terms and their Excel colour |
| `locations_exclude` | Locations ignored when deciding the region (India cities) |
| `max_posted_age_days` | Ignore postings older than this when first found |
| `interval_minutes` | Used only with `--loop` |
| `companies_file` | The company list (default `companies.csv`) |
| `parallel_requests` | How many boards are checked at once (default 8) |
| `recheck_not_found_days` | How long to skip a company with no board before trying again |
| `companies` | Optional quick additions, e.g. `["greenhouse:acme"]` |

## 6. The company list (`companies.csv`)

Open it in Excel or any text editor. It is sorted by region priority: Vancouver/BC (1), Ottawa (2), Seattle (3), California (4), Toronto/GTA (5), rest of Canada (6), remote/other US (7). A company with offices in several of your regions is placed by the best one, and the `hubs` column lists all of them.

| Column | Meaning |
|---|---|
| `priority`, `region` | Sort order; boards are checked in this order |
| `company` | Display name in the tracker |
| `board` | `greenhouse`, `ashby`, `lever`, or `auto` to detect it |
| `slug` | The company's ID on that board (optional with `auto`) |
| `enabled` | `no` to stop watching without deleting the row |
| `status`, `verified` | Filled in by the script: `ok` or `not_found`, and when |

**First run takes a few minutes**, because it tries every `auto` company on all three boards. It then writes the detected board and slug back into the file, so later runs only check the boards that exist.

Companies marked `not_found` usually use another hiring system (Workday, their own site). They are skipped and retried every 14 days, or right away with `python job_watcher.py --verify`.

**Adding a company:** add a row. Copy the slug from its careers URL if you know it:
- `job-boards.greenhouse.io/acme` → board `greenhouse`, slug `acme`
- `jobs.ashbyhq.com/Rose Rocket` → board `ashby`, slug `Rose Rocket`
- `jobs.lever.co/acme` → board `lever`, slug `acme`
- Not sure? Set board to `auto` and leave the slug blank.

If the careers page is on the company's own domain, view the page source and search for `greenhouse`, `ashbyhq` or `lever` to find the slug.

Close `companies.csv` before a run if you want the script to save detected boards into it. If it is open, the update waits until the next run.

## 7. Using the tracker

- **Jobs tab:** click a title to open the posting. Set the Status dropdown and type in Notes; both are kept when the script refreshes the file.
- **Close Excel to let a run update the file.** If it is open, that run's update waits until the next run. Nothing is lost.
- **Colours:** green Found cell = discovered in the last 24 hours; Region and Level cells are colour-coded; Status has its own colours; grey italic rows are Rejected, Skipped, or no longer posted. The Legend tab lists every colour.
