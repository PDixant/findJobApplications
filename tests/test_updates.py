"""The Update button: a clone pulls from GitHub, a downloaded folder copies GitHub's files over its own."""
import io
import json
import subprocess as sp
import zipfile

import pytest

from jobwatcher import updates
from jobwatcher.config import find_git


def _git(folder, *args):
    return sp.run(["git", "-C", str(folder), *args], check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def github(tmp_path, monkeypatch):
    """A bare repository standing in for GitHub, the user's clone of it, and the maintainer's clone."""
    if not find_git():
        pytest.skip("git is not installed")
    remote, mine, maintainer = tmp_path / "github.git", tmp_path / "mine", tmp_path / "maintainer"
    sp.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
    sp.run(["git", "clone", "-q", str(remote), str(maintainer)], check=True, capture_output=True)
    for who in (maintainer,):
        _git(who, "config", "user.email", "m@example.com")
        _git(who, "config", "user.name", "m")
    (maintainer / "jobwatcher").mkdir()
    (maintainer / "jobwatcher" / "app.py").write_text("v1\n")
    (maintainer / "config.toml").write_text("[jobs]\nmax_age_hours = 24\n")
    (maintainer / "pyproject.toml").write_text("deps = 1\n")
    _git(maintainer, "add", ".")
    _git(maintainer, "commit", "-qm", "First version")
    _git(maintainer, "push", "-q", "origin", "HEAD:main")
    sp.run(["git", "clone", "-q", str(remote), str(mine)], check=True, capture_output=True)
    monkeypatch.setattr(updates, "ROOT", mine)
    monkeypatch.setattr(updates, "make_my_copies", lambda: [])
    return mine, maintainer


def test_a_clone_sees_and_installs_new_versions(github):
    mine, maintainer = github
    assert updates.kind() == "git"
    u = updates.check("0.6.0", force=True)
    assert u["newer"] is False and u["behind"] == 0 and u["problem"] is None

    (maintainer / "jobwatcher" / "app.py").write_text("v2\n")
    (maintainer / "pyproject.toml").write_text("deps = 2\n")
    _git(maintainer, "commit", "-qam", "Years of experience filter")
    _git(maintainer, "push", "-q", "origin", "HEAD:main")
    assert updates.check("0.6.0")["newer"] is False  # checked a moment ago: GitHub isn't asked again
    u = updates.check("0.6.0", force=True)
    assert u["newer"] and u["behind"] == 1 and u["can_update"]
    assert u["changes"][0]["message"] == "Years of experience filter"

    (mine / "config.toml").write_text("[jobs]\nmax_age_hours = 6\n")  # an old copy that edited the shared file
    result = updates.apply()
    assert (mine / "jobwatcher" / "app.py").read_text() == "v2\n" and result["packages"] is True
    assert (mine / "config.toml").read_text() == "[jobs]\nmax_age_hours = 24\n"  # back to the shared one
    assert updates.packages_flag().exists()
    updates.packages_flag().unlink()
    assert updates.check("0.6.0")["newer"] is False  # the update cleared the remembered check


def test_a_clone_with_your_own_code_changes_is_left_alone(github):
    mine, maintainer = github
    (maintainer / "jobwatcher" / "app.py").write_text("v2\n")
    _git(maintainer, "commit", "-qam", "New")
    _git(maintainer, "push", "-q", "origin", "HEAD:main")
    (mine / "jobwatcher" / "app.py").write_text("my experiment\n")
    with pytest.raises(updates.UpdateProblem, match="jobwatcher/app.py"):
        updates.apply()
    assert (mine / "jobwatcher" / "app.py").read_text() == "my experiment\n"


class Reply:
    def __init__(self, data=None, content=b""):
        self.status_code, self._data, self.content, self.url = 200, data, content, "https://github.com/x"

    def json(self):
        return self._data

    def raise_for_status(self):
        pass

    def iter_content(self, n):
        for i in range(0, len(self.content), n):
            yield self.content[i:i + n]

    def close(self):
        pass


def fake_github(monkeypatch, answers):
    """requests.get answering from {piece of the address: JSON data or bytes}."""
    def get(url, **kw):
        for piece, answer in answers.items():
            if piece in url:
                return Reply(content=answer) if isinstance(answer, bytes) else Reply(answer)
        return Reply({"ahead_by": 0, "commits": []})
    monkeypatch.setattr(updates.requests, "get", get)


def github_zip(sha, files, comment=True, top=None):
    """A ZIP like GitHub's: one top folder named after the commit, and the commit as the comment."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, text in files.items():
            z.writestr(f"{top or 'findJobApplications-' + sha}/{name}", text)
        if comment:
            z.comment = sha.encode()
    return buf.getvalue()


def test_a_downloaded_folder_updates_from_github(tmp_path, monkeypatch):
    folder = tmp_path / "Pathfinder"
    (folder / "jobwatcher").mkdir(parents=True)
    (folder / "jobwatcher" / "app.py").write_text("v1\n")
    (folder / "my_settings.toml").write_text("mine\n")
    monkeypatch.setattr(updates, "ROOT", folder)
    monkeypatch.setattr(updates, "make_my_copies", lambda: [])
    sha = "abc1234def5678" + "0" * 26
    buf = github_zip(sha, {"jobwatcher/app.py": "v2\n", "jobwatcher/new_module.py": "new\n",
                           "pyproject.toml": "deps = 2\n", ".git/config": "never copied\n"})
    commit = {"sha": sha, "commit": {"message": "Update button\n\nDetails", "committer": {"date": "2099-01-01T00:00:00Z"}}}
    fake_github(monkeypatch, {"codeload": buf, "/commits/main": commit})

    assert updates.kind() == "download"
    u = updates.check("0.6.0", force=True)
    assert u["newer"] and u["changes"][0]["message"] == "Update button"  # files older than GitHub's latest
    result = updates.apply()
    assert result["to"] == "abc1234" and result["packages"] is True
    assert (folder / "jobwatcher" / "app.py").read_text() == "v2\n"
    assert (folder / "jobwatcher" / "new_module.py").exists() and not (folder / ".git").exists()
    assert (folder / "my_settings.toml").read_text() == "mine\n"
    assert json.loads((folder / ".jobwatcher-version").read_text())["commit"] == sha
    assert updates.check("0.6.0", force=True)["newer"] is False
    updates.packages_flag().unlink(missing_ok=True)


def test_packages_are_installed_on_the_next_start(monkeypatch):
    ran = []
    monkeypatch.setattr(updates.subprocess, "run", lambda cmd, **kw: ran.append(cmd) or sp.CompletedProcess(cmd, 0))
    monkeypatch.setattr(updates.sys, "prefix", "/venv")
    monkeypatch.setattr(updates.sys, "base_prefix", "/usr")
    assert updates.install_packages_if_needed() is False  # nothing to do
    updates.packages_flag().parent.mkdir(parents=True, exist_ok=True)
    updates.packages_flag().write_text("x")
    assert updates.install_packages_if_needed() is True
    assert ran and ran[0][1:4] == ["-m", "pip", "install"] and not updates.packages_flag().exists()


def _download_folder(tmp_path, monkeypatch):
    folder = tmp_path / "Pathfinder"
    (folder / "jobwatcher").mkdir(parents=True)
    (folder / "jobwatcher" / "app.py").write_text("v1\n")
    (folder / "jobwatcher" / "boards.py").write_text("boards v1\n")
    monkeypatch.setattr(updates, "ROOT", folder)
    monkeypatch.setattr(updates, "make_my_copies", lambda: [])
    return folder


def test_a_download_that_isnt_the_named_commit_changes_nothing(tmp_path, monkeypatch):
    folder = _download_folder(tmp_path, monkeypatch)
    sha, other = "a" * 40, "b" * 40
    fake_github(monkeypatch, {"codeload": github_zip(other, {"jobwatcher/app.py": "evil\n"}),
                              "/commits/main": {"sha": sha, "commit": {}}})
    with pytest.raises(updates.UpdateProblem, match="wasn't the version GitHub named"):
        updates.apply()
    assert (folder / "jobwatcher" / "app.py").read_text() == "v1\n"


def test_a_download_never_writes_outside_the_folder(tmp_path, monkeypatch):
    folder = _download_folder(tmp_path, monkeypatch)
    sha = "c" * 40
    fake_github(monkeypatch, {"codeload": github_zip(sha, {"../outside.txt": "x", "jobwatcher/app.py": "v2\n"}),
                              "/commits/main": {"sha": sha, "commit": {}}})
    updates.apply()
    assert not (tmp_path / "outside.txt").exists() and (folder / "jobwatcher" / "app.py").read_text() == "v2\n"


def test_a_failed_update_puts_every_file_back(tmp_path, monkeypatch):
    folder = _download_folder(tmp_path, monkeypatch)
    sha = "d" * 40
    fake_github(monkeypatch, {"codeload": github_zip(sha, {"jobwatcher/app.py": "v2\n", "jobwatcher/boards.py": "boards v2\n",
                                                           "jobwatcher/new.py": "new\n"}),
                              "/commits/main": {"sha": sha, "commit": {}}})
    real, swapped = updates.replace_file, []

    def flaky(part, target):
        if target.name == "new.py":
            raise PermissionError(13, "in use", str(target))
        real(part, target)
        swapped.append(target.name)
    monkeypatch.setattr(updates, "replace_file", flaky)
    with pytest.raises(updates.UpdateProblem, match="put back as it was"):
        updates.apply()
    assert swapped  # some files had already changed before the failure
    assert (folder / "jobwatcher" / "app.py").read_text() == "v1\n"
    assert (folder / "jobwatcher" / "boards.py").read_text() == "boards v1\n"
    assert not (folder / "jobwatcher" / "new.py").exists()
    assert not list(folder.rglob("*.updating")) and not (folder / ".jobwatcher-version").exists()


def _installed(monkeypatch, tmp_path, setup=b"MZ installer", sums=None, host="https://github.com"):
    exe = tmp_path / "app" / "Pathfinder.exe"
    exe.parent.mkdir()
    (exe.parent / "unins000.exe").write_bytes(b"")
    monkeypatch.setattr(updates.sys, "frozen", True, raising=False)
    monkeypatch.setattr(updates.sys, "executable", str(exe))
    monkeypatch.setattr(updates.sys, "platform", "win32")
    base = f"{host}/{updates.REPO}/releases/download/v9.0.0"
    sums = sums if sums is not None else f"{updates.hashlib.sha256(setup).hexdigest()}  PathfinderSetup.exe\n".encode()
    release = {"tag_name": "v9.0.0", "html_url": "https://github.com/x/releases/v9.0.0", "assets": [
        {"name": "PathfinderSetup.exe", "browser_download_url": f"{base}/PathfinderSetup.exe"},
        {"name": "SHA256SUMS.txt", "browser_download_url": f"{base}/SHA256SUMS.txt"}]}
    fake_github(monkeypatch, {"releases/latest": release, "PathfinderSetup.exe": setup, "SHA256SUMS.txt": sums})
    ran = []
    monkeypatch.setattr(updates.subprocess, "Popen", lambda cmd, **kw: ran.append(cmd))
    return ran


def test_the_installed_app_runs_only_a_checked_installer(tmp_path, monkeypatch):
    ran = _installed(monkeypatch, tmp_path)
    assert updates.kind() == "installed"
    u = updates.check("1.0.0", force=True)
    assert u["newer"] and u["can_update"] and u["installed"]
    result = updates.apply()
    assert result["installer"] and result["to"] == "v9.0.0"
    assert ran and ran[0][0].endswith("PathfinderSetup.exe") and "/SILENT" in ran[0]
    updates.cache_path().unlink(missing_ok=True)


def test_an_installer_that_doesnt_match_its_checksum_is_never_run(tmp_path, monkeypatch):
    ran = _installed(monkeypatch, tmp_path, sums=f"{'0' * 64}  PathfinderSetup.exe\n".encode())
    with pytest.raises(updates.UpdateProblem, match="doesn't match"):
        updates.apply()
    assert not ran


def test_an_installer_from_anywhere_else_is_never_downloaded(tmp_path, monkeypatch):
    ran = _installed(monkeypatch, tmp_path, host="https://evil.example")
    with pytest.raises(updates.UpdateProblem, match="own release page"):
        updates.apply()
    assert not ran


def test_the_portable_app_points_to_the_release_page(tmp_path, monkeypatch):
    _installed(monkeypatch, tmp_path)
    (tmp_path / "app" / "unins000.exe").unlink()
    assert updates.kind() == "portable"
    assert updates.check("1.0.0", force=True)["can_update"] is False
    with pytest.raises(updates.UpdateProblem, match="portable ZIP"):
        updates.apply()
    updates.cache_path().unlink(missing_ok=True)


def test_the_installed_app_lists_every_change_since_its_version(tmp_path, monkeypatch):
    _installed(monkeypatch, tmp_path)
    commits = [{"sha": f"{i:040x}", "commit": {"message": f"Change {i}\n\nmore", "committer": {"date": f"2026-12-0{i}T00:00:00Z"}}}
               for i in (1, 2, 3)]
    answers = {"compare/v1.0.0...v9.0.0": {"ahead_by": 3, "commits": commits}}
    real_get = updates.requests.get
    monkeypatch.setattr(updates.requests, "get", lambda url, **kw: Reply(answers[next(k for k in answers if k in url)])
                        if any(k in url for k in answers) else real_get(url, **kw))
    u = updates.check("1.0.0", force=True)
    assert u["behind"] == 3 and [c["message"] for c in u["changes"]] == ["Change 3", "Change 2", "Change 1"]
    updates.cache_path().unlink(missing_ok=True)
