"""Append-only shadow log writer.

One JSON line per judged call at ~/.local/state/airlock/shadow.jsonl.
Directory mode 700, file mode 600. Uses flock so concurrent hook processes
(one per tool call) never interleave a partial line.
"""
import fcntl
import json
import os
from pathlib import Path

from . import paths

LOG_DIR = paths.state_dir()
LOG_FILE = LOG_DIR / "shadow.jsonl"


def append(entry):
    """Append one entry as a JSON line. Never raises -- a logging failure must
    never surface anywhere a caller could turn into session-visible output."""
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(LOG_DIR, 0o700)
        except Exception:
            pass

        line = json.dumps(entry, default=str, ensure_ascii=False) + "\n"
        fd = os.open(str(LOG_FILE), os.O_CREAT | os.O_WRONLY | os.O_APPEND, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            try:
                os.write(fd, line.encode("utf-8"))
            finally:
                try:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                except Exception:
                    pass
        finally:
            os.close(fd)
        try:
            os.chmod(str(LOG_FILE), 0o600)
        except Exception:
            pass
    except Exception:
        return
