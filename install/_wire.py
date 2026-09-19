#!/usr/bin/env python3
"""Helper for install/wire.sh.

Two jobs, both idempotent:

  1. REPOINT an existing airlock PreToolUse hook command at the deployed
     copy. Surgical text substitution, not a JSON re-serialize, so this edit
     changes exactly the hook command string(s) and nothing else about a
     hand-authored settings.json (key order, indentation, unrelated hooks).

  2. ADD whatever is missing -- the PreToolUse entry itself (a first install
     has nothing to repoint: there is no airlock hook command anywhere yet),
     and, opt in per flag, the belay Stop hook and the function-hooks env
     var. Adding necessarily changes the JSON's structure, so this path
     re-serializes the whole file (via json.load/json.dump, which preserves
     existing key order -- Python dicts keep insertion order) rather than
     doing text surgery on a shape that was not there to begin with. Every
     existing key and hook survives; only the missing pieces are appended.

A file that needs ONLY a repoint (the common case on a machine that already
wired an earlier release) still goes through the byte-preserving text path.
A file that needs anything ADDED goes through the structural path, even if
it also needs a repoint -- there is no way to add new nested JSON without
becoming a JSON re-serializer for that file.

Reads NEW_HOOK, NEW_HOOK_COMMAND, APPLY, BELAY, BELAY_WRAPPER, FUNCTION_HOOKS
from the environment (set by wire.sh) and the settings.json paths from argv.
Never touches a path not given on the command line.
"""
import copy
import datetime
import json
import os
import re
import shutil
import sys

NEW_HOOK = os.environ["NEW_HOOK"]
NEW_HOOK_COMMAND = os.environ.get("NEW_HOOK_COMMAND", NEW_HOOK)
APPLY = os.environ.get("APPLY") == "1"
BELAY = os.environ.get("BELAY") == "1"
BELAY_WRAPPER = os.environ.get("BELAY_WRAPPER", "")
FUNCTION_HOOKS = os.environ.get("FUNCTION_HOOKS") == "1"
FUNCTION_HOOKS_ENV_KEY = "CLAUDE_CODE_ENABLE_FUNCTION_HOOKS"
PRETOOLUSE_TIMEOUT = 5
BELAY_TIMEOUT = 25

# Matches a JSON string value that is (or ends in) a path to airlock.py's
# hook entry point -- e.g. "$HOME/code/airlock/hooks/airlock.py" or
# "$HOME/.local/share/airlock/current/hooks/airlock.py" (written out in
# full in the file it reads; nothing here assumes a particular user).
# Deliberately anchored on "hooks/airlock.py" so it matches regardless of
# which checkout or release directory currently precedes it.
# Matches the new name and BOTH older names (`hooks/plumbline.py`,
# `hooks/jev_guard.py`), so wiring a machine that predates either rename
# repoints it in one pass.
#
# Captured in two parts, because a hook command is normally
# "<interpreter> <path>" and the interpreter is a deliberate choice -- a
# machine that pins /usr/bin/python3 rather than relying on the shebang and
# the file's mode bit must keep doing so after a rewire. Group 1 is
# everything up to and including the last space before the path; group 2 is
# the path itself, and only group 2 is replaced.
_HOOK_PATTERN = re.compile(
    r'"((?:(?:[^"\\]|\\.)*?\s)?)((?:[^"\\\s]|\\.)*hooks/(?:airlock|plumbline|jev_guard)\.py)"')

# Same match, applied to a bare (unquoted) command string already extracted
# from parsed JSON -- used when walking the structure instead of the text.
_HOOK_CMD_RE = re.compile(
    r'^((?:(?:[^\\]|\\.)*?\s)?)((?:[^\\\s]|\\.)*hooks/(?:airlock|plumbline|jev_guard)\.py)$')


def _backup(path):
    backup = "%s.bak.%s" % (path, datetime.datetime.now().strftime("%Y%m%dT%H%M%S"))
    shutil.copy2(path, backup)
    return backup


def _pretooluse_block():
    return {"matcher": "*", "hooks": [
        {"type": "command", "command": NEW_HOOK_COMMAND, "timeout": PRETOOLUSE_TIMEOUT}]}


def _belay_block():
    return {"matcher": "*", "hooks": [
        {"type": "command", "command": BELAY_WRAPPER, "timeout": BELAY_TIMEOUT}]}


def _matcher_block(entries, matcher):
    for entry in entries:
        if isinstance(entry, dict) and entry.get("matcher") == matcher:
            return entry
    return None


def _has_command(entries, command):
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        for h in entry.get("hooks") or []:
            if isinstance(h, dict) and h.get("command") == command:
                return True
    return False


def _append_hook(data, event, block, command):
    """Add `block`'s single hook to `data["hooks"][event]`, reusing an
    existing matcher="*" entry when one is there rather than adding a
    second one. No-op if `command` is already present anywhere in `event`."""
    hooks = data.setdefault("hooks", {})
    entries = hooks.setdefault(event, [])
    if _has_command(entries, command):
        return False
    existing_star = _matcher_block(entries, "*")
    if existing_star is not None:
        existing_star.setdefault("hooks", []).append(block["hooks"][0])
    else:
        entries.append(block)
    return True


def _fresh_settings():
    data = {"hooks": {"PreToolUse": [_pretooluse_block()]}}
    if BELAY and BELAY_WRAPPER and os.path.isfile(BELAY_WRAPPER):
        data["hooks"]["Stop"] = [_belay_block()]
    if FUNCTION_HOOKS:
        data["env"] = {FUNCTION_HOOKS_ENV_KEY: "1"}
    return data


def _describe_fresh():
    lines = ["  PreToolUse: matcher \"*\", command \"%s\", timeout %d"
             % (NEW_HOOK_COMMAND, PRETOOLUSE_TIMEOUT)]
    if BELAY:
        if BELAY_WRAPPER and os.path.isfile(BELAY_WRAPPER):
            lines.append("  Stop (belay): matcher \"*\", command \"%s\", timeout %d"
                          % (BELAY_WRAPPER, BELAY_TIMEOUT))
        else:
            lines.append("  Stop (belay): SKIPPED, no wrapper at %s" % (BELAY_WRAPPER or "<unset>"))
    if FUNCTION_HOOKS:
        lines.append("  env.%s = \"1\"" % FUNCTION_HOOKS_ENV_KEY)
    return lines


def _process_missing(path):
    if not APPLY:
        print("%s: does not exist; --apply would create it with:" % path)
        for line in _describe_fresh():
            print(line)
        return True
    data = _fresh_settings()
    with open(path, "w") as f:
        f.write(json.dumps(data, indent=2) + "\n")
    print("%s: did not exist, created with:" % path)
    for line in _describe_fresh():
        print(line)
    return True


def _repoint_text(text):
    """Pure text substitution: repoint every airlock hook command found to
    NEW_HOOK, keeping each command's own interpreter prefix. Returns the new
    text, or None if nothing needed repointing."""
    def _replace(match):
        return '"%s%s"' % (match.group(1), NEW_HOOK)
    return _HOOK_PATTERN.sub(_replace, text)


def _structural_repoint(data):
    """Walk hooks.PreToolUse (and, defensively, every hook block) and
    repoint any command whose path is an old airlock/plumbline/jev_guard
    hook path, in place. Returns True if anything changed."""
    changed = False
    hooks = data.get("hooks") or {}
    for entries in hooks.values():
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            for h in entry.get("hooks") or []:
                if not isinstance(h, dict):
                    continue
                cmd = h.get("command")
                if not isinstance(cmd, str):
                    continue
                m = _HOOK_CMD_RE.match(cmd)
                if m and m.group(2) != NEW_HOOK:
                    h["command"] = m.group(1) + NEW_HOOK
                    changed = True
    return changed


def process(path):
    if not os.path.isfile(path):
        return _process_missing(path)

    with open(path, "r") as f:
        text = f.read()

    try:
        data = json.loads(text)
    except Exception as exc:
        print("%s: not valid JSON (%s), refusing to touch it" % (path, exc), file=sys.stderr)
        return False

    matches = _HOOK_PATTERN.findall(text)
    needs_repoint = bool(matches) and not all(hook_path == NEW_HOOK for _prefix, hook_path in matches)
    needs_add_pretooluse = not matches

    belay_ok = BELAY and bool(BELAY_WRAPPER) and os.path.isfile(BELAY_WRAPPER)
    belay_missing_wrapper = BELAY and not belay_ok
    stop_entries = (data.get("hooks") or {}).get("Stop") or []
    needs_belay = belay_ok and not _has_command(stop_entries, BELAY_WRAPPER)

    needs_function_hooks = FUNCTION_HOOKS and (data.get("env") or {}).get(FUNCTION_HOOKS_ENV_KEY) != "1"

    if not (needs_repoint or needs_add_pretooluse or needs_belay or needs_function_hooks):
        if matches:
            print("%s: already wired to %s" % (path, NEW_HOOK))
        else:
            print("%s: nothing to add or repoint" % path)
        if belay_missing_wrapper:
            print("%s: --belay given but no wrapper at %s, skipping Stop hook"
                  % (path, BELAY_WRAPPER or "<unset>"))
        return False

    # A pure repoint -- nothing to ADD -- keeps the byte-preserving text
    # path: it is both the common case (a machine re-wiring after a
    # release) and the one this design exists to protect (see module
    # docstring and tests/test_wire.py's TestInterpreterPrefix/TestSafety).
    if needs_repoint and not (needs_add_pretooluse or needs_belay or needs_function_hooks):
        new_text = _repoint_text(text)
        try:
            json.loads(new_text)
        except Exception as exc:
            print("%s: substitution would produce invalid JSON (%s), refusing" % (path, exc),
                  file=sys.stderr)
            return False
        if not APPLY:
            print("%s: would repoint %d hook path(s) to %s" % (path, len(matches), NEW_HOOK))
            for prefix, hook_path in sorted(set(matches)):
                if hook_path != NEW_HOOK:
                    print("  - %s%s" % (prefix, hook_path))
                    print("    -> %s%s" % (prefix, NEW_HOOK))
            return True
        backup = _backup(path)
        with open(path, "w") as f:
            f.write(new_text)
        print("%s: backed up to %s, %d hook path(s) -> %s (interpreter prefix kept)"
              % (path, backup, len(matches), NEW_HOOK))
        return True

    # Something needs ADDING (possibly alongside a repoint): structural
    # edit. Still idempotent -- run again and every branch above reports
    # nothing left to do.
    actions = []
    new_data = copy.deepcopy(data)
    if needs_repoint:
        if _structural_repoint(new_data):
            actions.append("repointed %d existing hook path(s) to %s" % (len(matches), NEW_HOOK))
    if needs_add_pretooluse:
        if _append_hook(new_data, "PreToolUse", _pretooluse_block(), NEW_HOOK_COMMAND):
            actions.append('added PreToolUse: matcher "*", command "%s", timeout %d'
                            % (NEW_HOOK_COMMAND, PRETOOLUSE_TIMEOUT))
    if needs_belay:
        if _append_hook(new_data, "Stop", _belay_block(), BELAY_WRAPPER):
            actions.append('added Stop (belay): matcher "*", command "%s", timeout %d'
                            % (BELAY_WRAPPER, BELAY_TIMEOUT))
    if needs_function_hooks:
        new_data.setdefault("env", {})[FUNCTION_HOOKS_ENV_KEY] = "1"
        actions.append('set env.%s = "1"' % FUNCTION_HOOKS_ENV_KEY)

    new_text = json.dumps(new_data, indent=2) + "\n"
    try:
        json.loads(new_text)
    except Exception as exc:
        print("%s: edit would produce invalid JSON (%s), refusing" % (path, exc), file=sys.stderr)
        return False

    if not APPLY:
        print("%s: --apply would:" % path)
        for a in actions:
            print("  %s" % a)
        if belay_missing_wrapper:
            print("  (--belay given but no wrapper at %s, skipping Stop hook)"
                  % (BELAY_WRAPPER or "<unset>"))
        return True

    backup = _backup(path)
    with open(path, "w") as f:
        f.write(new_text)
    print("%s: backed up to %s" % (path, backup))
    for a in actions:
        print("  %s" % a)
    if belay_missing_wrapper:
        print("  (--belay given but no wrapper at %s, skipping Stop hook)"
              % (BELAY_WRAPPER or "<unset>"))
    return True


def main():
    paths = sys.argv[1:]
    if not paths:
        print("no settings.json paths given", file=sys.stderr)
        return 2
    for path in paths:
        process(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
