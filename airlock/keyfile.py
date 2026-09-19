"""Read the TypeSafe API key without ever printing it.

The hook process usually will NOT have TYPESAFE_API_KEY in its environment
(Claude Code hooks inherit a minimal env), so this falls back to parsing a key
file for a `TYPESAFE_API_KEY=` line. Which file is machine-specific
(AIRLOCK_KEY_FILE, default ~/.config/airlock/env, with any extra path this
machine's install/config.env names honoured when it exists). Never logs, never
echoes, never shells out to read the file (no cat/curl with the key on a
command line) -- plain Python file I/O only.
"""
import os

from . import paths

ENV_VAR = "TYPESAFE_API_KEY"

# Which file holds the key is machine-specific: set AIRLOCK_KEY_FILE in
# install/config.env (see install/config.env.example). The default is
# generic -- ~/.config/airlock/env -- so a fresh machine needs no config at
# all. No other path is baked into this file: a machine that keeps its key
# somewhere else says so in its own config.env, and install/install.sh
# records that path in the pointer file below so the hook -- which runs with
# a minimal environment and never sources config.env -- can still find it.
DEFAULT_ENV_FILE = "~/.config/airlock/env"

# A one-line file holding the PATH of the key file, never its contents.
# Written by install/install.sh from AIRLOCK_KEY_FILE, read here when the
# environment variable is absent (which is the normal case inside a hook).
POINTER_FILE = "keyfile.path"

# Extra key-file paths this machine wants honoured, colon-separated, from
# AIRLOCK_LEGACY_KEY_FILES in install/config.env. Empty on a fresh machine.
LEGACY_ENV_VAR = "AIRLOCK_LEGACY_KEY_FILES"


def _pointer_path():
    """The path recorded in $AIRLOCK_CONFIG_DIR/keyfile.path, or None."""
    try:
        pointer = paths.config_file(POINTER_FILE)
        if not pointer.is_file():
            return None
        recorded = pointer.read_text().strip()
    except Exception:
        return None
    if not recorded or recorded.startswith("#"):
        return None
    try:
        return os.path.expanduser(recorded)
    except Exception:
        return None


def legacy_env_files():
    """Extra key-file paths from the environment. Never raises."""
    raw = paths.env(LEGACY_ENV_VAR, default="")
    if not raw:
        return ()
    return tuple(part for part in raw.split(os.pathsep) if part.strip())


def default_env_file():
    """The key file to read when AIRLOCK_KEY_FILE is not set. Never raises."""
    generic = os.path.expanduser(DEFAULT_ENV_FILE)
    try:
        if os.path.isfile(generic):
            return generic
        recorded = _pointer_path()
        if recorded and os.path.isfile(recorded):
            return recorded
        for legacy in legacy_env_files():
            candidate = os.path.expanduser(legacy)
            if os.path.isfile(candidate):
                return candidate
    except Exception:
        pass
    return generic


ENV_FILE = os.path.expanduser(
    paths.env("AIRLOCK_KEY_FILE", "PLUMBLINE_KEY_FILE", "JEV_GUARD_KEY_FILE",
              default=default_env_file())
)


def get_api_key():
    """Return the API key string, or None if it cannot be found. Never raises."""
    try:
        key = os.environ.get(ENV_VAR)
        if key:
            return key
    except Exception:
        pass

    try:
        with open(ENV_FILE, "r") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("export "):
                    line = line[len("export "):].strip()
                if not line.startswith(ENV_VAR + "="):
                    continue
                value = line.split("=", 1)[1].strip()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
                    value = value[1:-1]
                return value or None
    except Exception:
        return None
    return None
