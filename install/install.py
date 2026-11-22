"""Sets Job Watcher up to open with one click, and adds a `jobwatcher` command for any terminal.

Run by "Install Job Watcher.bat" (Windows) or install.sh (Mac and Linux). It:
  1. makes a private Python environment in .venv inside the Job Watcher folder, so nothing
     else on the computer is changed,
  2. installs Job Watcher and the app's packages into it,
  3. adds "Job Watcher" to the Start menu and the Desktop (Mac: Applications; Linux: the
     app menu), with its icon,
  4. adds the `jobwatcher` command to your PATH, so it works in any terminal from any folder.

Your settings and data stay where they are. Run it again any time to repair or update.

    python install/install.py              set up (or update)
    python install/install.py --minimal    skip the optional extras (stock metrics, pop-ups)
    python install/install.py --uninstall  remove the shortcuts, the command and .venv
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import venv
from pathlib import Path

WINDOWS = sys.platform == "win32"
MAC = sys.platform == "darwin"
NAME = "Job Watcher"


def configure(root):
    """Paths for the Job Watcher folder at root. (Uninstall runs a copy of this file from a
    temporary folder, because Windows can't delete a Python that is running.)"""
    global ROOT, VENV, SCRIPTS, PYTHON, MANIFEST
    ROOT = Path(root).resolve()
    VENV = ROOT / ".venv"
    SCRIPTS = VENV / ("Scripts" if WINDOWS else "bin")
    PYTHON = SCRIPTS / ("python.exe" if WINDOWS else "python")
    MANIFEST = VENV / "jobwatcher-install.json"


configure(Path(__file__).resolve().parent.parent)


def step(text):
    print(f"\n== {text}", flush=True)


def run(cmd, **kw):
    print("   " + " ".join(f'"{c}"' if " " in str(c) else str(c) for c in cmd), flush=True)
    subprocess.run([str(c) for c in cmd], check=True, **kw)


def powershell(script):
    subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
                   check=True, capture_output=True, text=True)


def ps_quote(value):
    return "'" + str(value).replace("'", "''") + "'"


def windows_folder(name):
    """A special folder such as Desktop, which OneDrive may have moved."""
    out = subprocess.run(["powershell", "-NoProfile", "-Command", f"[Environment]::GetFolderPath('{name}')"],
                         check=True, capture_output=True, text=True).stdout.strip()
    return Path(out)


def edit_user_path(folder, add):
    """Add or remove one folder in your user PATH. Reads and writes the registry value as is, so
    entries such as %USERPROFILE%\\... stay unexpanded, then tells Windows that the environment
    changed (setting and clearing a throwaway variable does that), so new terminals see it."""
    change = ("if (-not ($parts -contains $d)) { $parts = @($parts) + $d }" if add
              else "$parts = $parts | Where-Object { $_ -ne $d }")
    powershell(
        f"$d = {ps_quote(folder)}; "
        "$k = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey('Environment', $true); "
        "$p = [string]$k.GetValue('Path', '', 'DoNotExpandEnvironmentNames'); "
        "$parts = @($p -split ';' | Where-Object { $_ }); "
        f"{change}; "
        "$k.SetValue('Path', ($parts -join ';'), 'ExpandString'); $k.Close(); "
        "[Environment]::SetEnvironmentVariable('JOBWATCHER_PATH_REFRESH', '1', 'User'); "
        "[Environment]::SetEnvironmentVariable('JOBWATCHER_PATH_REFRESH', $null, 'User')")


# ----------------------------------------------------------------- install
def install(minimal=False):
    if sys.version_info < (3, 10):
        sys.exit(f"Job Watcher needs Python 3.10 or newer; this is {sys.version.split()[0]}.")
    made = {"shortcuts": [], "command": None, "path_entry": None}

    step("Making a private Python environment (.venv)")
    if not PYTHON.exists():
        venv.EnvBuilder(with_pip=True, upgrade_deps=False).create(VENV)
    else:
        print("   already there; updating it")

    step("Installing Job Watcher and its packages (a few minutes the first time)")
    extras = "app" if minimal else "app,extras"
    run([PYTHON, "-m", "pip", "install", "--disable-pip-version-check", "-q", "-e", f"{ROOT}[{extras}]"])
    run([PYTHON, "-c", "import jobwatcher.server, jobwatcher.desktop; import webview, tomlkit, keyring"])

    app_exe = SCRIPTS / ("jobwatcher-app.exe" if WINDOWS else "jobwatcher-app")
    cli_exe = SCRIPTS / ("jobwatcher.exe" if WINDOWS else "jobwatcher")
    icon_ico = ROOT / "jobwatcher" / "assets" / "icon.ico"
    icon_png = ROOT / "jobwatcher" / "assets" / "icon.png"

    step("Adding Job Watcher to your menu and Desktop")
    if WINDOWS:
        for folder in (windows_folder("Programs"), windows_folder("Desktop")):
            link = folder / f"{NAME}.lnk"
            powershell(
                "$s = (New-Object -ComObject WScript.Shell).CreateShortcut(" + ps_quote(link) + "); "
                f"$s.TargetPath = {ps_quote(app_exe)}; $s.WorkingDirectory = {ps_quote(ROOT)}; "
                f"$s.IconLocation = {ps_quote(icon_ico)}; "
                "$s.Description = 'Find fresh software jobs and track your applications'; $s.Save()")
            made["shortcuts"].append(str(link))
            print(f"   {link}")
    elif MAC:
        bundle = Path.home() / "Applications" / f"{NAME}.app"
        (bundle / "Contents" / "MacOS").mkdir(parents=True, exist_ok=True)
        (bundle / "Contents" / "Resources").mkdir(parents=True, exist_ok=True)
        launcher = bundle / "Contents" / "MacOS" / "jobwatcher"
        launcher.write_text(f'#!/bin/sh\ncd "{ROOT}"\nexec "{app_exe}"\n')
        launcher.chmod(0o755)
        shutil.copy2(icon_png, bundle / "Contents" / "Resources" / "icon.png")
        (bundle / "Contents" / "Info.plist").write_text(
            '<?xml version="1.0" encoding="UTF-8"?>\n<plist version="1.0"><dict>'
            f"<key>CFBundleName</key><string>{NAME}</string><key>CFBundleExecutable</key><string>jobwatcher</string>"
            "<key>CFBundleIdentifier</key><string>io.github.jobwatcher</string>"
            "<key>CFBundlePackageType</key><string>APPL</string></dict></plist>\n")
        made["shortcuts"].append(str(bundle))
        print(f"   {bundle}")
    else:
        apps = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "applications"
        apps.mkdir(parents=True, exist_ok=True)
        entry = apps / "jobwatcher.desktop"
        entry.write_text(f"[Desktop Entry]\nType=Application\nName={NAME}\nExec=\"{app_exe}\"\n"
                         f"Path={ROOT}\nIcon={icon_png}\nCategories=Office;\nTerminal=false\n")
        made["shortcuts"].append(str(entry))
        print(f"   {entry}")

    step("Adding the jobwatcher command for any terminal")
    if WINDOWS:
        bin_dir = Path(os.environ["LOCALAPPDATA"]) / "JobWatcher" / "bin"
        bin_dir.mkdir(parents=True, exist_ok=True)
        shim = bin_dir / "jobwatcher.cmd"
        shim.write_text(f'@"{cli_exe}" %*\r\n', encoding="utf-8")
        made["command"] = str(shim)
        edit_user_path(bin_dir, add=True)
        made["path_entry"] = str(bin_dir)
        print(f"   {shim}\n   Open a new terminal window for the command to work there.")
    else:
        bin_dir = Path.home() / ".local" / "bin"
        bin_dir.mkdir(parents=True, exist_ok=True)
        link = bin_dir / "jobwatcher"
        if link.is_symlink() or link.exists():
            link.unlink()
        link.symlink_to(cli_exe)
        made["command"] = str(link)
        print(f"   {link}")
        if str(bin_dir) not in os.environ.get("PATH", "").split(os.pathsep):
            print(f"   Add {bin_dir} to your PATH to use it everywhere, for example:\n"
                  f"   echo 'export PATH=\"$HOME/.local/bin:$PATH\"' >> ~/{'.zprofile' if MAC else '.profile'}")

    MANIFEST.write_text(json.dumps(made, indent=2), encoding="utf-8")
    step("Done")
    print(f"Open {NAME} from the {'Start menu or Desktop' if WINDOWS else 'Applications folder' if MAC else 'app menu'}, "
          "or type  jobwatcher  in a terminal.\n"
          "  jobwatcher run       a run from the terminal (same options as job_watcher.py)\n"
          "  jobwatcher folder    where your settings and data are")


# ----------------------------------------------------------------- uninstall
def uninstall():
    made = {}
    if MANIFEST.exists():
        made = json.loads(MANIFEST.read_text(encoding="utf-8"))
    step("Removing the shortcuts and the jobwatcher command")
    for item in made.get("shortcuts", []):
        path = Path(item)
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
        elif path.exists():
            path.unlink()
        print(f"   removed {path}")
    if made.get("command"):
        Path(made["command"]).unlink(missing_ok=True)
        print(f"   removed {made['command']}")
    if WINDOWS and made.get("path_entry"):
        edit_user_path(made["path_entry"], add=False)
    step("Removing .venv")
    shutil.rmtree(VENV, ignore_errors=True)
    print("Done. Your settings, companies and tracker are untouched.")


def main():
    parser = argparse.ArgumentParser(description="Set up Job Watcher to open with one click.")
    parser.add_argument("--uninstall", action="store_true", help="remove the shortcuts, the command and .venv")
    parser.add_argument("--minimal", action="store_true", help="skip stock metrics and desktop pop-ups")
    parser.add_argument("--root", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.root:
        configure(args.root)
    try:
        uninstall() if args.uninstall else install(minimal=args.minimal)
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or "").strip() if isinstance(exc.stderr, str) else ""
        sys.exit(f"\nThis step failed: {exc.cmd[0] if exc.cmd else ''}\n{detail}")


if __name__ == "__main__":
    main()
