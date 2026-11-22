"""Every test runs against a throwaway Job Watcher folder, never your real one.
JOBWATCHER_HOME has to be set before jobwatcher is imported, so it happens here."""
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOME = Path(tempfile.mkdtemp(prefix="jobwatcher-test-"))
os.environ["JOBWATCHER_HOME"] = str(HOME)
for key in ("SERPER_API_KEY", "BRAVE_API_KEY", "SERPAPI_API_KEY", "HUNTER_API_KEY",
            "GMAIL_ADDRESS", "GMAIL_APP_PASSWORD", "ANTHROPIC_API_KEY"):
    os.environ.pop(key, None)
sys.path.insert(0, str(ROOT))

# The shipped settings, made fast for tests: no pause between requests to the local test sites.
config = (ROOT / "config.toml").read_text(encoding="utf-8")
config = config.replace("# website_seconds_between_requests = 1.0", "website_seconds_between_requests = 0.0")
(HOME / "config.toml").write_text(config, encoding="utf-8")
for name in ("about_me.example.md",):
    shutil.copy(ROOT / name, HOME / name)
