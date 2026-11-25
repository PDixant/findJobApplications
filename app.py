"""Pathfinder desktop app. Double-click "Install Pathfinder" once to get a Start menu and
Desktop shortcut and a `jobwatcher` command that works from any folder. Or run:

    python app.py              open the app in its own window
    python app.py --browser    open it in your web browser instead
    python app.py --dev        for changing the interface (see docs/GUIDE.md, For developers)

Everything `jobwatcher` does is in jobwatcher/desktop.py.
"""
from jobwatcher.desktop import main

if __name__ == "__main__":
    main()
