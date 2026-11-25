# PyInstaller build of Pathfinder for Windows, for people without Python. From the main folder:
#     pip install -e ".[app]" pyinstaller
#     pyinstaller --noconfirm packaging/pathfinder.spec
# Makes dist/Pathfinder/ with two programs sharing one set of files:
#     Pathfinder.exe   the app, no terminal window (what the shortcuts open)
#     jobwatcher.exe   the same for terminals: jobwatcher run, jobwatcher outreach, ...
# The release workflow wraps that folder in PathfinderSetup.exe (packaging/pathfinder.iss) and a
# portable ZIP. Settings and data live in %APPDATA%\JobWatcher (the app's first name, kept so nothing
# moves), set up with config.toml and companies.csv from this folder the first time it opens.
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(SPECPATH).parent
ICON = str(ROOT / "jobwatcher" / "assets" / ("icon.ico" if sys.platform == "win32" else "icon.png"))

datas = [
    (str(ROOT / "jobwatcher" / "ui_dist"), "jobwatcher/ui_dist"),
    (str(ROOT / "jobwatcher" / "assets"), "jobwatcher/assets"),
]
datas += [(str(ROOT / name), "defaults") for name in ("config.toml", "companies.csv", "about_me.example.md", ".env.example",
                                                             "resume_rules.example.md")]

hidden = ["outreach", "job_watcher", "tomlkit"]
hidden += collect_submodules("uvicorn") + collect_submodules("keyring.backends") + collect_submodules("jobwatcher")
hidden += collect_submodules("pystray")

a = Analysis(
    [str(ROOT / "packaging" / "launch.py")],
    pathex=[str(ROOT)],
    datas=datas,
    hiddenimports=hidden,
    # Stock metrics (yfinance and pandas) would triple the size; company signals still use the other facts.
    excludes=["tkinter", "yfinance", "pandas", "numpy", "matplotlib", "pytest"],
)
pyz = PYZ(a.pure)
version = str(ROOT / "packaging" / "version_info.txt") if sys.platform == "win32" and (ROOT / "packaging" / "version_info.txt").exists() else None
app = EXE(pyz, a.scripts, [], exclude_binaries=True, name="Pathfinder", console=False, icon=ICON, version=version)
cli = EXE(pyz, a.scripts, [], exclude_binaries=True, name="jobwatcher", console=True, icon=ICON, version=version)
COLLECT(app, cli, a.binaries, a.datas, name="Pathfinder")
