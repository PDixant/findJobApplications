# Job Watcher setup

Checks company career sites on Greenhouse, Ashby, Lever, Workday, SmartRecruiters and Workable directly, plus companies' own careers websites, filters by your titles, locations and stack, scores each company on stock, growth, layoff and funding signals, and keeps `job_tracker.xlsx` up to date.

It also finds companies you are not watching yet: twice a day it runs a web search for fresh postings on Ashby, Greenhouse and Lever, and adds every new company it finds to `companies.csv` (see section 7).

Only postings that went up in the last 24 hours are added. Anything older, or already in your tracker, is ignored.

**Two lists.** Each run downloads every board's full list (normally one request per board) and keeps it locally:
- `postings.db` and `all_postings.csv`: every open posting from every board. Open the CSV in Excel to browse everything.
- `job_tracker.xlsx`: only the postings that pass your filters.

Filtering happens on your computer against the saved list, so changing your rules never costs extra requests. After editing `config.toml`, run `python job_watcher.py --refilter` to re-apply your filters instantly with no requests at all.

## 1. Install (one time)

1. Install Python 3.11 or newer from python.org (on Windows, tick "Add Python to PATH"). Python 3.10 also works if you add `tomli` to the install line below.
2. Put `job_watcher.py`, `config.toml` and `companies.csv` in one folder, for example `Documents\JobWatcher`.
3. In a terminal, run:
   ```
   pip install requests openpyxl yfinance plyer
   ```
   `yfinance` adds stock metrics and scores for public companies. `plyer` adds desktop pop-ups. Both are optional.
4. Optional, for finding new companies: copy `.env.example` to `.env` and paste in a search API key (section 7).
5. Check that your settings say what you mean:
   ```
   python job_watcher.py --show-config
   ```

## 2. First run

```
cd path\to\JobWatcher
python job_watcher.py
```

**Every company is checked on every run.** Each company's job board and careers page is read every time. For a company whose board is not known yet, the script first reads its careers page, which usually names the board, and otherwise guesses board names (Greenhouse, Ashby, Lever and so on). Found boards are written back into `companies.csv`, after which each board costs one request per run. Guessing for companies where nothing has been found is retried once a day, spread across runs (`retry_board_search_hours`): guessing hundreds of names every 30 minutes would make runs take over an hour and trip the boards' rate limits, which would then hold up the boards you do read. The first run after adding many companies takes longest; the log ends with how long the run took.

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
- Set `since_last_check_by_default = true` under `[tracker]` in `config.toml` to make every run behave this way.

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
| `board` | `greenhouse`, `ashby`, `lever`, `workday`, `smartrecruiters`, `workable`, `auto` to detect it, or `website` / `other` for companies with their own careers site |
| `slug` | The company's ID on that board (optional with `auto`, empty for `website`) |
| `careers_url` | The company's careers page. Read directly for `website` and `other` companies (section 8), and tried when no job board is found |
| `domain` | The company's email domain, used by `outreach.py` (filled in for you when found) |
| `ats` | Optional hint about the job board the company uses (`greenhouse`, `ashby`, `internal`...). Limits board guessing to that one, or none for `internal` |
| `enabled` | `no` to stop watching without deleting the row |
| `status`, `verified` | Filled in by the script: `ok` or `not_found`, and when |
| metrics columns | Filled in by the script from Yahoo Finance |

Most rows are set to `auto`: the script detects the board on the first run and writes it back. These columns (`stage`, the hubs, board guesses) come from research and general knowledge as of mid-2026, so treat them as a starting point and correct anything you know better.

**Companies with their own careers site** (Amazon, Google, Microsoft, Apple, Meta, RBC and others) are marked `other` or `website`. Their careers page is read directly (section 8). They still get a score, and the Companies tab links to their careers page.

**Companies marked `not_found`** usually use a system the script does not support, or have no careers page listed. `--find-career-pages` can look up their careers pages for you (section 8).

**Importing company lists:** any spreadsheet with a `Company` column works, and a careers page column is used when present (as are location, `ATS Platform`, sector and stack columns):
```
python job_watcher.py --import bc-tech-companies.xlsx us-companies.csv
```
Every sheet in a workbook is read. Companies you already have are matched by name (including names in brackets, so "Slack (Salesforce)" matches Salesforce), careers page or job board, and only get missing details filled in. Links to job aggregators such as Built In are ignored.

**The list grows by itself.** The web search (section 7) adds new companies at the bottom with region `Found by search`, and fills in the board for listed companies that had none.

**Adding a company:** the quickest way is to paste any job posting, job board or careers page link:
```
python job_watcher.py --add https://jobs.lever.co/acme/1234
python job_watcher.py --add https://careers.acme.com --name "Acme"
```
Or add a row yourself. Copy the slug from its careers URL if you know it:
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

## 7. Finding new companies with web search

Your list only covers the companies in `companies.csv`. To find the rest, the script runs the same kind of search you would type into Google, once per job site:

```
site:jobs.ashbyhq.com (intitle:"Software Engineer" OR intitle:"Software Developer" OR intitle:"Web Developer") ("Toronto" OR "Vancouver" OR "Ottawa" OR "Canada") -India
```

limited to the past day. Every result link names the company's job board (`jobs.ashbyhq.com/acme/...` means Ashby, slug `acme`). Companies you are not watching yet are added to `companies.csv` with region `Found by search`, and from that run on their whole board is read directly, so you get the real posting date, the full description for your stack filter, and every later posting too. A listed company with no board found yet gets the board from the search instead. The log shows each one as `ADDED` or `BOARD`.

**Set it up (one time):**
1. Sign up at [serper.dev](https://serper.dev) (Google results, 2,500 free queries when you sign up, no card needed).
2. Copy `.env.example` to `.env` in the same folder and paste your key after `SERPER_API_KEY=`.
3. Run `python job_watcher.py --show-config` and check it says the key was found.

Google's own search API is closed to new sign-ups and shuts down on January 1, 2027, and google.com blocks scripts, so a search service is needed. Brave (`BRAVE_API_KEY`, its own index) and SerpAPI (`SERPAPI_API_KEY`, 250 free a month) also work: set `provider` under `[search]` in `config.toml`.

**What it costs:** each search uses up to sites x `pages_per_query` queries (3 x 3 = 9 by default, usually fewer, because it stops when a page comes back short). Twice a day is about 500 queries a month at most. `monthly_query_limit` (default 1,000) is a hard stop, and the log reports how many have been used this month.

**Useful commands:**
- `python job_watcher.py --search` searches now, even if it is not due.
- `python job_watcher.py --search --look-back week` searches the past week once, which is a good way to fill your list the first time.
- `python job_watcher.py --no-search` skips the search for that run.

Set `new_companies = "review"` under `[search]` if you would rather approve new companies yourself: they are added with `enabled = no`, and you switch on the ones you want.

## 8. Company careers websites

For companies with `board` set to `website` or `other` and a `careers_url` (and for any company whose job board could not be found but has a `careers_url`), the script reads the careers page itself:

1. **Is it really a job board?** Many careers pages are a Greenhouse, Lever, Ashby, Workable, SmartRecruiters or Workday board inside the company's own site. If so, the company is switched to that board in `companies.csv` (the log says `BOARD`) and read through its feed from then on.
2. **Otherwise it reads the job pages.** It collects the job links from the careers page (or the site's sitemap, if the page has none), skips any whose title does not match your `titles`, and opens the rest, a few per run (`new_pages_per_site_per_run`, default 15). Each page is only ever opened once. From each page it reads the standard job details that sites publish for Google Jobs: title, posting date, location and description. Your usual filters then apply.

It follows each site's `robots.txt` rules and spaces requests to each site at least a second apart.

**Do postings appear on the company website first?** Not usually. For companies that use Greenhouse, Lever, Ashby, Workday and the like, the careers page is just a window onto that board, so the board's feed (which this script already reads) is the original and is never later than the website. Sites like LinkedIn and Indeed get postings after both. The website matters for companies with their own hiring system, which is what this section covers.

**What it cannot read:** some big companies' careers sites (Microsoft, Google, Apple and Meta, for example) build the job list with JavaScript in the browser, so there is nothing for a script to read. Those show `NO JOBS READ` on the Companies tab with a note, are rechecked once a day, and still need a manual look.

**Find careers pages for companies with no board:** `python job_watcher.py --find-career-pages` searches `"Company" careers jobs` for up to 25 companies that have no board and no `careers_url`, and fills in the careers page (or the job board it points to). It uses one search query per company, and each company is looked up at most once every 60 days.

## 9. Your settings (`config.toml`)

All settings are in `config.toml`, in plain sections with a note above each line:

| Section | What it controls |
|---|---|
| `[jobs]` | `titles` to include, `skip_titles`, the `must_mention` stack keywords (an empty list turns this off) and `max_age_hours` |
| `[places]` | Locations to `skip`, and your `[[places.region]]` blocks: a name, an optional Excel colour and the place names to match |
| `[search]` | The web search: on or off, `provider`, how often, which sites, title words, places, words to leave out, query budget, and whether new companies are watched or held for review |
| `[career_sites]` | Reading companies' own careers websites: on or off, and pages per site per run |
| `[tracker]` | File names, desktop pop-ups, `since_last_check_by_default`, minutes between checks with `--loop`, `check_all_companies_every_run` and `retry_board_search_hours` |
| `[outreach]` | Recruiter emails (section 11): your resume, tailored resumes folder, `about_me` file, contacts per company, Hunter departments and monthly credit limit, follow-up timing |
| `[advanced]` | Request pacing, retry timing, Workday search terms and quick extra companies. Each line is commented out and shows its default; remove the `#` to change it |

Changes apply on the next run, no restart needed. A setting you delete falls back to its default. A misspelled setting is reported in the log instead of being silently ignored, and a typo such as a missing comma stops the run with the line number.

Run `python job_watcher.py --show-config` to see your settings described in plain English, including the exact search queries.

API keys never go in `config.toml`. They go in `.env`, which is in `.gitignore` so it is never committed.

**Upgrading from `config.json`:** if both files exist, `config.toml` is used and `config.json` is ignored, so you can delete it. A folder with only `config.json` still works.

## 10. Using the tracker

- **Jobs tab:** click a title to open the posting. Set the Status dropdown and type in Notes; both are kept when the script refreshes the file. Filter or sort by the Score column to focus on stronger companies.
- **Close Excel to let a run update the file.** If it is open, that run's update waits until the next run. Nothing is lost.
- **Outreach column:** set it to `Email` for jobs you want a recruiter email for (section 11). The script fills in `Prepared`, `No contact` and `Follow-up`; you set `Sent` once you send an email and `Replied` when someone answers.
- **Colours:** green Found cell = discovered in the last 24 hours; Score cells use the tier colours; Region and Level cells are colour-coded; Status and Outreach have their own colours; grey italic rows are Rejected, Skipped, or no longer posted. The Legend tab lists every colour.

## 11. Recruiter emails (`outreach.py`)

Applying and then writing to a recruiter or the hiring team usually gets more attention than applying alone. `outreach.py` does the legwork: it finds the right people at each company, gathers everything needed to write to them, and hands it to your Claude agent, which writes the emails and creates them as **drafts** in your mailbox. Nothing is ever sent: you read, edit and send each draft yourself.

**Set up (one time):**
1. Get a free key at [hunter.io](https://hunter.io) (50 credits a month) and add `HUNTER_API_KEY=...` to `.env`.
2. Copy `about_me.example.md` to `about_me.md` and fill it in: your name, signature, what you are looking for, work authorization and a few real achievements. The email writer only uses what is in this file and your resume. `about_me.md` stays private (it is in `.gitignore`).
3. Put your resume PDF in this folder as `resume.pdf`, or set `resume` under `[outreach]` in `config.toml`. If you keep tailored resumes, set `tailored_resumes_folder`; a PDF whose name contains the company name is attached for that company.
4. Connect Gmail to your Claude agent (in Claude, the Gmail connector), so it can create drafts.

**Each time:**
1. In `job_tracker.xlsx`, set the Outreach column to `Email` for the jobs you want. Applying first is best, and set Status to `Applied` when you have, so the email can say so. Save and close the file.
2. Run `python outreach.py`. For each company it finds a recruiter and an engineering leader through Hunter (one credit per company, reused for 60 days), checks the addresses, and writes a packet folder under `outreach/`: one file per company with the roles, job descriptions, contacts and the resume to attach, plus `INSTRUCTIONS.md` with the writing rules and your `about_me.md`.
3. Tell your Claude agent: *Read outreach/<folder>/INSTRUCTIONS.md in my job watcher folder and follow it to create my draft emails.* It writes one short email per contact (and a LinkedIn note for each) and creates them as Gmail drafts.
4. Review the drafts in Gmail, attach the resume if your agent's Gmail tool could not, and send. Set the Outreach column to `Sent`.

**Follow-ups:** `python outreach.py --follow-ups` prepares a short follow-up for emails marked `Sent` more than 5 days ago (`follow_up_after_days`) that are not marked `Replied`. Your agent drafts them as replies in the original thread. Only one follow-up is ever suggested per email.

**Drafts with the resume attached, without a Gmail connector:** if your agent cannot create Gmail drafts, the instructions tell it to save the emails to `emails.json` in the packet folder. Then run `python outreach.py --make-drafts outreach/<folder>`, which puts them in your Gmail Drafts folder with the resume attached. This needs `GMAIL_ADDRESS` and a `GMAIL_APP_PASSWORD` (from myaccount.google.com/apppasswords, with 2-Step Verification on) in `.env`.

**Draft files for a desktop mail app (Outlook, Thunderbird):** `python outreach.py --export-drafts outreach/<folder>` writes a `drafts` folder in the packet with two files per email: a `.md` file to read or copy from (with the LinkedIn note), and an `.eml` file that opens as an unsent draft from your own mail account when you double-click it, with the resume attached. The new Outlook for Windows sometimes drops the attachment from such drafts, so check it before sending.

`python outreach.py --credits` shows how many Hunter credits are used and left this month.
