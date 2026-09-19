"""Backward-compatibility shim: `plumbline` is now `airlock`.

The project has been renamed twice -- `jev_guard` -> `plumbline` -> `airlock`
-- and this package exists only so that a machine still running the middle
layout keeps working: a settings.json hook, a systemd unit, a script, or a
session that has `python3 -m plumbline.report` in its habits. Every name here
is the real object from `airlock`, not a copy, so there is no second
implementation to drift.

The oldest name is shimmed the same way in `jev_guard/__init__.py`, and both
point at the same modules.

Delete this package once every machine has been migrated. Nothing inside
`airlock` imports it.
"""
import importlib
import sys
import warnings

TARGET = "airlock"

# The submodules a caller could plausibly have imported by the old name.
# Aliased eagerly rather than lazily: `import plumbline.report` (as opposed to
# `from plumbline import report`) is resolved by the import system against
# this package's own __path__, which has no such file, so a module-level
# __getattr__ would not catch it. Every import is wrapped, so one module that
# cannot load -- a missing optional dependency, say -- does not take the whole
# shim down with it.
SUBMODULES = (
    "belt", "client", "context", "daemon", "enforce", "eval", "guards",
    "health", "keyfile", "log", "mode", "paths", "policy", "questions",
    "redact", "report", "rules", "scope", "state", "worker",
)

warnings.warn(
    "plumbline has been renamed to airlock; import airlock instead. "
    "This shim will be removed once every machine has been migrated.",
    DeprecationWarning,
    stacklevel=2,
)


def _install_aliases():
    importlib.import_module(TARGET)
    for name in SUBMODULES:
        try:
            module = importlib.import_module("%s.%s" % (TARGET, name))
        except Exception:  # a broken submodule is not this shim's problem
            continue
        sys.modules["%s.%s" % (__name__, name)] = module
        globals()[name] = module


_install_aliases()
