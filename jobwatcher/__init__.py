"""Job Watcher core package. job_watcher.py, outreach.py and the desktop app all use it."""
__version__ = "0.6.0"
from .base import *  # noqa: F401,F403
from .config import *  # noqa: F401,F403
from .boards import *  # noqa: F401,F403
from .website import *  # noqa: F401,F403
from .companies import *  # noqa: F401,F403
from .store import *  # noqa: F401,F403
from .filters import *  # noqa: F401,F403
from .discovery import *  # noqa: F401,F403
from .tracker import *  # noqa: F401,F403
from .tracked import *  # noqa: F401,F403
from .runner import *  # noqa: F401,F403
from .cli import main  # noqa: F401

__all__ = [n for n in list(globals()) if not n.startswith("__")]
