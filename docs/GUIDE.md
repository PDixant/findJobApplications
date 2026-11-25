# Job Watcher guide

Everything in detail. New here? Start with the [README](../README.md).

**Using it:** [How it finds jobs](#how-it-finds-jobs) · [Settings](#settings) · [Finding new companies](#finding-new-companies) · [Your company list](#your-company-list) · [Running by itself](#running-by-itself) · [Updates](#updates)

**Applying:** [Claude's help](#claudes-help) · [The fine print](#the-fine-print) · [People you know](#people-you-know) · [Recruiter emails](#recruiter-emails) · [Pipeline and follow-ups](#pipeline-and-follow-ups) · [Phone alerts](#phone-alerts)

**Reference:** [Commands](#commands) · [Your files](#your-files) · [Opening the app](#opening-the-app) · [The Windows installer](#the-windows-installer) · [For developers](#for-developers)

---

## How it finds jobs

- **Every company, every run.** Each company's job board is read through its public feed, usually in one request. For a company whose board isn't known yet, Job Watcher reads its careers page, which usually names the board.
- **Two lists.** Every open posting is saved in `postings.db` (and `all_postings.csv` for Excel). Only postings that pass your filters become your **matches**. Filtering happens on your computer, so after changing a filter, **Re-filter saved postings** applies it instantly without asking any website again.
- **One run at a time.** A second run, from the app or a terminal, waits its turn.
- **Polite to job sites.** Requests are spaced out. If a site says "too many requests", Job Watcher waits as asked and comes back on the next run.
- **"What did I miss?"** **Run and list since last check** shows only postings that appeared since you last looked.

## Settings

Change them in the app under **Settings**. A side panel explains them in plain words as you type.

> [!NOTE]
> Your settings are saved in `my_settings.toml`, made the first time you run Job Watcher from `config.toml` (the starting point everyone shares). Only your copy ever changes, and git ignores it. Updates never touch it, and your changes never reach anyone else.

| Section | What it controls |
|---|---|
| **Which jobs** | Titles to include and skip, tech the description must mention, how recently it was posted, and your years of experience |
| **Where** | Your regions, and places to skip |
| **Finding new companies** | The web search: service, how often, job sites, places, your own searches and a monthly limit |
| **Careers websites** | Reading companies' own careers pages |
| **Runs and alerts** | Your tracker file, desktop pop-ups and "since last check" |
| **Recruiter emails** | Your resume, about-me file, contacts per company and follow-up timing |
| **Automatic runs** | When it runs by itself while open, and whether it stays in the tray |
| **AI help (Claude)** | Fit scores for new matches, a monthly limit and your resume files |
| **Phone alerts** | The ntfy server |

**Years of experience.** Set yours, and postings asking for more (for example "8+ years of experience") are left out. **Stretch allowance** lets through jobs asking for a little more than you have: with 5 years and an allowance of 1, a 6+ year job counts and a 7+ doesn't. Choose **flag** instead of **skip** to keep those jobs, marked with the years they ask for.

> [!TIP]
> Prefer a text editor? `my_settings.toml` has a note above every setting. A typo stops the run and names the line.

## Finding new companies

Your list only covers companies you already know. To find more, Job Watcher runs the same search you'd type into Google, once or twice a day, for postings from the past day:

```text
site:jobs.ashbyhq.com (intitle:"Frontend" OR intitle:"Full Stack") ("Vancouver" OR "Canada") -India
```

Every result links to a company's job board, so new companies are added and read directly from then on.

1. Sign up at [serper.dev](https://serper.dev) (2,500 free searches, no card).
2. Paste the key under **Settings → Keys and accounts**.

**Seeing and editing the searches.** **Settings → Finding new companies** lists every search exactly as it will be sent, updated as you edit:

- **Same titles as Which jobs** searches for your job titles. A long list is split over a few searches, because Google ignores words past the 32nd.
- **Your own searches**: one per line, in Google's search syntax. Keep a `site:` part so results link to job boards.
- **Edit as my own** copies a built search into your list so you can change it. Turn off **Built searches** to run only yours.

> [!IMPORTANT]
> Each search reads up to **Pages per search** result pages, so a run uses about *searches × pages* queries. 3 pages is plenty. **Monthly search limit** is a hard stop.

## Your company list

About 970 companies to start, in `my_companies.csv` (yours, made from the shared `companies.csv`). Add more in **Companies → Add companies** by pasting any job link, or by importing a spreadsheet with a Company column. Each company gets a 0 to 100 "worth applying" score from its stock, growth, funding and recent layoffs.

<details>
<summary>What the columns mean</summary>
<br>

| Column | Meaning |
|---|---|
| `priority`, `region`, `hubs` | Sort order, its region and every place it has offices |
| `board`, `slug` | Its job board (`greenhouse`, `ashby`, `lever`, `workday`, `smartrecruiters`, `workable`, `auto` to detect it, or `website`) and its ID there |
| `careers_url`, `domain` | Its careers page and email domain |
| `type`, `ticker`, `stage`, `last_raise`, `layoffs_12m` | Used for the score |
| `enabled` | `no` stops watching it without deleting the row |
| `score`, `tier`, `status`, `verified` | Filled in for you |

Board IDs by hand: `job-boards.greenhouse.io/acme` is greenhouse `acme`, `jobs.lever.co/acme` is lever `acme`, and for Workday paste the whole careers URL. Not sure? Use `auto`.

</details>

**Big employers.** Amazon, Oracle Recruiting, Eightfold (Microsoft and others) and iCIMS are read through their own job search. A few sites (Google, Apple, Meta) still can't be read and show **Nothing readable**.

## Running by itself

By default, Job Watcher runs only while its window is open, and closing the window quits it. While it's open, it also checks at the times under **Settings → Automatic runs** (8:00, 12:00, 16:00 and 20:00 to start).

Want it checking all day? Turn on **Keep running after closing**. Closing the window then leaves it in the tray by the clock (Windows), and **Start when you sign in** opens it there each time you sign in.

## Updates

When a new version is on GitHub, a banner says so with what changed. **Update now** downloads it, installs anything new it needs, and reopens Job Watcher on it. **Settings → Updates** shows your version and has **Check for updates**.

| How you got Job Watcher | How it updates |
|---|---|
| `git clone` | Pulls the new commits. If you changed code yourself, it stops and tells you which files. |
| Download ZIP | Downloads the newest files into your folder. |
| The installer | Links to the newest installer. |

Your settings, company list, jobs and keys are never touched.

---

## Claude's help

Add your Anthropic key under **Settings → Keys and accounts**. Each use sends one posting plus your resume and `about_me.md` to Anthropic, and never more than your monthly limit (300 by default).

- **Fit scores.** Claude marks each requirement **Have it**, **Partly** or **Missing**, and gives a verdict: **Apply**, **Stretch** (one gap you can bridge) or **Skip**. New matches are scored after each run.
- **Tailored resumes.** Put your LaTeX resume in the folder as `resume.tex` (and your own rules as `resume_rules.md`, starting from `resume_rules.example.md`). **Tailor my resume** rewrites it for one posting without inventing anything. Numbers that would help come back as a "numbers to find" list. With [MiKTeX](https://miktex.org) or [Tectonic](https://tectonic-typesetting.github.io) installed you get a PDF too, and Outreach attaches it.

## The fine print

Read from each posting, on your computer, and shown as tags on every match:

| Tag | Means |
|---|---|
| **Pay** | The pay range, in CAD for Canadian jobs and USD otherwise |
| **Remote / Hybrid / On-site** | From the title, location and description |
| **Asks 7+ yrs** | Years of experience asked for (highlighted when it's more than yours) |
| **No visa sponsorship**, **Security clearance**, **Citizens only** | Work permit limits |
| **Relocation help** | Relocation offered |
| **Reposted**, **Open 60 days** | Posted before, or up for a long time |

Filter by them in **Jobs** with **Fit**, **Where** and **Only**.

## People you know

1. On LinkedIn: **Settings → Data privacy → Get a copy of your data**, pick **Connections**. LinkedIn emails you `Connections.csv` within about 10 minutes.
2. In Job Watcher: **Settings → People you know → Import Connections.csv**.

Every match then shows how many people you know there, with their names in the match's details. The list stays on your computer.

## Recruiter emails

Applying and then writing to a recruiter usually gets more attention than applying alone.

1. Set **Outreach** to **Email** on a job, then open **Outreach**.
2. **Find people** finds a recruiter and an engineering lead (one [Hunter](https://hunter.io) credit).
3. **Write drafts with Claude** writes short emails from your `about_me.md` and resume, never inventing anything.
4. Edit, then **Open in mail app** (resume attached) or **Gmail draft**. Press **I sent it** after sending, and Job Watcher reminds you to follow up.

> [!NOTE]
> Nothing is ever sent for you. Fill in `about_me.md` first (copy `about_me.example.md`), and put your resume in the folder as `resume.pdf`.

## Pipeline and follow-ups

The **Pipeline** screen has a column per status: New, Applied, Interviewing, Offer, Rejected and Skipped. Drag a card, or use its menu. At the top: how many you've applied to, how often you hear back, and applications per week. **Time to follow up** lists applications and emails with no reply after a few days; **I followed up** resets the clock.

## Phone alerts

1. Install the free [ntfy](https://ntfy.sh) app on your phone.
2. In Job Watcher: **Settings → Phone alerts → Make me a private topic**.
3. In ntfy, tap **+** and subscribe to that topic. **Send a test alert** to check.

New matches arrive with the company, title and a link.

---

## Commands

After installing, `jobwatcher` works in any terminal.

| Command | What it does |
|---|---|
| `jobwatcher` | Open the app (or bring it to the front) |
| `jobwatcher --browser` | Open it in your web browser |
| `jobwatcher run` | A run in the terminal |
| `jobwatcher run --refilter` | Re-apply your filters to saved postings, without asking any website |
| `jobwatcher run --since-last-check` | Only what's new since you last looked (`--hours 72` for a fixed window) |
| `jobwatcher run --search` | Search for new companies now (`--look-back week` to look further back) |
| `jobwatcher run --add <link>` | Watch a company from any job link |
| `jobwatcher run --import list.xlsx` | Add the companies in a spreadsheet |
| `jobwatcher run --find-career-pages` | Look up careers pages for companies with no board |
| `jobwatcher run --show-config` | Explain your settings in plain words |
| `jobwatcher outreach` | Prepare recruiter emails from the terminal |
| `jobwatcher folder` | Open the folder with your settings and data |

`python job_watcher.py` and `python outreach.py` take the same options.

## Your files

Your own files are listed in `.gitignore`, so they never reach GitHub and updates never change them.

| File | What it is | Shared in git? |
|---|---|---|
| `config.toml`, `companies.csv` | The starting settings and company list everyone shares | Yes |
| `my_settings.toml`, `my_companies.csv` | **Your** settings and company list | No |
| `.env` | Keys, if you keep them in a file instead of the app | No |
| `about_me.md`, `resume.pdf`, `resume.tex`, `resume_rules.md`, `resumes/` | You, your resumes and your tailoring rules | No |
| `outreach/` | Email drafts and the contacts cache | No |
| `postings.db`, `job_tracker.xlsx`, `all_postings.csv`, `job_watcher_state.json`, `job_watcher.log` | Saved postings, your tracker, run history and the log | No |

> [!NOTE]
> `job_tracker.xlsx` is a read-only copy of your matches for Excel. Edit statuses and notes in the app.

## Opening the app

| Do this | What you see |
|---|---|
| Click **Job Watcher** on the Desktop, in the Start menu or at the top of the Job Watcher folder | Just the app window |
| Type `jobwatcher` in any terminal | The app window, with its log in the terminal |

Moved or renamed the folder? Double-click **Install Job Watcher** again so the shortcuts follow. **Uninstall Job Watcher** removes the shortcuts and the command, and keeps your data.

## The Windows installer

`packaging/` builds `JobWatcherSetup.exe`, which needs no Python and no admin rights, and keeps its data in `%APPDATA%\JobWatcher`. To publish one, copy `packaging/release.yml` to `.github/workflows/` and push a version tag:

```bash
git tag v0.6.0
git push origin v0.6.0
```

GitHub builds the installer and attaches it to a release.

## For developers

- **Code:** the `jobwatcher` package. `runner` does a run, `boards` and `systems` read job boards, `filters` and `signals` decide matches, `server` is the app's API (on 127.0.0.1 only, with a per-launch token), `desktop` opens the window and `updates` handles updates.
- **Data folder:** `JOBWATCHER_HOME` overrides it. By default it's the source folder, or `%APPDATA%\JobWatcher` for an installed copy.
- **Tests** use fake job boards, a fake search service and a fake Claude, so they make no outside requests:

  ```bash
  pip install -r requirements-dev.txt
  python -m pytest -q tests
  ```

- **Interface:** React and TypeScript in `ui/` (Node 20+):

  ```bash
  cd ui
  npm install
  npm run dev       # and in another terminal: python app.py --dev
  npm run build     # writes jobwatcher/ui_dist; commit it with your change
  ```
