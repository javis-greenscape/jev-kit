"""Where airlock keeps its config, its state, and its deployed releases.

The project has been renamed twice: it was `jev-guard`, then `plumbline`, and
is now `airlock`. A machine that has been running either earlier name has real
data under the old directory names: a mode file that may say `enforce`, a
`rules.json` with per-rule overrides, the shadow log, the loop protection
state, and the tuning loop's own state. Renaming must not silently reset any
of that, so every directory here resolves in this order:

  1. an explicit environment variable, newest name first, both older prefixes
     still accepted (AIRLOCK_*, then PLUMBLINE_*, then the jev-guard names);
  2. the new `airlock` directory, if it already exists;
  3. the `plumbline` directory, if THAT already exists;
  4. the original `jev-guard` directory, if THAT already exists;
  5. otherwise the new `airlock` directory, created on first write.

Steps 3 and 4 are what keep a live machine mid-cutover on its existing mode
and history instead of silently dropping back to `shadow`. The live box has
real `plumbline` directories with `jev-guard` symlinks pointing at them, so
step 3 wins there and resolves to exactly the same files either way.

`install/migrate-to-airlock.sh` moves the old directories to the new names and
is idempotent; until it has been run, steps 3 and 4 are what keep the machine
working.

Deliberately stdlib-only and free of any other airlock import, so the hot
path in hooks/airlock.py can resolve a directory without pulling in client,
guards or policy.
"""
import os
from pathlib import Path

APP = "airlock"
# Every earlier name, newest first. A directory is looked for under each in
# turn, so a machine that skipped the middle name still resolves correctly.
LEGACY_APPS = ("plumbline", "jev-guard")
# Kept for anything that still imports the old single-name constant.
LEGACY_APP = LEGACY_APPS[-1]

CONFIG_ENV = ("AIRLOCK_CONFIG_DIR", "PLUMBLINE_CONFIG_DIR", "JEV_GUARD_CONFIG_DIR")
STATE_ENV = ("AIRLOCK_STATE_DIR", "PLUMBLINE_STATE_DIR", "JEV_GUARD_STATE_DIR")
HOME_ENV = ("AIRLOCK_HOME", "PLUMBLINE_HOME", "JEV_HOME")


def env(*names, **kwargs):
    """First non-empty environment variable among `names`, else `default`.

    The new AIRLOCK_* name always comes first, then PLUMBLINE_*, then the
    original jev-guard name, so a machine that sets more than one during a
    cutover gets the newest. Never raises.
    """
    default = kwargs.get("default")
    for name in names:
        try:
            value = os.environ.get(name)
        except Exception:
            return default
        if value:
            return value
    return default


def _home():
    try:
        return Path(os.path.expanduser("~"))
    except Exception:
        return Path("/")


def _pick(new, *legacies):
    """Prefer `new`; otherwise the first of `legacies` that exists; otherwise
    `new` again (to be created on first write). Never raises -- an unreadable
    path just means `new`."""
    try:
        if new.exists():
            return new
        for legacy in legacies:
            try:
                if legacy.exists():
                    return legacy
            except Exception:
                continue
    except Exception:
        pass
    return new


def _resolve(env_names, parent_parts, legacy_parent_parts=None):
    override = env(*env_names)
    if override:
        try:
            return Path(os.path.expanduser(override))
        except Exception:
            pass
    home = _home()
    parent = home.joinpath(*parent_parts)
    legacy_parent = home.joinpath(*(legacy_parent_parts or parent_parts))
    return _pick(parent / APP, *[legacy_parent / name for name in LEGACY_APPS])


def config_dir():
    """~/.config/airlock, else ~/.config/plumbline, else ~/.config/jev-guard,
    whichever of the older two exists first."""
    return _resolve(CONFIG_ENV, (".config",))


def state_dir():
    """~/.local/state/airlock, else the plumbline then jev-guard names."""
    return _resolve(STATE_ENV, (".local", "state"))


def install_home():
    """$AIRLOCK_HOME: where deploy.sh writes releases and `current`.
    ~/.local/share/airlock, else the plumbline then jev-guard names."""
    return _resolve(HOME_ENV, (".local", "share"))


def config_file(name):
    return config_dir() / name


def state_file(name):
    return state_dir() / name


def runtime_socket():
    """The daemon's Unix socket. New name first; if it is absent and an older
    socket is live -- plumbline's, then jev-guard's -- use that, so a daemon
    started before a rename keeps serving hooks from a renamed release until
    somebody restarts it."""
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if not runtime:
        try:
            runtime = "/run/user/%d" % os.getuid()
        except Exception:
            runtime = "/tmp"
    new = os.path.join(runtime, APP, "%s.sock" % APP)
    legacies = (
        os.path.join(runtime, "plumbline", "plumbline.sock"),
        os.path.join(runtime, "jev", "jev.sock"),
    )
    try:
        if not os.path.exists(new):
            for legacy in legacies:
                if os.path.exists(legacy):
                    return legacy
    except Exception:
        pass
    return new


def runtime_socket_dir():
    """The directory the DAEMON should create and bind in -- always the new
    name. Only a client ever falls back to an older socket."""
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if not runtime:
        try:
            runtime = "/run/user/%d" % os.getuid()
        except Exception:
            runtime = "/tmp"
    return os.path.join(runtime, APP)
