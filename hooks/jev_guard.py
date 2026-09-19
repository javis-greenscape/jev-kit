#!/usr/bin/env python3
"""Backward-compatibility shim: this hook is now hooks/airlock.py.

A machine whose settings.json still names `hooks/jev_guard.py` keeps working
unchanged: this file runs the real entry point in the same process, so stdin,
stdout and the exit code behave exactly as before. There is no second
implementation here to drift out of step.

Delete this file once every settings.json on every machine has been repointed
(`install/wire.sh --print <settings.json>` shows the edit).
"""
import os
import runpy
import sys

HOOK_DIR = os.path.dirname(os.path.abspath(__file__))
TARGET = os.path.join(HOOK_DIR, "airlock.py")

if __name__ == "__main__":
    try:
        # run_name="__main__" so the target's own `if __name__ == "__main__"`
        # block runs, including its sys.exit(0). Nothing is imported from
        # hooks/, so the package/module name clash cannot bite here either.
        runpy.run_path(TARGET, run_name="__main__")
    except SystemExit:
        raise
    except Exception:
        pass
    sys.exit(0)
