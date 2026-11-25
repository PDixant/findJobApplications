"""Sets Pathfinder up to open with one click, and adds a `pathfinder` command for any terminal.

Run by "Install Pathfinder.bat" (Windows) or install.sh (Mac and Linux). It:
  1. makes a private Python environment in .venv inside the Pathfinder folder, so nothing
     else on the computer is changed,
  2. installs Pathfinder and the app's packages into it,
  3. adds "Pathfinder" to the Start menu and the Desktop (Mac: Applications; Linux: the
     app menu), with its icon, and a "Pathfinder" shortcut at the top of this folder too,
  4. adds the `pathfinder` command (and `jobwatcher`, its first name) to your PATH, so it works in any
     terminal from any folder.

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
NAME = "Pathfinder"
OLD_NAMES = ("Job Watcher",)  # what it was called; shortcuts made under it are replaced
COMMANDS = ("pathfinder", "jobwatcher")  # both work in any terminal


def configure(root):
    """Paths for the Pathfinder folder at root. (Uninstall runs a copy of this file from a
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


PIP_TROUBLE = ("Permission denied", "WinError 5", "WinError 32", "Access is denied",
               "being used by another process", "OSError")


def pip_install(args):
    """pip install into .venv. pip keeps its downloads in a cache of its own inside .venv, not the
    one shared by every Python on the computer: that shared cache can hold files another program
    has locked (antivirus scanning them, an earlier install run as administrator), which stops
    pip with "Permission denied". If pip still hits a locked file, it tries once more without any
    cache, which only means downloading again."""
    cmd = [str(PYTHON), "-m", "pip", "install", "--disable-pip-version-check", "-q", *map(str, args)]
    env = {**os.environ, "PIP_CACHE_DIR": str(VENV / "pip-cache")}
    print("   " + " ".join(f'"{c}"' if " " in c else c for c in cmd), flush=True)
    first = subprocess.run(cmd, env=env, capture_output=True, text=True)
    if first.returncode == 0:
        return
    said = (first.stdout or "") + (first.stderr or "")
    if not any(t in said for t in PIP_TROUBLE):
        print(said.strip())
        raise subprocess.CalledProcessError(first.returncode, cmd, output=first.stdout, stderr=first.stderr)
    print("   A file pip needed was locked or off limits. Trying again without pip's cache...", flush=True)
    second = subprocess.run([*cmd[:4], "--no-cache-dir", *cmd[4:]], env=env, capture_output=True, text=True)
    if second.returncode == 0:
        return
    print(((second.stdout or "") + (second.stderr or "")).strip())
    sys.exit("\nThe packages couldn't be installed because Windows refused pip access to a file.\n"
             "This is usually antivirus software scanning the download, or a folder left behind by an\n"
             "install that was run as administrator. Try these, then double-click Install Pathfinder again:\n"
             "  1. Restart the computer (it releases locked files).\n"
             "  2. Delete the folder  %LOCALAPPDATA%\\pip\\cache  (paste that into File Explorer's address bar).\n"
             "  3. Don't run Install Pathfinder as administrator; a normal double-click is right.\n"
             "Still stuck? Send the messages above to whoever shared Pathfinder with you.")


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
def windows_shortcut_folders():
    """Where the Windows shortcuts go: the Start menu, the Desktop, and the top of the Pathfinder
    folder itself, so nobody has to dig into .venv\\Scripts for the app."""
    return [windows_folder("Programs"), windows_folder("Desktop"), ROOT]


def store_python(executable=None, base_prefix=None):
    """The Microsoft Store build of Python. Files it writes under AppData go to a private copy that
    only it can see, so the pathfinder command and the Start menu shortcut would be invisible to the
    rest of Windows, and the app can fail to open from a click."""
    executable = sys.executable if executable is None else executable
    base_prefix = sys.base_prefix if base_prefix is None else base_prefix
    return "WindowsApps" in executable or "WindowsApps" in base_prefix


def venv_base(cfg_text):
    """The Python a .venv was made from, from its pyvenv.cfg."""
    for line in cfg_text.splitlines():
        key, _, value = line.partition("=")
        if key.strip().lower() == "home":
            return value.strip()
    return ""


def venv_needs_remaking(cfg_text, base_prefix):
    """True when .venv was made by another Python (the Store one, or one since replaced)."""
    home = venv_base(cfg_text)
    if not home or "WindowsApps" in home:
        return True
    return os.path.normcase(os.path.normpath(home)) != os.path.normcase(os.path.normpath(base_prefix))


def install(minimal=False):
    if sys.version_info < (3, 10):
        sys.exit(f"Pathfinder needs Python 3.10 or newer; this is {sys.version.split()[0]}.")
    if WINDOWS and store_python():
        sys.exit("This is the Microsoft Store version of Python, which Pathfinder can't use: it keeps the\n"
                 "files Pathfinder sets up in a private copy the rest of Windows can't see.\n"
                 "Double-click \"Install Pathfinder\" instead; it installs the python.org version if needed.")
    made = {"shortcuts": [], "command": None, "commands": [], "path_entry": None}
    try:  # shortcuts from an earlier install, under the old name, go once the new ones are made
        before = json.loads(MANIFEST.read_text(encoding="utf-8")).get("shortcuts", []) if MANIFEST.exists() else []
    except ValueError:
        before = []

    step("Making a private Python environment (.venv)")
    cfg = VENV / "pyvenv.cfg"
    if PYTHON.exists() and cfg.exists() and venv_needs_remaking(cfg.read_text(encoding="utf-8"), sys.base_prefix):
        print(f"   it was made with another Python ({venv_base(cfg.read_text(encoding='utf-8')) or 'unknown'}); "
              "making it again with this one")
        try:
            shutil.rmtree(VENV)
        except OSError as exc:
            sys.exit(f"Couldn't replace .venv ({exc}).\nClose Pathfinder and any terminal using it, "
                     "then run Install Pathfinder again.")
    if not PYTHON.exists():
        venv.EnvBuilder(with_pip=True, upgrade_deps=False).create(VENV)
    else:
        print("   already there; updating it")

    step("Installing Pathfinder and its packages (a few minutes the first time)")
    extras = "app" if minimal else "app,extras"
    pip_install(["-e", f"{ROOT}[{extras}]"])
    run([PYTHON, "-c", "import jobwatcher.server, jobwatcher.desktop; import webview, tomlkit, keyring"])

    app_exe = SCRIPTS / ("jobwatcher-app.exe" if WINDOWS else "jobwatcher-app")
    cli_exe = SCRIPTS / ("jobwatcher.exe" if WINDOWS else "jobwatcher")
    icon_ico = ROOT / "jobwatcher" / "assets" / "icon.ico"
    icon_png = ROOT / "jobwatcher" / "assets" / "icon.png"

    step("Adding Pathfinder to your menu, your Desktop and this folder")
    if WINDOWS:
        for folder in windows_shortcut_folders():
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
        here = ROOT / f"{NAME}.app"  # the same app, at the top of this folder
        if here.is_symlink() or here.exists():
            remove_shortcut(here)
        here.symlink_to(bundle, target_is_directory=True)
        made["shortcuts"].append(str(here))
        print(f"   {here}")
    else:
        apps = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "applications"
        apps.mkdir(parents=True, exist_ok=True)
        entry = apps / "jobwatcher.desktop"
        entry.write_text(f"[Desktop Entry]\nType=Application\nName={NAME}\nExec=\"{app_exe}\"\n"
                         f"Path={ROOT}\nIcon={icon_png}\nCategories=Office;\nTerminal=false\n")
        made["shortcuts"].append(str(entry))
        print(f"   {entry}")
        here = ROOT / f"{NAME}.desktop"  # the same launcher, at the top of this folder
        shutil.copy2(entry, here)
        here.chmod(0o755)
        made["shortcuts"].append(str(here))
        print(f"   {here}")

    for old in before:
        if old not in made["shortcuts"] and Path(old).stem in OLD_NAMES:
            remove_shortcut(old)
            print(f"   removed the old {old}")

    step("Adding the pathfinder command (and jobwatcher) for any terminal")
    if WINDOWS:
        bin_dir = Path(os.environ["LOCALAPPDATA"]) / "JobWatcher" / "bin"
        bin_dir.mkdir(parents=True, exist_ok=True)
        for name in COMMANDS:
            shim = bin_dir / f"{name}.cmd"
            shim.write_text(f'@"{cli_exe}" %*\r\n', encoding="utf-8")
            made["commands"].append(str(shim))
            print(f"   {shim}")
        edit_user_path(bin_dir, add=True)
        made["path_entry"] = str(bin_dir)
        print("   Open a new terminal window for the commands to work there.")
    else:
        bin_dir = Path.home() / ".local" / "bin"
        bin_dir.mkdir(parents=True, exist_ok=True)
        for name in COMMANDS:
            link = bin_dir / name
            if link.is_symlink() or link.exists():
                link.unlink()
            link.symlink_to(cli_exe)
            made["commands"].append(str(link))
            print(f"   {link}")
        if str(bin_dir) not in os.environ.get("PATH", "").split(os.pathsep):
            print(f"   Add {bin_dir} to your PATH to use it everywhere, for example:\n"
                  f"   echo 'export PATH=\"$HOME/.local/bin:$PATH\"' >> ~/{'.zprofile' if MAC else '.profile'}")
    made["command"] = made["commands"][-1]  # older copies of this file read only this one

    MANIFEST.write_text(json.dumps(made, indent=2), encoding="utf-8")
    step("Done")
    print(f"Open {NAME} from the {'Start menu or Desktop' if WINDOWS else 'Applications folder' if MAC else 'app menu'}, "
          "or type  pathfinder  in a terminal.\n"
          "  pathfinder run       a run from the terminal (same options as job_watcher.py)\n"
          "  pathfinder folder    where your settings and data are\n"
          "jobwatcher works too, the same as pathfinder.")


# ----------------------------------------------------------------- uninstall
def remove_shortcut(path):
    """A .lnk, a .desktop file, an app bundle, or a link to one (removing a link leaves its target)."""
    path = Path(path)
    if path.is_symlink():
        try:
            path.unlink()
        except OSError:  # Windows removes a link to a folder like a folder
            os.rmdir(path)
    elif path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path, ignore_errors=True)


def uninstall():
    made = {}
    if MANIFEST.exists():
        made = json.loads(MANIFEST.read_text(encoding="utf-8"))
    step("Removing the shortcuts and the pathfinder and jobwatcher commands")
    for item in made.get("shortcuts", []):
        path = Path(item)
        remove_shortcut(path)
        print(f"   removed {path}")
    for command in dict.fromkeys(made.get("commands", []) + ([made["command"]] if made.get("command") else [])):
        Path(command).unlink(missing_ok=True)
        print(f"   removed {command}")
    if WINDOWS and made.get("path_entry"):
        edit_user_path(made["path_entry"], add=False)
    step("Removing .venv")
    shutil.rmtree(VENV, ignore_errors=True)
    print("Done. Your settings, companies and tracker are untouched.")


def main():
    parser = argparse.ArgumentParser(description="Set up Pathfinder to open with one click.")
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
