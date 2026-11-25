# Pathfinder guide

Everything in detail. New here? Start with the [README](../README.md).

**Using it:** [How it finds jobs](#how-it-finds-jobs) · [Settings](#settings) · [Finding new companies](#finding-new-companies) · [Your company list](#your-company-list) · [Running by itself](#running-by-itself) · [Updates](#updates)

**Applying:** [Claude's help](#claudes-help) · [The fine print](#the-fine-print) · [People you know](#people-you-know) · [Recruiter emails](#recruiter-emails) · [Pipeline and follow-ups](#pipeline-and-follow-ups) · [Phone alerts](#phone-alerts)

**Reference:** [What leaves your computer](#what-leaves-your-computer) · [Where your keys are kept](#where-your-keys-are-kept) · [Staying safe](#staying-safe) · [Commands](#commands) · [Your files](#your-files) · [Opening the app](#opening-the-app) · [The Windows installer](#the-windows-installer) · [For developers](#for-developers)

---

## How it finds jobs

- **Every company, every run.** Each company's job board is read through its public feed, usually in one request. For a company whose board isn't known yet, Pathfinder reads its careers page, which usually names the board.
- **Two lists.** Every open posting is saved in `postings.db` (and `all_postings.csv` for Excel). Only postings that pass your filters become your **matches**. Filtering happens on your computer, so after changing a filter, **Re-filter saved postings** (offered right after you save, and under **Other ways to run**) applies it instantly without asking any website again.
- **One run at a time.** A second run, from the app or a terminal, waits its turn.
- **Polite to job sites.** Requests are spaced out. If a site says "too many requests", Pathfinder waits as asked and comes back on the next run.
- **New since your last run.** **Today** lists what your latest run found apart from what came earlier in the day, and **Jobs → Found: Your last run** filters to the same jobs. Run it at night and again in the morning, and the morning list is just what was posted overnight.
- **"What did I miss?"** Under **Run now**, **Other ways to run → Run and list what's new** shows only postings that appeared since you last looked.
- **Every step shows.** After the job boards are read, a run still saves the postings, finds your matches, writes `all_postings.csv` and saves your tracker. **Today** lists those steps as they happen, and **Cancel** gives way to "Finishing up" once there's nothing left to stop.

## Settings

Change them in the app under **Settings**. Beside the form, **In plain words** sums up what they do: the checks a job must pass, how new companies are found and what else is on. As you edit, it shows the result of your unsaved changes and marks each check they affect.

> [!NOTE]
> Your settings are saved in `my_settings.toml`, made the first time you run Pathfinder from `config.toml` (the starting point everyone shares). Only your copy ever changes, and git ignores it. Updates never touch it, and your changes never reach anyone else.

Settings has six tabs:

| Tab | What's in it |
|---|---|
| **Which jobs** | Titles to include and skip, tech the description must mention, how recently it was posted, your years of experience, and your regions and places to skip |
| **Finding companies** | The web search (service, how often, job sites, places, your own searches and a monthly limit) and companies' own careers pages |
| **Runs and alerts** | When it runs by itself, whether it keeps running after you close it, desktop pop-ups, "since last check" and phone alerts |
| **Claude and email** | What Claude can see, fit scores, a monthly limit, your resume files, recruiter emails and people you know |
| **Keys** | Your keys, saved in your computer's password store |
| **Updates** | Your version, what's new and the Update button |

**Years of experience.** Set yours, and postings asking for more (for example "8+ years of experience") are left out. **Stretch allowance** lets through jobs asking for a little more than you have: with 5 years and an allowance of 1, a 6+ year job counts and a 7+ doesn't. Choose **flag** instead of **skip** to keep those jobs, marked with the years they ask for.

> [!TIP]
> Prefer a text editor? `my_settings.toml` has a note above every setting. A typo stops the run and names the line.

## Finding new companies

Your list only covers companies you already know. To find more, Pathfinder runs the same search you'd type into Google, once or twice a day, for postings from the past day:

```text
site:jobs.ashbyhq.com (intitle:"Frontend" OR intitle:"Full Stack") ("Vancouver" OR "Canada") -India
```

Every result links to a company's job board, so new companies are added and read directly from then on.

1. Sign up at [serper.dev](https://serper.dev) (2,500 free searches, no card).
2. Paste the key under **Settings → Keys**.

**Seeing and editing the searches.** **Settings → Finding companies** lists every search exactly as it will be sent, updated as you edit:

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
| `score`, `tier`, `status`, `verified` | Filled in for you. `score` is the **company signal**: a rough 0 to 100 read of public facts (share price, revenue growth, margin and size for public companies; funding stage and last raise for private ones; lower after layoffs). It is not advice on whether to apply. |

Board IDs by hand: `job-boards.greenhouse.io/acme` is greenhouse `acme`, `jobs.lever.co/acme` is lever `acme`, and for Workday paste the whole careers URL. Not sure? Use `auto`.

</details>

**Big employers.** Amazon, Oracle Recruiting, Eightfold (Microsoft and others) and iCIMS are read through their own job search. A few sites (Google, Apple, Meta) still can't be read and show **Nothing readable**.

## Running by itself

By default, Pathfinder runs only while its window is open, and closing the window quits it. While it's open, it also checks at the times under **Settings → Runs and alerts** (8:00, 12:00, 16:00 and 20:00 to start).

Want it checking all day? Turn on **Keep running after closing**. Closing the window then leaves it in the tray by the clock (Windows), and **Start when you sign in** opens it there each time you sign in.

## Updates

When a new version is on GitHub, **New version ready** appears at the top right of the app, with what changed. **Update now** downloads it, installs anything new it needs, and reopens Pathfinder on it. **Settings → Updates** shows your version and has **Check for updates**.

| How you got Pathfinder | How it updates |
|---|---|
| `git clone` | Pulls the new commits. If you changed code yourself, it stops and tells you which files. |
| Download ZIP | Downloads the newest files into your folder. |
| The installer | Downloads the newest installer from this project's Releases page, checks its SHA-256 against the release's `SHA256SUMS.txt`, then runs it and reopens Pathfinder. |

Your settings, company list, jobs and keys are never touched.

---

## Claude's help

Add your Anthropic key under **Settings → Keys**. Each use sends one posting plus your resume and `about_me.md` to Anthropic, and never more than your monthly limit (300 by default).

- **Fit scores.** Claude marks each requirement **Have it**, **Partly** or **Missing**, and gives a verdict: **Apply**, **Stretch** (one gap you can bridge) or **Skip**. New matches are scored after each run.
- **Tailored resumes.** Put your LaTeX resume in the folder as `resume.tex` (and your own rules as `resume_rules.md`, starting from `resume_rules.example.md`). **Tailor my resume** rewrites it for one posting without inventing anything. Numbers that would help come back as a "numbers to find" list. With [MiKTeX](https://miktex.org) or [Tectonic](https://tectonic-typesetting.github.io) installed you get a PDF too, and Outreach attaches it.

## The fine print

Read from each posting, on your computer. Pay has its own column in **Jobs**; the rest sits on the quiet line under each title, and anything that could rule a job out carries a warning sign:

| You'll see | Means |
|---|---|
| **Pay** | The pay range, in CAD for Canadian jobs and USD otherwise |
| **Remote / Hybrid / On-site** | From the title, location and description |
| **7+ yrs** | Years of experience asked for (with a warning sign when it's more than yours) |
| **No visa sponsorship**, **Security clearance**, **Citizens only** | Work permit limits |
| **Relocation help** | Relocation offered |
| **Reposted**, **Open 60 days** | Posted before, or up for a long time |

Filter by them in **Jobs** with **Fit**, and under **More** with **Where you work** and **Only**.

## People you know

1. On LinkedIn: **Settings → Data privacy → Get a copy of your data**, pick **Connections**. LinkedIn emails you `Connections.csv` within about 10 minutes.
2. In Pathfinder: **Settings → Claude and email → People you know → Import Connections.csv**.

Every match then shows how many people you know there, with their names in the match's details. The list stays on your computer.

## Recruiter emails

Applying and then writing to a recruiter usually gets more attention than applying alone.

1. Set **Outreach** to **Email** on a job, then open **Outreach**.
2. **Find people** finds a recruiter and an engineering lead (one [Hunter](https://hunter.io) credit).
3. **Write drafts with Claude** writes short emails from your `about_me.md` and resume, never inventing anything.
4. Edit, then **Open in mail app** (resume attached) or **Gmail draft**. Press **I sent it** after sending, and Pathfinder reminds you to follow up.

> [!NOTE]
> Nothing is ever sent for you. Fill in `about_me.md` first (copy `about_me.example.md`), and put your resume in the folder as `resume.pdf`.

## Pipeline and follow-ups

The **Pipeline** screen has a column per status: New, Applied, Interviewing, Offer, Rejected and Skipped. Drag a card, or use its **Move to** menu. At the top: how many you've applied to, how often you hear back, how many you're interviewing for, and applications per week. **Time to follow up** lists applications and emails with no reply after a few days; **I followed up** resets the clock.

## Phone alerts

1. Install the free [ntfy](https://ntfy.sh) app on your phone.
2. In Pathfinder: **Settings → Runs and alerts → Make me a private topic**.
3. In ntfy, tap **+** and subscribe to that topic. **Send a test alert** to check.

New matches arrive with the company, title, region and a link, nothing else about you.

> [!WARNING]
> Anyone who knows your topic name can read your alerts. Use the long random topic Pathfinder makes, keep it out of screenshots and bug reports, and make a new one (and subscribe to it) if it ever gets out.

---

## What leaves your computer

Pathfinder has no server or account of its own: your settings, jobs, tracker, resumes and contacts are files on your computer. These are the only places it sends anything, and every one except the job boards and update checks is off until you add its key.

| Service | When | What it receives | Needed? |
|---|---|---|---|
| **Job boards and careers sites** (Greenhouse, Lever, Ashby, Workday, companies' own sites and others) | Every run | Ordinary requests for their public job listings. Nothing about you. | Yes, this is how it finds jobs |
| **[Anthropic](https://www.anthropic.com/legal/privacy)** (Claude) | When you ask for a fit score, a tailored resume or email drafts, and after a run if **score new matches** is on | The posting's text and your `about_me.md`. Fit scores and drafts also get `resume.pdf`; tailoring gets `resume.tex` and `resume_rules.md`. Drafts also get each contact's name, email and role, and your earlier email when following up. | Optional |
| **Serper**, **Brave** or **SerpApi** (whichever key you add) | Finding new companies, within your monthly query limit, and **Find career pages** | Search phrases built from your job titles and places, or a company's name | Optional |
| **[Hunter](https://hunter.io)** | Outreach: **Find people** and checking an address | A company's name or web address, and the email addresses being checked | Optional |
| **Gmail** | Only when you press **Gmail draft** | That email and its attached resume, saved to your own Drafts folder | Optional |
| **[ntfy](https://ntfy.sh)** | When a run finds new matches, if you set a topic | Each new match's company, title, region and link | Optional |
| **Yahoo Finance** (through `yfinance`) | **Refresh signals**, about once a week | The stock tickers in your company list | Optional extra |
| **GitHub** | A few times a day, and when you update | A request for the newest version. Nothing about you. | Built in |

Each provider keeps and handles what it receives under its own terms; read them before you add its key. Pathfinder shows a short notice the first time it would send anything to Claude.

## Where your keys are kept

Keys go into your computer's own password store, under the name **Job Watcher** (the app's first name, kept so older keys are still found):

| Computer | Password store |
|---|---|
| Windows | Credential Manager (**Windows Credentials**) |
| Mac | Keychain Access (login keychain) |
| Linux | Your desktop's keyring through Secret Service: GNOME Keyring or KDE Wallet |

If a computer has no password store (some Linux setups, like a server with no desktop), Pathfinder says so and asks before saving a key in `.env`, a plain text file that only your user account can read. `.env` is never shared in git. Keys typed into a terminal session as environment variables are used first and never saved.

## Staying safe

- **Only public websites.** Pathfinder reads careers pages only over `http` or `https`, and never on this computer or a private network (addresses like `localhost`, `192.168.x.x` or `10.x.x.x`). Every redirect and the address it finally connects to are checked too, and each page is capped in size. A company whose page breaks these rules shows why on the **Companies** screen.
- **Checked updates.** A downloaded update must be exactly the version GitHub named, and it's copied in all at once or not at all. The Windows app takes a new installer only from this project's own [Releases](https://github.com/PDixant/findJobApplications/releases) page, and only after its SHA-256 matches the release's `SHA256SUMS.txt`.
- **Job postings are text, not instructions.** Claude is told to treat each posting as untrusted text to read. A tailored resume is never turned into a PDF if Claude added commands that would read your files or run programs, and LaTeX runs with those commands turned off.

---

## Commands

After installing, `pathfinder` works in any terminal. `jobwatcher`, the app's first name, is the same command and still works.

| Command | What it does |
|---|---|
| `pathfinder` | Open the app (or bring it to the front) |
| `pathfinder --browser` | Open it in your web browser |
| `pathfinder run` | A run in the terminal |
| `pathfinder run --refilter` | Re-apply your filters to saved postings, without asking any website |
| `pathfinder run --since-last-check` | Only what's new since you last looked (`--hours 72` for a fixed window) |
| `pathfinder run --search` | Search for new companies now (`--look-back week` to look further back) |
| `pathfinder run --add <link>` | Watch a company from any job link |
| `pathfinder run --import list.xlsx` | Add the companies in a spreadsheet |
| `pathfinder run --find-career-pages` | Look up careers pages for companies with no board |
| `pathfinder run --show-config` | Explain your settings in plain words |
| `pathfinder outreach` | Prepare recruiter emails from the terminal |
| `pathfinder folder` | Open the folder with your settings and data |

`python job_watcher.py` and `python outreach.py` take the same options.

## Your files

Your own files are listed in `.gitignore`, so they never reach GitHub and updates never change them.

| File | What it is | Shared in git? |
|---|---|---|
| `config.toml`, `companies.csv` | The starting settings and company list everyone shares | Yes |
| `my_settings.toml`, `my_companies.csv` | **Your** settings and company list | No |
| `.env` | Keys, only if your computer has no password store, or you keep them in a file yourself | No |
| `about_me.md`, `resume.pdf`, `resume.tex`, `resume_rules.md`, `resumes/` | You, your resumes and your tailoring rules | No |
| `outreach/` | Email drafts and the contacts cache | No |
| `postings.db`, `job_tracker.xlsx`, `all_postings.csv`, `job_watcher_state.json`, `job_watcher.log` | Saved postings, your tracker, run history and the log | No |

> [!NOTE]
> `job_tracker.xlsx` is a read-only copy of your matches for Excel. Edit statuses and notes in the app.

## Opening the app

| Do this | What you see |
|---|---|
| Click **Pathfinder** on the Desktop, in the Start menu or at the top of the Pathfinder folder | Just the app window |
| Type `pathfinder` in any terminal | The app window, with its log in the terminal |

Moved or renamed the folder? Double-click **Install Pathfinder** again so the shortcuts follow. **Uninstall Pathfinder** removes the shortcuts and the command, and keeps your data.

## The Windows installer

Every release has three files, built by GitHub Actions from the tagged source:

| File | What it is |
|---|---|
| `PathfinderSetup.exe` | The installer. No Python, no admin rights; installs to your user folder and keeps its data in `%APPDATA%\JobWatcher`, which uninstalling leaves alone. |
| `Pathfinder-portable-win64.zip` | The same app with no installer: unzip anywhere and open `Pathfinder.exe`. |
| `SHA256SUMS.txt` | The SHA-256 of each file. `Get-FileHash .\PathfinderSetup.exe -Algorithm SHA256` in PowerShell should print the same. |

To publish a version (maintainers):

1. Set the version in `pyproject.toml` and `jobwatcher/__init__.py`.
2. Tag it and push the tag:

   ```bash
   git tag v1.0.0
   git push origin v1.0.0
   ```

The workflow, `.github/workflows/release.yml`, runs the tests, builds the interface and `Pathfinder.exe` with PyInstaller (`packaging/pathfinder.spec`), starts it once to be sure it works, wraps it with Inno Setup (`packaging/pathfinder.iss`), writes `SHA256SUMS.txt` and publishes the release. **Actions → release → Run workflow** does a dry run that keeps the files as artifacts without publishing. It stops if the tag and `pyproject.toml` disagree, because the app's update check compares them.

The installer isn't code-signed. Signing needs a certificate bought in the publisher's own name; add it as the repository secrets `WINDOWS_CERT_PFX` (the `.pfx`, base64) and `WINDOWS_CERT_PASSWORD`, and the workflow signs the programs and the installer.

## For developers

- **Code:** the `jobwatcher` package. `runner` does a run, `boards` and `systems` read job boards, `filters` and `signals` decide matches, `server` is the app's API (on 127.0.0.1 only, with a per-launch token), `desktop` opens the window and `updates` handles updates.
- **Packages:** `pyproject.toml` is the one list. `requirements.txt` has only what a run needs, `requirements-app.txt` adds the desktop app, `requirements-extras.txt` the optional stock metrics and pop-ups, and `requirements-dev.txt` the tests.
- **Data folder:** `JOBWATCHER_HOME` overrides it. By default it's the source folder, or `%APPDATA%\JobWatcher` for an installed copy.
- **Checks on GitHub:** `.github/workflows/tests.yml` runs the tests on Windows, macOS and Linux with Python 3.10 and 3.13, runs the real installer and uninstaller on each, checks the committed interface is up to date, and looks for known security problems in the packages (pip-audit and npm audit) every push and every Monday. Dependabot (`.github/dependabot.yml`) proposes package updates weekly.
- **Tests** use fake job boards, a fake search service and a fake Claude, so they make no outside requests:

  ```bash
  pip install -r requirements-dev.txt
  python -m pytest -q tests
  ```

- **Design:** the interface follows [Kanso](design/README.md) ([PDF](design/Kanso-Design-Language.pdf)). Its tokens are CSS custom properties at the top of `ui/src/styles.css`, and the shared pieces are in `ui/src/components/bits.tsx`. Use only those values; where the PDF is silent, follow its ten principles and note the choice in `docs/design/README.md`.
- **Interface:** React and TypeScript in `ui/` (Node 20+):

  ```bash
  cd ui
  npm install
  npm run dev       # and in another terminal: python app.py --dev
  npm run build     # writes jobwatcher/ui_dist; commit it with your change
  ```
