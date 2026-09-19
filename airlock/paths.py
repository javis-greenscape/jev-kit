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

On Windows there is no XDG layout to follow, so the three directories move
to the places Windows actually reserves for them (item 1 of the native-Windows
brief), keeping every environment override exactly as it is:

  config    %APPDATA%\\airlock          (roams with the profile: mode,
                                         rules.json, the key file)
  state     %LOCALAPPDATA%\\airlock\\state   (machine-local: the shadow log,
                                         loop-protection state, tune state)
  releases  %LOCALAPPDATA%\\airlock      (holds releases\\ and the `current`
                                         pointer; state\\ is its sibling)

The same legacy-name fallback applies there, under the same two parents, so a
Windows machine that somehow carries a `plumbline` directory is not reset.

Deliberately stdlib-only apart from airlock.platform_compat (which is itself
stdlib-only and imports nothing from airlock), so the hot path in
hooks/airlock.py can resolve a directory without pulling in client, guards or
policy.
"""
import os
from pathlib import Path

from .platform_compat import is_windows

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


def _win_base(var, *fallback_parts):
    """%APPDATA% or %LOCALAPPDATA%, falling back to the documented default
    location under the profile when the variable is missing (which happens in
    a stripped hook environment). Never raises."""
    try:
        raw = os.environ.get(var)
    except Exception:
        raw = None
    if raw:
        try:
            return Path(os.path.expandvars(os.path.expanduser(raw)))
        except Exception:
            pass
    return _home().joinpath(*fallback_parts)


def _resolve(env_names, parent_parts, legacy_parent_parts=None,
             win_base=None, win_tail=(), windows=None):
    override = env(*env_names)
    if override:
        try:
            return Path(os.path.expanduser(override))
        except Exception:
            pass
    if is_windows(windows) and win_base is not None:
        # The app directory itself is picked first (new name, then each legacy
        # name), and only then is the tail appended -- so state lands at
        # %LOCALAPPDATA%\airlock\state, a sibling of releases\ and current,
        # rather than at %LOCALAPPDATA%\state\airlock.
        app_dir = _pick(win_base / APP, *[win_base / name for name in LEGACY_APPS])
        return app_dir.joinpath(*win_tail)
    home = _home()
    parent = home.joinpath(*parent_parts)
    legacy_parent = home.joinpath(*(legacy_parent_parts or parent_parts))
    return _pick(parent / APP, *[legacy_parent / name for name in LEGACY_APPS])


def config_dir(windows=None):
    """~/.config/airlock, else ~/.config/plumbline, else ~/.config/jev-guard,
    whichever of the older two exists first.

    On Windows: %APPDATA%\\airlock, with the same two legacy names tried under
    %APPDATA% before falling back to the new one."""
    return _resolve(CONFIG_ENV, (".config",),
                    win_base=_win_base("APPDATA", "AppData", "Roaming"),
                    windows=windows)


def state_dir(windows=None):
    """~/.local/state/airlock, else the plumbline then jev-guard names.

    On Windows: %LOCALAPPDATA%\\airlock\\state -- machine-local, deliberately
    NOT roaming, and a sibling of the releases directory."""
    return _resolve(STATE_ENV, (".local", "state"),
                    win_base=_win_base("LOCALAPPDATA", "AppData", "Local"),
                    win_tail=("state",), windows=windows)


def install_home(windows=None):
    """$AIRLOCK_HOME: where deploy.sh writes releases and `current`.
    ~/.local/share/airlock, else the plumbline then jev-guard names.

    On Windows: %LOCALAPPDATA%\\airlock."""
    return _resolve(HOME_ENV, (".local", "share"),
                    win_base=_win_base("LOCALAPPDATA", "AppData", "Local"),
                    windows=windows)


def config_file(name):
    return config_dir() / name


def state_file(name):
    return state_dir() / name


def _runtime_dir(windows=None):
    """The parent the daemon socket lives in.

    On Windows there is no daemon at all (no AF_UNIX), so there is also no
    /tmp to fall back to and nothing world-writable may be named: the answer
    is a path under the per-user state directory that nothing ever binds.
    `airlock/client.py` never even asks for it there -- it skips the daemon
    outright -- but the function must still return something rather than
    raising or naming /tmp."""
    if is_windows(windows):
        return str(state_dir(windows=windows) / "runtime")
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if not runtime:
        try:
            runtime = "/run/user/%d" % os.getuid()
        except Exception:
            runtime = "/tmp"
    return runtime


def runtime_socket(windows=None):
    """The daemon's Unix socket. New name first; if it is absent and an older
    socket is live -- plumbline's, then jev-guard's -- use that, so a daemon
    started before a rename keeps serving hooks from a renamed release until
    somebody restarts it."""
    runtime = _runtime_dir(windows=windows)
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


def runtime_socket_dir(windows=None):
    """The directory the DAEMON should create and bind in -- always the new
    name. Only a client ever falls back to an older socket."""
    return os.path.join(_runtime_dir(windows=windows), APP)
