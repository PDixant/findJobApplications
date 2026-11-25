"""Keeping Job Watcher up to date: the app tells you when there's a newer version on GitHub, and its
Update button installs it and restarts.

How a copy updates depends on how it was set up:
  git        a clone of the repository: every new commit on GitHub is a new version, and updating is
             a fast-forward `git pull`. Your settings and data are never in git, so it can't clash.
  download   a folder from GitHub's "Download ZIP": the latest commit on GitHub is compared with the
             one this folder has, and updating downloads that commit and copies its files over Job
             Watcher's own. Your files (settings, company list, data, keys) are never in a download.
  installed  the Windows installer: new releases on GitHub; updating means downloading the new installer.
When an update changes the packages Job Watcher needs, they're installed as the app starts again.
"""
from __future__ import annotations

import io
import subprocess
import tempfile
import zipfile

from .base import *  # noqa: F401,F403
from .config import find_git, make_my_copies

REPO = os.environ.get("JOBWATCHER_UPDATE_REPO", "PDixant/findJobApplications")
BRANCH = os.environ.get("JOBWATCHER_UPDATE_BRANCH", "main")
ROOT = SOURCE_DIR  # the Job Watcher folder being updated
CHECK_EVERY = dt.timedelta(hours=3)
PACKAGE_FILES = ("pyproject.toml", "requirements.txt", "requirements-app.txt")
SHARED_FILES = ("config.toml", "companies.csv")  # yours are copies of these, so they can be put back
NEVER_FROM_DOWNLOAD = {".git", ".venv", "node_modules", ".app_window", "outreach", "resumes", "resume"}
NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


class UpdateProblem(Exception):
    """Updating can't go ahead; the message says why and what to do."""


def version_file():
    """Which commit a downloaded folder has (a clone knows from git)."""
    return ROOT / ".jobwatcher-version"


def cache_path():
    return BASE / ".app_window" / "update.json"


def packages_flag():
    return BASE / ".app_window" / "update-packages"


def kind():
    if getattr(sys, "frozen", False):
        return "installed"
    return "git" if (ROOT / ".git").exists() else "download"


def _version(text):
    return tuple(int(x) for x in re.findall(r"\d+", str(text))[:3])


def _github(path):
    try:
        r = requests.get(f"https://api.github.com/{path}", timeout=10,
                         headers={"Accept": "application/vnd.github+json", "User-Agent": USER_AGENT})
        return r.json() if r.status_code == 200 else None
    except (requests.RequestException, ValueError):
        return None


def git(*args, timeout=60):
    """git in the Job Watcher folder, never asking for a password or opening a console window."""
    exe = find_git()
    if not exe:
        raise UpdateProblem("Job Watcher can't find git. Install Git from git-scm.com, or update this folder "
                            "with GitHub Desktop (Fetch origin, then Pull origin).")
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_SSH_COMMAND": "ssh -o BatchMode=yes"}
    try:
        return subprocess.run([exe, "-C", str(ROOT), *args], capture_output=True, text=True,
                              timeout=timeout, env=env, creationflags=NO_WINDOW)
    except subprocess.TimeoutExpired:
        raise UpdateProblem("git took too long to answer (is the internet connection slow?).") from None
    except OSError as exc:
        raise UpdateProblem(f"git couldn't run ({exc.__class__.__name__}).") from None


def _out(result):
    return (result.stdout or "").strip()


def _change(sha, date, message):
    return {"sha": sha[:7], "date": date, "message": (message or "").strip().splitlines()[0][:200] if message else ""}


# ----------------------------------------------------------------- checking
def _check_git():
    head = _out(git("rev-parse", "HEAD"))
    date = _out(git("log", "-1", "--format=%cI"))
    fetched = git("fetch", "--quiet", "origin", timeout=60)
    if fetched.returncode != 0:
        return {"commit": head[:7], "date": date, "newer": False,
                "problem": "Couldn't reach GitHub to check for updates. Job Watcher tries again later."}
    upstream = _out(git("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")) or f"origin/{BRANCH}"
    behind = int(_out(git("rev-list", "--count", f"HEAD..{upstream}")) or 0)
    ahead = int(_out(git("rev-list", "--count", f"{upstream}..HEAD")) or 0)
    log_ = _out(git("log", "--format=%H%x1f%cI%x1f%s", "-n", "30", f"HEAD..{upstream}"))
    changes = [_change(*line.split("\x1f", 2)) for line in log_.splitlines() if line.count("\x1f") == 2]
    latest = _out(git("rev-parse", upstream))
    problem = None
    if behind and ahead:
        problem = ("This folder has commits that aren't on GitHub, so it can't update by itself. "
                   "Run git pull in the Job Watcher folder.")
    return {"commit": head[:7], "date": date, "latest": latest[:7], "newer": behind > 0, "behind": behind,
            "changes": changes, "can_update": behind > 0 and not ahead, "problem": problem,
            "url": f"https://github.com/{REPO}/compare/{head[:12]}...{latest[:12]}" if behind else None}


def _newest_code_time():
    """When this folder's code was last written. A folder unzipped from GitHub keeps each file's
    commit time, so this is about when its version was made."""
    times = [p.stat().st_mtime for p in (ROOT / "jobwatcher").glob("*.py")]
    return dt.datetime.fromtimestamp(max(times), dt.timezone.utc) if times else None


def _check_download():
    mine = load_json(version_file(), {})
    latest = _github(f"repos/{REPO}/commits/{BRANCH}")
    if not latest:
        return {"commit": (mine.get("commit") or "")[:7] or None, "date": mine.get("date"), "newer": False,
                "problem": "Couldn't reach GitHub to check for updates. Job Watcher tries again later."}
    sha, info = latest["sha"], latest.get("commit") or {}
    when = (info.get("committer") or {}).get("date")
    out = {"commit": (mine.get("commit") or "")[:7] or None, "date": mine.get("date"), "latest": sha[:7],
           "url": f"https://github.com/{REPO}/commits/{BRANCH}"}
    if mine.get("commit"):
        if mine["commit"] == sha:
            return {**out, "newer": False, "behind": 0, "changes": []}
        compare = _github(f"repos/{REPO}/compare/{mine['commit']}...{sha}") or {}
        commits = list(reversed(compare.get("commits") or []))[:30]
        changes = [_change(c["sha"], ((c.get("commit") or {}).get("committer") or {}).get("date"),
                           (c.get("commit") or {}).get("message")) for c in commits]
        return {**out, "newer": True, "behind": compare.get("ahead_by") or len(changes), "changes": changes,
                "can_update": True, "url": compare.get("html_url") or out["url"]}
    # A folder from before updates were tracked: compare its files' age with GitHub's latest.
    code_time, remote = _newest_code_time(), parse_date(when)
    newer = bool(remote and (code_time is None or remote - code_time > dt.timedelta(minutes=10)))
    return {**out, "newer": newer, "behind": None, "can_update": True,
            "changes": [_change(sha, when, info.get("message"))] if newer else []}


def _check_installed(current):
    body = _github(f"repos/{REPO}/releases/latest") or {}
    latest = body.get("tag_name")
    newer = bool(latest) and _version(latest) > _version(current)
    return {"latest": latest, "newer": newer, "url": body.get("html_url"), "can_update": False,
            "changes": [_change("", body.get("published_at"), body.get("name") or latest)] if newer else []}


def check(current, force=False):
    """Is there a newer Job Watcher? Asks GitHub at most every few hours unless forced."""
    data = load_json(cache_path(), {})
    how = kind()
    fresh = (data.get("kind") == how and data.get("checked")
             and dt.datetime.now(dt.timezone.utc) - parse_date(data["checked"]) < CHECK_EVERY)
    if force or not fresh:
        try:
            found = (_check_git() if how == "git" else _check_download() if how == "download"
                     else _check_installed(current))
        except UpdateProblem as exc:
            found = {"newer": False, "problem": str(exc)}
        data = {"newer": False, "behind": None, "changes": [], "can_update": False, "problem": None, "url": None,
                "commit": None, "date": None, "latest": None, **found, "kind": how,
                "checked": dt.datetime.now(dt.timezone.utc).isoformat()}
        cache_path().parent.mkdir(parents=True, exist_ok=True)
        save_json(cache_path(), data)
    return {**data, "current": current, "installed": how == "installed"}


# ----------------------------------------------------------------- updating
def apply():
    """Bring this folder up to date. Returns what changed; the app then restarts to use it."""
    how = kind()
    if how == "installed":
        raise UpdateProblem("Download the new installer from the release page and run it.")
    make_my_copies()  # your settings and companies are in your own files before anything changes
    result = _apply_git() if how == "git" else _apply_download()
    if result["packages"]:
        packages_flag().parent.mkdir(parents=True, exist_ok=True)
        packages_flag().write_text("pyproject.toml changed", encoding="utf-8")
    cache_path().unlink(missing_ok=True)
    log.info("Updated Job Watcher from %s to %s.", result.get("from") or "an older version", result["to"])
    return result


def _apply_git():
    if (ROOT / ".git" / "MERGE_HEAD").exists():
        raise UpdateProblem("A git merge is half done in this folder. In the Job Watcher folder run "
                            "git merge --abort, then press Update again.")
    status = git("status", "--porcelain", "--untracked-files=no").stdout or ""
    dirty = [line[3:].strip().strip('"') for line in status.splitlines() if len(line) > 3]
    for name in [d for d in dirty if d in SHARED_FILES]:
        git("checkout", "--", name)  # the shared copy goes back; yours is in my_settings.toml / my_companies.csv
    others = [d for d in dirty if d not in SHARED_FILES]
    if others:
        listed = ", ".join(others[:5]) + (" and more" if len(others) > 5 else "")
        raise UpdateProblem(f"You've changed {listed} in the Job Watcher folder. Updating would overwrite that, "
                            "so it stopped. Commit or undo those changes (git stash), then press Update again.")
    old = _out(git("rev-parse", "HEAD"))
    pulled = git("pull", "--ff-only", "--quiet", timeout=300)
    if pulled.returncode != 0:
        said = (pulled.stderr or pulled.stdout or "").strip()
        if "fast-forward" in said or "diverg" in said:
            raise UpdateProblem("This folder has commits that aren't on GitHub, so it can't update by itself. "
                                "Run git pull in the Job Watcher folder.")
        raise UpdateProblem("git couldn't download the update: " + (said.splitlines()[-1] if said else "no reason given"))
    new = _out(git("rev-parse", "HEAD"))
    changed = _out(git("diff", "--name-only", old, new)).splitlines()
    return {"from": old[:7], "to": new[:7], "files": len(changed),
            "packages": any(name in PACKAGE_FILES for name in changed)}


def _apply_download():
    latest = _github(f"repos/{REPO}/commits/{BRANCH}")
    if not latest:
        raise UpdateProblem("Couldn't reach GitHub. Check the internet connection and try again.")
    sha = latest["sha"]
    try:
        r = requests.get(f"https://codeload.github.com/{REPO}/zip/{sha}", timeout=120,
                         headers={"User-Agent": USER_AGENT})
        r.raise_for_status()
        archive = zipfile.ZipFile(io.BytesIO(r.content))
    except (requests.RequestException, zipfile.BadZipFile) as exc:
        raise UpdateProblem(f"The download didn't work ({exc.__class__.__name__}). Try again in a minute.") from None
    old = load_json(version_file(), {}).get("commit")
    packages, written = False, 0
    with tempfile.TemporaryDirectory(prefix="jobwatcher-update-") as tmp:
        tmp = Path(tmp)
        for member in archive.infolist():
            parts = Path(member.filename).parts[1:]  # inside the top folder GitHub adds
            if not parts or member.is_dir() or parts[0] in NEVER_FROM_DOWNLOAD or ".." in parts:
                continue
            staged = tmp.joinpath(*parts)
            staged.parent.mkdir(parents=True, exist_ok=True)
            staged.write_bytes(archive.read(member))
        for staged in sorted(p for p in tmp.rglob("*") if p.is_file()):
            rel = staged.relative_to(tmp)
            target = ROOT / rel
            data = staged.read_bytes()
            if target.exists() and target.read_bytes() == data:
                continue
            if str(rel).replace("\\", "/") in PACKAGE_FILES:
                packages = True
            target.parent.mkdir(parents=True, exist_ok=True)
            part = target.with_name(target.name + ".updating")  # same folder, so the swap is instant
            part.write_bytes(data)
            replace_file(part, target)
            written += 1
    when = ((latest.get("commit") or {}).get("committer") or {}).get("date")
    save_json(version_file(), {"commit": sha, "date": when})
    return {"from": (old or "")[:7] or None, "to": sha[:7], "files": written, "packages": packages}


def install_packages_if_needed():
    """After an update that changed Job Watcher's packages: install them, as the app starts again
    (before, Windows would refuse to replace files the running app had open)."""
    flag = packages_flag()
    if not flag.exists() or getattr(sys, "frozen", False):
        return False
    flag.unlink(missing_ok=True)
    if sys.prefix == sys.base_prefix:
        log.warning("Job Watcher was updated and needs new packages, but it isn't running from its own "
                    "environment. Double-click Install Job Watcher to install them.")
        return False
    cmd = [sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "-q", "-e",
           f"{ROOT}[app,extras]"]
    env = {**os.environ, "PIP_CACHE_DIR": str(Path(sys.prefix) / "pip-cache")}
    log.info("Installing the packages this version of Job Watcher needs...")
    done = subprocess.run(cmd, capture_output=True, text=True, env=env, creationflags=NO_WINDOW)
    if done.returncode != 0:
        log.warning("Couldn't install the new packages: %s Double-click Install Job Watcher to finish the update.",
                    (done.stderr or done.stdout or "").strip()[-400:])
        return False
    return True


__all__ = ["check", "apply", "install_packages_if_needed", "kind", "UpdateProblem", "REPO", "BRANCH"]
