"""Test-suite fixture: point every path the suite touches at a throwaway HOME.

Config, state and key-file resolution (airlock/paths.py, airlock/rules.py,
airlock/mode.py, airlock/state.py, airlock/log.py, airlock/keyfile.py) is
computed ONCE, at import time, from $HOME and the AIRLOCK_*/legacy
environment variables (see e.g. `rules.CONFIG_FILE = str(paths.config_file(...))`
at module scope). A real machine's $HOME can carry a `~/.config/airlock/rules.json`
that overrides a rule's action (e.g. `{"R5-sudo": "warn"}`) or a `mode` file
that is not the test suite's assumed default -- and because those constants
are cached at import time, a test-by-test `mock.patch` cannot always reach
them before the first import wins the race. The result: the exact same suite
passes on a clean machine and fails on a machine that has actually configured
airlock, which is backwards -- `install/deploy.sh` running this suite before
deploying must not be able to refuse a deploy because of THIS machine's own
config.

`python -m unittest discover -s tests` imports the `tests` package (this
file) before it imports any `test_*.py` module, so setting the environment
here -- before any `airlock.*` submodule has been imported anywhere in the
process -- is early enough that every module-level path constant computed
downstream sees this throwaway directory, never the real one. This is the
single central mechanism; individual tests still choose to patch further
(e.g. test_paths.py deliberately pops these variables to test the fallback
chain), and that is unaffected since paths.py's own functions read the
environment live rather than caching it.
"""
import atexit
import os
import shutil
import tempfile

_TMP_HOME = tempfile.mkdtemp(prefix="airlock-test-home-")
atexit.register(shutil.rmtree, _TMP_HOME, ignore_errors=True)

# Belt-and-braces: HOME itself, so anything that still does a bare
# os.path.expanduser("~") (airlock/rules.py's own HOME constant, R5/R7's path
# checks, keyfile.py's default key-file location) lands under the throwaway
# directory too, not the real one -- regardless of what HOME was set to by
# whatever invoked this test run.
os.environ["HOME"] = _TMP_HOME

# Clear any of this machine's own overrides for the newest and both legacy
# names, then pin the newest name at a directory under the throwaway HOME.
# Clearing first means a real AIRLOCK_HOME (or a legacy PLUMBLINE_/JEV_ one)
# exported by the calling shell can never leak into the suite.
for _var in (
    "AIRLOCK_CONFIG_DIR", "PLUMBLINE_CONFIG_DIR", "JEV_GUARD_CONFIG_DIR",
    "AIRLOCK_STATE_DIR", "PLUMBLINE_STATE_DIR", "JEV_GUARD_STATE_DIR",
    "AIRLOCK_HOME", "PLUMBLINE_HOME", "JEV_HOME",
    "AIRLOCK_KEY_FILE", "PLUMBLINE_KEY_FILE", "JEV_GUARD_KEY_FILE",
    "AIRLOCK_MODE", "PLUMBLINE_MODE", "JEV_GUARD_MODE",
):
    os.environ.pop(_var, None)

os.environ["AIRLOCK_CONFIG_DIR"] = os.path.join(_TMP_HOME, ".config", "airlock")
os.environ["AIRLOCK_STATE_DIR"] = os.path.join(_TMP_HOME, ".local", "state", "airlock")
os.environ["AIRLOCK_HOME"] = os.path.join(_TMP_HOME, ".local", "share", "airlock")

# --- platform gates ----------------------------------------------------------
#
# The suite runs on Linux and on native Windows. Most of it is platform-neutral
# and must pass on both; a handful of tests assert something only POSIX has --
# a Unix domain socket, a `chmod` mode, the XDG directory layout, a bash
# installer script -- and those are skipped on Windows with the reason stated,
# never quietly deleted. Where a Windows equivalent exists it is named in the
# skip reason.
#
# The rule that matters and is enforced by the reverse of this gate: NO test
# may REQUIRE Windows to pass. Everything Windows-specific is exercised on
# Linux by injecting the platform (see airlock/platform_compat.py), so
# install/deploy.sh's Linux test run still gates the Windows code.
import sys
import unittest

WINDOWS = sys.platform == "win32"


def posix_only(reason):
    """Skip on Windows, with the reason printed rather than implied."""
    return unittest.skipIf(WINDOWS, "POSIX only: %s" % reason)
