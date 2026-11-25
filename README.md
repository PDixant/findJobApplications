<div align="center">

# Job Watcher

**Catch software jobs the hour they're posted, and know which ones are worth your time.**

[![tests](https://github.com/PDixant/findJobApplications/actions/workflows/tests.yml/badge.svg)](https://github.com/PDixant/findJobApplications/actions/workflows/tests.yml)
![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-1f3864)
![Windows · macOS · Linux](https://img.shields.io/badge/runs%20on-Windows%20%7C%20macOS%20%7C%20Linux-2a4a80)
[![MIT license](https://img.shields.io/badge/license-MIT-3f9a6a)](LICENSE)

[Get started](#get-started) · [What it does](#what-it-does) · [Staying up to date](#staying-up-to-date) · [Full guide](docs/GUIDE.md)

<img src="docs/screenshots/dashboard.png" alt="The Job Watcher dashboard: new matches, every company's job board being read, and what to do next" width="900">

</div>

Most job sites show you a posting a day or two late, next to everyone else. Job Watcher goes straight to the source: it reads about a thousand companies' own job boards a few times a day, keeps only the roles that fit you, and helps you act while they're fresh.

## What it does

- 🛰️ **Reads job boards directly.** Greenhouse, Lever, Ashby, Workday, Amazon, Oracle and more, plus companies' own careers pages.
- 🎯 **Keeps only your jobs.** Filters by title, seniority, years of experience, location, tech stack and how recently it was posted.
- 🧭 **Finds new companies.** A web search adds companies you weren't watching yet.
- 🤖 **Says if it's worth applying.** Claude compares each match with your resume and answers **Apply**, **Stretch** or **Skip**.
- 📄 **Tailors your resume.** One click rewrites your LaTeX resume for a posting, using only facts already in it.
- 💸 **Reads the fine print.** Pay range, remote or hybrid, visa sponsorship, relocation help and years of experience.
- 🤝 **Shows who you know.** Your LinkedIn connections at each company.
- 🗂️ **Tracks every application.** A pipeline board, follow-up reminders and phone alerts for new matches.

## Get started

> [!NOTE]
> Windows is the smoothest. On a Mac or Linux, run `sh install.sh` in step 2 instead.

**1. Get Job Watcher.** Press the green **Code** button above, then **Download ZIP** and unzip it. Or, if you use git:

```bash
git clone https://github.com/PDixant/findJobApplications.git
```

**2. Double-click `Install Job Watcher`** in the folder. It sets everything up in a few minutes, installing Python first if your computer needs it.

**3. Open Job Watcher** from your Desktop or Start menu. A few quick questions (the jobs you want, where, your skills), and it starts looking.

> [!TIP]
> Add your keys under **Settings → Keys and accounts** to switch on the extras. All of them are optional:
>
> | Key | Turns on | Cost |
> |---|---|---|
> | [Serper](https://serper.dev) | Finding new companies with web search | 2,500 free searches |
> | [Anthropic](https://console.anthropic.com) | Fit scores, tailored resumes, email drafts | Pay per use, with a monthly cap you set |
> | [Hunter](https://hunter.io) | Finding recruiters' emails | 50 free a month |

## Everyday use

| To | Do this |
|---|---|
| **Open it** | Click **Job Watcher** on your Desktop or Start menu, or type `jobwatcher` in any terminal. |
| **Check for jobs now** | Press **Run now**. While the window is open, it also checks by itself a few times a day. |
| **Change what it looks for** | Open **Settings**. A side panel explains your settings in plain words as you edit. |
| **Close it** | Close the window. Nothing keeps running in the background. |

## Staying up to date

When there's a new version on GitHub, a banner appears at the top of the app showing what changed. Press **Update now**: Job Watcher downloads it and reopens on the new version a few seconds later.

> [!IMPORTANT]
> Updates never touch your settings, company list, jobs or keys. Those live in your own files (`my_settings.toml`, `my_companies.csv` and your data), which stay on your computer and are never shared.

## A look inside

<table>
  <tr>
    <td width="50%"><img src="docs/screenshots/job-details.png" alt="A match opened in Jobs, with an Apply fit score, the requirements you meet, pay and workplace"></td>
    <td width="50%"><img src="docs/screenshots/pipeline.png" alt="The Pipeline board with application columns, weekly stats and follow-ups due"></td>
  </tr>
  <tr>
    <td align="center"><b>Jobs</b>: your fit, pay and the fine print for every match</td>
    <td align="center"><b>Pipeline</b>: where every application stands</td>
  </tr>
</table>

<sub>Screenshots use made-up companies.</sub>

## Your data stays yours

- 🔒 **Everything runs on your computer.** Your settings, jobs, resumes and contacts never leave it.
- 🔑 **Keys are stored safely** in Windows Credential Manager (or the Mac Keychain), never in a file that goes to GitHub.
- 🤖 **Claude only sees what it needs**: one posting plus your resume, and only when you use an AI feature.
- 🤝 **It's polite to job sites.** It spaces out requests and follows each site's rules.
- ✉️ **Nothing is ever sent for you.** Recruiter emails are drafts you review and send yourself.

## Something not working?

<details>
<summary><b>The <code>jobwatcher</code> command isn't found</b></summary>
<br>

Close the terminal and open a new one. Still not found? Double-click **Install Job Watcher** again.

</details>

<details>
<summary><b>Clicking Job Watcher does nothing</b></summary>
<br>

What went wrong is written to `job_watcher_errors.log` in the Job Watcher folder. Typing `jobwatcher` in a terminal shows the same messages as they happen.

</details>

<details>
<summary><b>Setup stopped with "Permission denied"</b></summary>
<br>

Another program (often antivirus) was holding a file. Restart your computer, then double-click **Install Job Watcher** again, with a normal double-click rather than "Run as administrator".

</details>

<details>
<summary><b>Windows says "Windows protected your PC"</b></summary>
<br>

Job Watcher isn't code-signed yet. Choose **More info**, then **Run anyway**.

</details>

<details>
<summary><b>I installed Python from the Microsoft Store</b></summary>
<br>

Job Watcher can't use that version. **Install Job Watcher** skips it and sets up Python from python.org instead, so just double-click it again.

</details>

## Learn more

The **[full guide](docs/GUIDE.md)** covers everything in detail:

- [Settings](docs/GUIDE.md#settings) and [finding new companies](docs/GUIDE.md#finding-new-companies)
- [Claude's help: fit scores and tailored resumes](docs/GUIDE.md#claudes-help)
- [Recruiter emails](docs/GUIDE.md#recruiter-emails), [people you know](docs/GUIDE.md#people-you-know) and [phone alerts](docs/GUIDE.md#phone-alerts)
- [All commands](docs/GUIDE.md#commands) and [which files are yours](docs/GUIDE.md#your-files)

<details>
<summary><b>For developers</b></summary>
<br>

The app is Python (FastAPI, served only on your own computer) with a React and TypeScript interface in `ui/`.

```bash
pip install -r requirements-dev.txt
python -m pytest -q tests        # uses fake job boards, no outside requests

cd ui && npm install
npm run dev                      # and in another terminal: python app.py --dev
npm run build                    # writes jobwatcher/ui_dist; commit it with your change
```

More in the [developer notes](docs/GUIDE.md#for-developers).

</details>

## License

[MIT](LICENSE): free to use, change and share. Job Watcher reads public job boards for your own job search, so please use it politely.
