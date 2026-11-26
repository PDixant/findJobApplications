"""Keeping Pathfinder up to date: the app tells you when there's a newer version on GitHub, and its
Update button installs it and restarts.

How a copy updates depends on how it was set up:
  git        a clone of the repository: every new commit on GitHub is a new version, and updating is
             a fast-forward `git pull`. Your settings and data are never in git, so it can't clash.
  download   a folder from GitHub's "Download ZIP": the latest commit on GitHub is compared with the
             one this folder has, and updating downloads that commit and copies its files over Job
             Watcher's own. Your files (settings, company list, data, keys) are never in a download.
  installed  the Windows installer: new releases on GitHub. Updating downloads the new release's
             PathfinderSetup.exe, checks its SHA-256 against the release's SHA256SUMS.txt, and runs it.
  portable   the portable ZIP of the Windows app: new releases on GitHub; you download the new ZIP.
When an update changes the packages Pathfinder needs, they're installed as the app starts again.

What's downloaded is checked before anything changes: only from this project's own GitHub addresses,
over HTTPS, size-capped, and it must be exactly the commit (or the installer) GitHub named. A
downloaded folder's files are swapped in all at once, and put back as they were if anything fails.
"""
from __future__ import annotations

import io
import subprocess
import tempfile
import zipfile

from .base import *  # noqa: F401,F403
from .config import find_git, make_my_copies

OFFICIAL_REPO = "PDixant/findJobApplications"
REPO = os.environ.get("JOBWATCHER_UPDATE_REPO", OFFICIAL_REPO)  # only ever changed for testing a fork
if REPO != OFFICIAL_REPO:
    log.warning("Updates come from %s instead of %s (JOBWATCHER_UPDATE_REPO is set).", REPO, OFFICIAL_REPO)
BRANCH = os.environ.get("JOBWATCHER_UPDATE_BRANCH", "main")
ROOT = SOURCE_DIR  # the Pathfinder folder being updated
CHECK_EVERY = dt.timedelta(hours=3)
PACKAGE_FILES = ("pyproject.toml", "requirements.txt", "requirements-app.txt", "requirements-extras.txt")
SHARED_FILES = ("config.toml", "companies.csv")  # yours are copies of these, so they can be put back
NEVER_FROM_DOWNLOAD = {".git", ".venv", "node_modules", ".app_window", "outreach", "resumes", "resume"}
NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0
SETUP_NAME, SUMS_NAME = "PathfinderSetup.exe", "SHA256SUMS.txt"
MAX_ZIP_BYTES, MAX_SETUP_BYTES = 80_000_000, 300_000_000


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
        # The installer leaves its uninstaller beside the app; the portable ZIP has none.
        return "installed" if (Path(sys.executable).parent / "unins000.exe").exists() else "portable"
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
    """git in the Pathfinder folder, never asking for a password or opening a console window."""
    exe = find_git()
    if not exe:
        raise UpdateProblem("Pathfinder can't find git. Install Git from git-scm.com, or update this folder "
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
                "problem": "Couldn't reach GitHub to check for updates. Pathfinder tries again later."}
    upstream = _out(git("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")) or f"origin/{BRANCH}"
    behind = int(_out(git("rev-list", "--count", f"HEAD..{upstream}")) or 0)
    ahead = int(_out(git("rev-list", "--count", f"{upstream}..HEAD")) or 0)
    log_ = _out(git("log", "--format=%H%x1f%cI%x1f%s", "-n", "30", f"HEAD..{upstream}"))
    changes = [_change(*line.split("\x1f", 2)) for line in log_.splitlines() if line.count("\x1f") == 2]
    latest = _out(git("rev-parse", upstream))
    problem = None
    if behind and ahead:
        problem = ("This folder has commits that aren't on GitHub, so it can't update by itself. "
                   "Run git pull in the Pathfinder folder.")
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
                "problem": "Couldn't reach GitHub to check for updates. Pathfinder tries again later."}
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


def _release_assets(body):
    """The installer and checksum file of a release, as download addresses."""
    found = {a.get("name"): a.get("browser_download_url") for a in body.get("assets") or []}
    return found.get(SETUP_NAME), found.get(SUMS_NAME)


def _check_release(current, how):
    body = _github(f"repos/{REPO}/releases/latest") or {}
    latest = body.get("tag_name")
    newer = bool(latest) and _version(latest) > _version(current)
    setup, sums = _release_assets(body)
    can = newer and how == "installed" and sys.platform == "win32" and bool(setup and sums)
    changes, behind = [], None
    if newer:  # every change between your version's tag and the new one, newest first
        compare = _github(f"repos/{REPO}/compare/v{current}...{latest}") or {}
        commits = list(reversed(compare.get("commits") or []))[:30]
        changes = [_change(c["sha"], ((c.get("commit") or {}).get("committer") or {}).get("date"),
                           (c.get("commit") or {}).get("message")) for c in commits]
        behind = compare.get("ahead_by") or (len(changes) or None)
        changes = changes or [_change("", body.get("published_at"), body.get("name") or latest)]
    return {"latest": latest, "newer": newer, "url": body.get("html_url"), "can_update": can,
            "behind": behind, "changes": changes}


def check(current, force=False):
    """Is there a newer Pathfinder? Asks GitHub at most every few hours unless forced."""
    data = load_json(cache_path(), {})
    how = kind()
    fresh = (data.get("kind") == how and data.get("checked")
             and dt.datetime.now(dt.timezone.utc) - parse_date(data["checked"]) < CHECK_EVERY)
    if force or not fresh:
        try:
            found = (_check_git() if how == "git" else _check_download() if how == "download"
                     else _check_release(current, how))
        except UpdateProblem as exc:
            found = {"newer": False, "problem": str(exc)}
        data = {"newer": False, "behind": None, "changes": [], "can_update": False, "problem": None, "url": None,
                "commit": None, "date": None, "latest": None, **found, "kind": how,
                "checked": dt.datetime.now(dt.timezone.utc).isoformat()}
        cache_path().parent.mkdir(parents=True, exist_ok=True)
        save_json(cache_path(), data)
    return {**data, "current": current, "installed": how in ("installed", "portable")}


# ----------------------------------------------------------------- updating
def apply():
    """Bring this folder up to date. Returns what changed; the app then restarts to use it (or, for
    the installed app, closes while the checked installer puts the new version in and reopens it)."""
    how = kind()
    if how == "portable":
        raise UpdateProblem("Download the new portable ZIP from the release page and unzip it over this one.")
    if how == "installed":
        return _apply_installed()
    make_my_copies()  # your settings and companies are in your own files before anything changes
    result = _apply_git() if how == "git" else _apply_download()
    if result["packages"]:
        packages_flag().parent.mkdir(parents=True, exist_ok=True)
        packages_flag().write_text("pyproject.toml changed", encoding="utf-8")
    cache_path().unlink(missing_ok=True)
    log.info("Updated Pathfinder from %s to %s.", result.get("from") or "an older version", result["to"])
    return result


def _apply_git():
    if (ROOT / ".git" / "MERGE_HEAD").exists():
        raise UpdateProblem("A git merge is half done in this folder. In the Pathfinder folder run "
                            "git merge --abort, then press Update again.")
    status = git("status", "--porcelain", "--untracked-files=no").stdout or ""
    dirty = [line[3:].strip().strip('"') for line in status.splitlines() if len(line) > 3]
    if any(d in SHARED_FILES for d in dirty):
        # edits typed into the shared files move into yours first (my_settings.toml, my_companies.csv),
        # so the update never throws them away
        from .config import keep_shared_file_edits, make_my_copies
        make_my_copies(ROOT)
        keep_shared_file_edits(ROOT, force=True)
    for name in [d for d in dirty if d in SHARED_FILES]:
        git("checkout", "--", name)  # the shared copy goes back; your edits are in my_settings.toml / my_companies.csv
    others = [d for d in dirty if d not in SHARED_FILES]
    if others:
        listed = ", ".join(others[:5]) + (" and more" if len(others) > 5 else "")
        raise UpdateProblem(f"You've changed {listed} in the Pathfinder folder. Updating would overwrite that, "
                            "so it stopped. Commit or undo those changes (git stash), then press Update again.")
    old = _out(git("rev-parse", "HEAD"))
    pulled = git("pull", "--ff-only", "--quiet", timeout=300)
    if pulled.returncode != 0:
        said = (pulled.stderr or pulled.stdout or "").strip()
        if "fast-forward" in said or "diverg" in said:
            raise UpdateProblem("This folder has commits that aren't on GitHub, so it can't update by itself. "
                                "Run git pull in the Pathfinder folder.")
        raise UpdateProblem("git couldn't download the update: " + (said.splitlines()[-1] if said else "no reason given"))
    new = _out(git("rev-parse", "HEAD"))
    changed = _out(git("diff", "--name-only", old, new)).splitlines()
    return {"from": old[:7], "to": new[:7], "files": len(changed),
            "packages": any(name in PACKAGE_FILES for name in changed)}


def _get(url, limit, timeout=120):
    """Download from GitHub over HTTPS, up to limit bytes."""
    try:
        r = requests.get(url, timeout=timeout, stream=True, headers={"User-Agent": USER_AGENT})
        r.raise_for_status()
        return read_capped(r, limit)
    except TooBig:
        raise UpdateProblem("The download was far bigger than any Pathfinder version, so it was stopped.") from None
    except requests.RequestException as exc:
        raise UpdateProblem(f"The download didn't work ({exc.__class__.__name__}). Try again in a minute.") from None


def _is_commit(archive, sha):
    """GitHub names the commit in the ZIP it makes: as the archive's comment, and in its one top folder."""
    if archive.comment.decode("ascii", "replace").strip() == sha:
        return True
    tops = {Path(m.filename).parts[0] for m in archive.infolist() if Path(m.filename).parts}
    return len(tops) == 1 and next(iter(tops)).endswith("-" + sha)


def _apply_download():
    latest = _github(f"repos/{REPO}/commits/{BRANCH}")
    if not latest:
        raise UpdateProblem("Couldn't reach GitHub. Check the internet connection and try again.")
    sha = str(latest.get("sha") or "")
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise UpdateProblem("GitHub's answer didn't name a version. Try again in a minute.")
    try:
        archive = zipfile.ZipFile(io.BytesIO(_get(f"https://codeload.github.com/{REPO}/zip/{sha}", MAX_ZIP_BYTES)))
    except zipfile.BadZipFile:
        raise UpdateProblem("The download wasn't a complete ZIP. Try again in a minute.") from None
    if not _is_commit(archive, sha):
        raise UpdateProblem("The download wasn't the version GitHub named, so nothing was changed. Try again later.")
    old = load_json(version_file(), {}).get("commit")
    root = ROOT.resolve()
    with tempfile.TemporaryDirectory(prefix="jobwatcher-update-") as tmp:
        tmp = Path(tmp)
        for member in archive.infolist():
            parts = Path(member.filename.replace("\\", "/")).parts[1:]  # inside the top folder GitHub adds
            if (not parts or member.is_dir() or parts[0] in NEVER_FROM_DOWNLOAD or ".." in parts
                    or any(":" in p for p in parts)):
                continue
            staged = tmp.joinpath(*parts)
            staged.parent.mkdir(parents=True, exist_ok=True)
            staged.write_bytes(archive.read(member))
        changes = []
        for staged in sorted(p for p in tmp.rglob("*") if p.is_file()):
            rel = staged.relative_to(tmp)
            target = ROOT / rel
            if root not in target.resolve().parents:
                continue  # never anything outside the Pathfinder folder
            data = staged.read_bytes()
            if not (target.exists() and target.read_bytes() == data):
                changes.append((rel, target, data))
        _swap_in(changes, tmp)
    when = ((latest.get("commit") or {}).get("committer") or {}).get("date")
    save_json(version_file(), {"commit": sha, "date": when})
    packages = any(str(rel).replace("\\", "/") in PACKAGE_FILES for rel, _, _ in changes)
    return {"from": (old or "")[:7] or None, "to": sha[:7], "files": len(changes), "packages": packages}


def _swap_in(changes, scratch):
    """Write every changed file, all or nothing. Each new file is written beside its target first,
    then the old ones are kept aside while the new ones take their place, and put back if anything fails."""
    parts, kept, done = [], scratch / ".kept", []
    try:
        for rel, target, data in changes:
            target.parent.mkdir(parents=True, exist_ok=True)
            part = target.with_name(target.name + ".updating")  # same folder, so each swap is instant
            part.write_bytes(data)
            parts.append(part)
        for (rel, target, _), part in zip(changes, parts):
            if target.exists():
                (kept / rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(target, kept / rel)
            replace_file(part, target)
            done.append((rel, target))
    except OSError as exc:
        for rel, target in reversed(done):
            try:
                if (kept / rel).exists():
                    shutil.copy2(kept / rel, target)
                else:
                    target.unlink(missing_ok=True)
            except OSError:
                log.exception("Couldn't put %s back after a failed update", rel)
        for part in parts:
            part.unlink(missing_ok=True)
        raise UpdateProblem(f"The update couldn't write {getattr(exc, 'filename', None) or 'a file'}, so this "
                            "folder was put back as it was. Close anything using Pathfinder's files and try again.") from None


def _apply_installed():
    """Download the newest release's installer, check it against the release's own checksums, and
    run it. It closes Pathfinder, installs the new version and opens it again."""
    body = _github(f"repos/{REPO}/releases/latest") or {}
    tag = body.get("tag_name")
    setup, sums = _release_assets(body)
    official = f"https://github.com/{REPO}/releases/download/"
    if not (tag and setup and sums):
        raise UpdateProblem("The newest release has no installer yet. Try again later, or download it from the release page.")
    if not (setup.startswith(official) and sums.startswith(official)):
        raise UpdateProblem("The release's files aren't on Pathfinder's own release page, so they weren't downloaded.")
    expected = None
    for line in _get(sums, 200_000, timeout=30).decode("utf-8", "replace").splitlines():
        bits = line.strip().split()
        if len(bits) == 2 and bits[1].lstrip("*") == SETUP_NAME:
            expected = bits[0].lower()
    if not re.fullmatch(r"[0-9a-f]{64}", expected or ""):
        raise UpdateProblem(f"The release's {SUMS_NAME} doesn't list the installer, so it wasn't run.")
    data = _get(setup, MAX_SETUP_BYTES, timeout=600)
    if hashlib.sha256(data).hexdigest() != expected:
        raise UpdateProblem("The downloaded installer doesn't match the release's checksum, so it wasn't run. "
                            "Try again later, or download it from the release page.")
    path = Path(tempfile.mkdtemp(prefix="pathfinder-update-")) / SETUP_NAME
    path.write_bytes(data)
    flags = 0x00000008 | 0x00000200 if sys.platform == "win32" else 0  # detached, its own process group
    subprocess.Popen([str(path), "/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS"],
                     close_fds=True, creationflags=flags)
    log.info("Installing Pathfinder %s with its checked installer (SHA-256 %s).", tag, expected[:12])
    return {"from": None, "to": tag, "files": 1, "packages": False, "installer": True}


def install_packages_if_needed():
    """After an update that changed Pathfinder's packages: install them, as the app starts again
    (before, Windows would refuse to replace files the running app had open)."""
    flag = packages_flag()
    if not flag.exists() or getattr(sys, "frozen", False):
        return False
    flag.unlink(missing_ok=True)
    if sys.prefix == sys.base_prefix:
        log.warning("Pathfinder was updated and needs new packages, but it isn't running from its own "
                    "environment. Double-click Install Pathfinder to install them.")
        return False
    cmd = [sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "-q", "-e",
           f"{ROOT}[app,extras]"]
    env = {**os.environ, "PIP_CACHE_DIR": str(Path(sys.prefix) / "pip-cache")}
    log.info("Installing the packages this version of Pathfinder needs...")
    done = subprocess.run(cmd, capture_output=True, text=True, env=env, creationflags=NO_WINDOW)
    if done.returncode != 0:
        log.warning("Couldn't install the new packages: %s Double-click Install Pathfinder to finish the update.",
                    (done.stderr or done.stdout or "").strip()[-400:])
        return False
    return True


__all__ = ["check", "apply", "install_packages_if_needed", "kind", "UpdateProblem", "REPO", "BRANCH", "OFFICIAL_REPO"]
