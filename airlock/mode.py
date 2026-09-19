"""Mode resolution for airlock: shadow (default), enforce, or off.

Resolution order (item 1 of the enforce-mode brief):
  1. env AIRLOCK_MODE, if it is one of the valid values.
  2. else the first whitespace-separated word of ~/.config/airlock/mode.
  3. else "shadow".

The separate kill switch (AIRLOCK_DISABLE=1 / ~/.config/airlock/disabled)
still wins over everything -- that check happens in hooks/airlock.py before
mode is ever consulted, unchanged from shadow-only behaviour.

Deliberately dependency-free (stdlib only, no other airlock imports) so the
hot path in hooks/airlock.py can resolve mode without pulling in client,
guards, or policy for the common shadow/off case.
"""
import os

from . import paths

MODE_FILE = str(paths.config_file("mode"))
MODE_ENV = ("AIRLOCK_MODE", "PLUMBLINE_MODE", "JEV_GUARD_MODE")
VALID_MODES = ("shadow", "enforce", "off")
DEFAULT_MODE = "shadow"


def resolve_mode():
    """Return one of "shadow", "enforce", "off". Never raises."""
    try:
        env = paths.env(*MODE_ENV)
        if env in VALID_MODES:
            return env
    except Exception:
        pass

    try:
        with open(MODE_FILE, "r") as f:
            words = f.read().split()
        if words and words[0] in VALID_MODES:
            return words[0]
    except Exception:
        pass

    return DEFAULT_MODE
