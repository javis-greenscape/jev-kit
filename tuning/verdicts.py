#!/usr/bin/env python3
"""Persist the judge's per-row verdicts so a run can be audited afterwards.

The first unattended run reported `"judged": 20, "wrong": 17` and kept
nothing else. Answering "why 17?" afterwards took reconstructing the batch
from timestamps in the shadow log and guessing at the prompt, because the
verdicts themselves were held in a local variable and dropped on exit. A run
that cannot be questioned after the fact cannot be trusted before it.

Every run now writes one JSONL file per run under `$STATE_DIR/tune_verdicts/`,
mode 600 (the rows are redacted but still private), keeping the newest 20
runs and deleting the rest.

What is stored is the VERDICT, not the row: row id, guard, sample rule, the
label Jev chose and the label the judge chose, whether the action taken
matched policy, and the judge's one-line reason. No command text, no cwd, no
prompt -- the shadow log already holds those under the same protection, and a
second copy is a second thing to leak.
"""
import json
import os
import time
from pathlib import Path

VERDICT_DIRNAME = "tune_verdicts"
KEEP_RUNS = 20

# The only keys copied out of a verdict. Anything the judge invents beyond
# this is dropped rather than written to disk.
VERDICT_FIELDS = (
    "id", "ts", "guard", "sample_rule", "question", "jev_label", "correct_label",
    "label_correct", "action_taken", "action_correct", "cannot_tell", "wrong",
    "reason",
)


def verdict_dir(state_dir):
    return Path(state_dir) / VERDICT_DIRNAME


def _slim(verdict):
    return {k: verdict.get(k) for k in VERDICT_FIELDS if k in verdict}


def write_run(state_dir, verdicts, run_ts=None, meta=None, keep=KEEP_RUNS):
    """Write one run's verdicts and prune old ones. Returns the path written,
    or None if there was nothing to write.

    Never raises: a tuning run must not die because an audit file could not be
    created. A failure returns None and the caller logs it."""
    if not verdicts:
        return None
    try:
        directory = verdict_dir(state_dir)
        directory.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(directory, 0o700)
        except Exception:
            pass
        stamp = run_ts or time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        path = directory / ("%s.jsonl" % stamp)
        # Create with 600 from the start: never a window where it is readable.
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as handle:
            if meta:
                handle.write(json.dumps({"_meta": meta}, default=str) + "\n")
            for verdict in verdicts:
                handle.write(json.dumps(_slim(verdict), default=str) + "\n")
        try:
            os.chmod(path, 0o600)
        except Exception:
            pass
        prune(state_dir, keep=keep)
        return path
    except Exception:
        return None


def prune(state_dir, keep=KEEP_RUNS):
    """Keep only the newest `keep` verdict files. Returns how many were
    removed."""
    directory = verdict_dir(state_dir)
    if not directory.is_dir():
        return 0
    files = sorted(directory.glob("*.jsonl"))
    removed = 0
    for path in files[:max(0, len(files) - keep)]:
        try:
            path.unlink()
            removed += 1
        except Exception:
            pass
    return removed


def read_run(path):
    """Read one verdict file back. Used by tests and by a human asking what a
    run actually decided."""
    out = []
    with open(path, "r") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                continue
    return out
