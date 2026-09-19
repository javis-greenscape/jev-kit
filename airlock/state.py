"""Small persistent state for enforce mode: loop protection.

If the same session gets denied the same normalised command (or the same
Agent description) again within LOOP_WINDOW_S, the repeat is allowed rather
than denied again -- a wrong deny can wedge a session into repeating the same
blocked call forever otherwise. State lives at
~/.local/state/airlock/loop_state.json (directory mode 700, file mode 600),
guarded with flock so concurrent PreToolUse hook processes never corrupt it.

Fail-safe direction: any failure reading/writing this file means
was_recently_denied() returns False (i.e. "no prior denial seen") -- the
consequence is one extra deny gets emitted rather than a wrong one being
silently allowed through loop protection.
"""
import fcntl
import json
import os
import time
from pathlib import Path

from . import paths

STATE_DIR = paths.state_dir()
STATE_FILE = STATE_DIR / "loop_state.json"

# Keep the file bounded: prune entries far older than any window callers will
# realistically pass (the brief's loop window is 10 minutes).
PRUNE_AFTER_S = 3600


def _key_str(key):
    """key is (kind, normalised_value), e.g. ("bash", "find / -name foo")."""
    kind, value = key
    return "%s\x00%s" % (kind, value)


def _ensure_dir():
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(STATE_DIR, 0o700)
    except Exception:
        pass


def _open_locked(lock_type):
    _ensure_dir()
    fd = os.open(str(STATE_FILE), os.O_CREAT | os.O_RDWR, 0o600)
    fcntl.flock(fd, lock_type)
    return fd


def _load(fd):
    try:
        os.lseek(fd, 0, os.SEEK_SET)
        raw = os.read(fd, 10 * 1024 * 1024)
        if not raw:
            return {}
        data = json.loads(raw.decode("utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save(fd, data):
    raw = json.dumps(data).encode("utf-8")
    os.ftruncate(fd, 0)
    os.lseek(fd, 0, os.SEEK_SET)
    os.write(fd, raw)


def _prune(data, now):
    for sess in list(data.keys()):
        entries = data.get(sess) or {}
        for k in list(entries.keys()):
            try:
                stale = (now - entries[k]) > PRUNE_AFTER_S
            except Exception:
                stale = True
            if stale:
                del entries[k]
        if entries:
            data[sess] = entries
        else:
            del data[sess]
    return data


def was_recently_denied(session_id, key, window_s):
    """True if (session_id, key) was recorded by record_denial() within the
    last window_s seconds. Never raises."""
    try:
        fd = _open_locked(fcntl.LOCK_SH)
    except Exception:
        return False
    try:
        data = _load(fd)
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except Exception:
            pass
        try:
            os.close(fd)
        except Exception:
            pass

    ts = (data.get(session_id or "") or {}).get(_key_str(key))
    if ts is None:
        return False
    try:
        return (time.time() - ts) <= window_s
    except Exception:
        return False


def record_denial(session_id, key):
    """Record that (session_id, key) was just denied, for future loop
    protection. Never raises."""
    try:
        fd = _open_locked(fcntl.LOCK_EX)
    except Exception:
        return
    try:
        data = _load(fd)
        now = time.time()
        data = _prune(data, now)
        data.setdefault(session_id or "", {})[_key_str(key)] = now
        _save(fd, data)
    except Exception:
        return
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except Exception:
            pass
        try:
            os.close(fd)
        except Exception:
            pass
