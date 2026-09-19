#!/usr/bin/env python3
"""airlock: install the guard on NATIVE WINDOWS (no WSL).

    py -3 install\\windows_install.py --check-only
    py -3 install\\windows_install.py
    py -3 install\\windows_install.py --wire

What it installs is deliberately the CORE and nothing else: the PreToolUse
guard in shadow mode, the SessionStart session check, the rules table, the
key file, the file-search steer pointing at voidtools Everything, the health
check, this installer, the doctor and the uninstaller. Out of scope on
Windows, and not installed by anything here: the warm-connection daemon (it listens on a Unix domain
socket, which Windows does not have -- the client falls back to a direct
HTTPS call per judgement, about 0.9 s rather than about 0.3 s warm), belay,
compaction, the browser agent, code review and the tuning loop.

It never runs as Administrator, never writes outside the user's profile,
never touches the registry or PATH, and never edits a settings.json unless
you pass --wire. Without --wire it prints the exact JSON to add and stops.

Steps, in order:

  1. copy this checkout into %LOCALAPPDATA%\\airlock\\releases\\<stamp>
  2. point `current` at it -- a directory junction if the volume allows one,
     and a `current.txt` text pointer always (see install/windows_common.py
     for why both, and why settings.json points at neither)
  3. write the two stable launchers, %LOCALAPPDATA%\\airlock\\airlock-hook.py
     (the PreToolUse guard) and airlock-session-check.py (the SessionStart
     check that tells you when the guard has stopped judging)
  4. write the mode file: shadow. Arming is a separate, human decision
  5. with --wire: back up %USERPROFILE%\\.claude\\settings.json to a
     timestamped sibling, then add the PreToolUse hook and the SessionStart
     session check, in one edit and one backup
  6. with --schedule-health: register an hourly Task Scheduler health check
"""
import argparse
import json
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import windows_common as wc  # noqa: E402

sys.path.insert(0, wc.REPO_ROOT)
from airlock import everything, keyfile, paths  # noqa: E402


def parse_args(argv=None):
    ap = argparse.ArgumentParser(
        prog="windows_install.py",
        description="Install the airlock guard on native Windows.")
    ap.add_argument("--check-only", action="store_true",
                    help="print the plan and change nothing at all")
    ap.add_argument("--wire", action="store_true",
                    help="edit settings.json (backed up first). Off by default")
    ap.add_argument("--settings", action="append", default=[],
                    help="an extra settings.json to wire; repeatable")
    ap.add_argument("--schedule-health", action="store_true",
                    help="register an hourly health check with Task Scheduler")
    ap.add_argument("--no-session-check", action="store_true",
                    help="do not register the SessionStart session check "
                         "(it is part of the default set)")
    ap.add_argument("--source", default=wc.REPO_ROOT,
                    help="the checkout to deploy (default: this one)")
    return ap.parse_args(argv)


def plan(args):
    """Everything the install would do, as data, before any of it happens."""
    root = wc.install_root()
    python_exe = wc.find_python()
    git_bash = wc.find_git_bash()
    launcher = os.path.join(root, wc.LAUNCHER_NAME)
    session_launcher = os.path.join(root, wc.SESSION_LAUNCHER_NAME)
    session_check = not args.no_session_check
    return {
        "install_root": root,
        "releases_dir": os.path.join(root, wc.RELEASES_NAME),
        "launcher": launcher,
        "pointer": os.path.join(root, wc.POINTER_NAME),
        "config_dir": str(paths.config_dir()),
        "state_dir": str(paths.state_dir()),
        # The ONE resolver (airlock/keyfile.py), never a path spelled out
        # here: the kit default %APPDATA%\jev-kit\env, then the guard-era
        # %APPDATA%\airlock\env, then the pointer, then the legacy list.
        "key_file": keyfile.key_file(),
        "python": python_exe,
        "git_bash": git_bash,
        "shell": "bash (Git for Windows)" if git_bash else "powershell (no Git Bash found)",
        "hook_command": (wc.hook_command(python_exe, launcher, git_bash)
                         if python_exe else None),
        "session_launcher": session_launcher,
        "session_check": session_check,
        "session_command": (wc.hook_command(python_exe, session_launcher, git_bash)
                            if (python_exe and session_check) else None),
        "settings_files": (args.settings or wc.default_settings_files()),
        "mode": "shadow",
        "wire": bool(args.wire),
        "schedule_health": bool(args.schedule_health),
        "daemon": "not installed: out of scope on Windows (no Unix domain sockets)",
    }


def report_prerequisites(p):
    """Warnings, not failures. Every one of these is something the install
    can proceed without; it just would not be complete."""
    warnings = []
    if not p["python"]:
        warnings.append(
            "No usable Python interpreter found for the hook command. Install\n"
            "  Python from python.org (NOT the Microsoft Store stub) or set\n"
            "  AIRLOCK_PYTHON to an absolute python.exe/py.exe path.")
    if not p["git_bash"]:
        warnings.append(
            "Git for Windows was not found. Claude Code's setup documentation:\n"
            '  "Git for Windows is recommended on native Windows so Claude Code can\n'
            '  use the Bash tool. If Git for Windows is not installed, Claude Code\n'
            '  uses PowerShell as the shell tool instead." The guard covers both\n'
            "  tools, so this is fine -- but the hook command is written in the\n"
            "  PowerShell shape. Install Git for Windows LATER and you must re-run\n"
            "  this installer with --wire so the command is rewritten for bash.")
    key_file = p["key_file"]
    if not os.path.isfile(key_file):
        warnings.append(
            "No key file at %s.\n"
            "  Ask the human for a TYPESAFE_API_KEY and have THEM put one\n"
            "  `TYPESAFE_API_KEY=...` line in that file. Never print it, never\n"
            "  paste it into a session, never write it into a file you then show.\n"
            "  Without a key the guard still installs and still fails open: it\n"
            "  simply judges nothing, so only the code-only rules fire."
            % key_file)
    es = everything.status()
    if es["advice"]:
        warnings.append(es["advice"])
    return warnings, es


def do_install(args, p):
    actions = []
    root = p["install_root"]
    releases = p["releases_dir"]
    os.makedirs(releases, exist_ok=True)
    os.makedirs(p["config_dir"], exist_ok=True)
    os.makedirs(p["state_dir"], exist_ok=True)

    release_dir = os.path.join(releases, wc.stamp())
    if os.path.isdir(release_dir):
        shutil.rmtree(release_dir)
    wc.copy_release(args.source, release_dir)
    actions.append("copied %s -> %s" % (args.source, release_dir))

    ok, how = wc.make_junction(os.path.join(root, wc.CURRENT_NAME), release_dir)
    if ok:
        actions.append("current: directory junction -> %s" % release_dir)
    else:
        actions.append("current: no junction (%s); the text pointer carries it" % how)
    wc.write_pointer(root, release_dir)
    actions.append("current.txt -> %s" % release_dir)

    launcher = wc.write_launcher(root)
    actions.append("launcher written: %s" % launcher)
    if p["session_check"]:
        session_launcher = wc.write_session_launcher(root)
        actions.append("session-check launcher written: %s" % session_launcher)
    else:
        session_launcher = None
        actions.append("session check: skipped (--no-session-check)")

    mode_file = os.path.join(p["config_dir"], "mode")
    if not os.path.isfile(mode_file):
        with open(mode_file, "w") as f:
            f.write("shadow\n")
        actions.append("mode: shadow (%s)" % mode_file)
    else:
        with open(mode_file) as f:
            existing = (f.read().split() or ["shadow"])[0]
        actions.append("mode: left as it already was (%s in %s)" % (existing, mode_file))

    pruned = wc.prune_releases(releases, current=release_dir)
    for old in pruned:
        actions.append("pruned old release %s" % old)

    if args.wire:
        if not p["python"]:
            actions.append("WIRE SKIPPED: no interpreter to put in the hook command")
        else:
            for path, changed in wc.wire(
                    p["settings_files"], launcher, p["hook_command"],
                    session_hook_path=session_launcher,
                    session_hook_cmd=p["session_command"]):
                actions.append("settings.json %s: %s"
                               % (path, "edited (backed up first)" if changed else "no change"))
    else:
        actions.append("settings.json NOT touched (pass --wire). The entries to add:")
        entry = {"hooks": {"PreToolUse": [
            {"matcher": "*", "hooks": [{"type": "command",
                                        "command": p["hook_command"],
                                        "timeout": 5}]}]}}
        if p["session_check"]:
            entry["hooks"]["SessionStart"] = [
                {"matcher": "*", "hooks": [{"type": "command",
                                            "command": p["session_command"],
                                            "timeout": 5}]}]
        actions.append(json.dumps(entry, indent=2))

    if args.schedule_health:
        if not p["python"]:
            actions.append("TASK SKIPPED: no interpreter for the health check")
        else:
            ok, out = wc.create_health_task(p["python"], release_dir)
            actions.append("Task Scheduler '%s': %s%s"
                           % (wc.TASK_NAME, "created, hourly" if ok else "FAILED",
                              "" if ok else " (%s)" % out))
    else:
        actions.append("Task Scheduler: nothing registered (pass --schedule-health)")

    return actions, release_dir


def main(argv=None):
    args = parse_args(argv)
    if not wc.require_windows("install/windows_install.py"):
        return 2

    p = plan(args)
    warnings, _es = report_prerequisites(p)

    print("airlock: native Windows install")
    for key in ("install_root", "config_dir", "state_dir", "key_file",
                "python", "shell", "hook_command", "session_command", "mode",
                "daemon"):
        print("  %-14s %s" % (key + ":", p[key]))
    print("  %-14s %s" % ("settings:", ", ".join(p["settings_files"])))

    if warnings:
        print("\nPrerequisites to resolve:")
        for w in warnings:
            print("  - %s" % w.replace("\n", "\n  "))

    if args.check_only:
        print("\n--check-only: nothing was installed.")
        return 0

    print("\nInstalling:")
    actions, release_dir = do_install(args, p)
    for a in actions:
        print("  %s" % a)

    print("\nDone. Next:")
    print("  py -3 install\\windows_doctor.py        prove it actually runs")
    print("  It is in SHADOW mode: it logs what it would have done and blocks")
    print("  nothing. Arming it (enforce) is a separate, human decision:")
    print("      echo enforce > \"%s\"" % os.path.join(p["config_dir"], "mode"))
    print("  Release deployed: %s" % release_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
