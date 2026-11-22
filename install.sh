#!/bin/sh
# Mac and Linux: sets up Job Watcher with an app shortcut and a `jobwatcher` command.
# Run:  sh install.sh        (or  sh install.sh --uninstall)
cd "$(dirname "$0")" || exit 1
for py in python3.13 python3.12 python3.11 python3.10 python3; do
  if command -v "$py" >/dev/null 2>&1 && "$py" -c 'import sys; sys.exit(sys.version_info < (3, 10))'; then
    exec "$py" install/install.py "$@"
  fi
done
echo "Python 3.10 or newer is needed. Install it from https://www.python.org/downloads/ and run this again."
exit 1
