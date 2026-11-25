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


def test_a_downloaded_folder_updates_from_github(tmp_path, monkeypatch):
    folder = tmp_path / "Job Watcher"
    (folder / "jobwatcher").mkdir(parents=True)
    (folder / "jobwatcher" / "app.py").write_text("v1\n")
    (folder / "my_settings.toml").write_text("mine\n")
    monkeypatch.setattr(updates, "ROOT", folder)
    monkeypatch.setattr(updates, "make_my_copies", lambda: [])
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("findJobApplications-abc/jobwatcher/app.py", "v2\n")
        z.writestr("findJobApplications-abc/jobwatcher/new_module.py", "new\n")
        z.writestr("findJobApplications-abc/pyproject.toml", "deps = 2\n")
        z.writestr("findJobApplications-abc/.git/config", "never copied\n")
    commit = {"sha": "abc1234def5678", "commit": {"message": "Update button\n\nDetails",
                                                  "committer": {"date": "2099-01-01T00:00:00Z"}}}

    class Reply:
        def __init__(self, data=None, content=b""):
            self.status_code, self._data, self.content = 200, data, content

        def json(self):
            return self._data

        def raise_for_status(self):
            pass

    def get(url, **kw):
        if "codeload" in url:
            return Reply(content=buf.getvalue())
        if url.endswith("/commits/main"):
            return Reply(commit)
        return Reply({"ahead_by": 0, "commits": []})
    monkeypatch.setattr(updates.requests, "get", get)

    assert updates.kind() == "download"
    u = updates.check("0.6.0", force=True)
    assert u["newer"] and u["changes"][0]["message"] == "Update button"  # files older than GitHub's latest
    result = updates.apply()
    assert result["to"] == "abc1234" and result["packages"] is True
    assert (folder / "jobwatcher" / "app.py").read_text() == "v2\n"
    assert (folder / "jobwatcher" / "new_module.py").exists() and not (folder / ".git").exists()
    assert (folder / "my_settings.toml").read_text() == "mine\n"
    assert json.loads((folder / ".jobwatcher-version").read_text())["commit"] == "abc1234def5678"
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
