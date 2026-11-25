"""Prints this Python's path if Pathfinder can be installed with it, and nothing otherwise.

"Install Pathfinder.bat" asks each Python it finds. Usable means 3.10 or newer and not the
Microsoft Store build: that one keeps files it writes under AppData in a private copy, so the
`pathfinder` command and the Start menu shortcut it makes are invisible to the rest of Windows,
and the app can't open from a click.
"""
import sys


def usable(executable, base_prefix, version):
    store = "WindowsApps" in executable or "WindowsApps" in base_prefix
    return version >= (3, 10) and not store


if __name__ == "__main__":
    if usable(sys.executable, sys.base_prefix, sys.version_info[:2]):
        print(sys.executable)
