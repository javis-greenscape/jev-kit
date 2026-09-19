#!/usr/bin/env python3
"""airlock: prove each installed piece actually runs, on NATIVE WINDOWS.

    py -3 install\\windows_doctor.py

The Linux doctor's rule applies here too: a file that exists is not a
capability that works. Nothing below checks that a file is present. Each
check RUNS something and reads what came back:

  - one REAL DENY and one REAL ALLOW through the actual hook process, in
    enforce mode, against a throwaway profile, so nothing touches the real
    shadow log or the real loop-protection state
  - a malformed payload, to prove the guard fails open
  - the kill switch, to prove it is a complete no-op
  - the Windows file-search steer: that a `dir /s C:\\` classifies as
    disk-wide and that the suggestion it would print names es.exe
  - voidtools Everything: is es.exe there, is the index running
  - the health check, end to end

Exit 0 when everything installed works, 1 otherwise. A component that is not
installed is reported `skip`, never `FAIL`. The daemon is reported `skip`
permanently: it is out of scope on Windows.
"""
import json
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import windows_common as wc  # noqa: E402

sys.path.insert(0, wc.REPO_ROOT)
from airlock import everything, mode as mode_mod, paths, policy, scope  # noqa: E402

DENY_PAYLOAD = {
    "session_id": "doctor-deny", "cwd": "C:\\", "tool_name": "Bash",
    "tool_input": {"command": "xdg-open https://example.com"},
}
ALLOW_PAYLOAD = {
    "session_id": "doctor-allow", "cwd": "C:\\", "tool_name": "Bash",
    "tool_input": {"command": "echo hello"},
}
SEARCH_COMMAND = "dir /s C:\\ *.xlsm"


class Report(object):
    def __init__(self):
        self.passed = 0
        self.failed = 0
        self.skipped = 0

    def ok(self, text):
        print("  PASS  %s" % text)
        self.passed += 1

    def bad(self, text):
        print("  FAIL  %s" % text)
        self.failed += 1

    def skip(self, text):
        print("  skip  %s" % text)
        self.skipped += 1

    def head(self, text):
        print("\n== %s" % text)


def run_hook(hook, python_exe, payload, env_extra, profile):
    """Pipe one fake PreToolUse event at the real hook process.

    A throwaway profile is pointed at with USERPROFILE, APPDATA and
    LOCALAPPDATA together, because those three are what airlock/paths.py
    resolves config, state and releases from on Windows. Setting only HOME,
    as the Linux doctor does, would reach none of them.
    """
    env = dict(os.environ)
    env.update({
        "USERPROFILE": profile,
        "APPDATA": os.path.join(profile, "AppData", "Roaming"),
        "LOCALAPPDATA": os.path.join(profile, "AppData", "Local"),
        "HOME": profile,
    })
    env.update(env_extra)
    for var in ("AIRLOCK_CONFIG_DIR", "AIRLOCK_STATE_DIR", "AIRLOCK_HOME",
                "AIRLOCK_KEY_FILE", "PLUMBLINE_CONFIG_DIR", "PLUMBLINE_STATE_DIR",
                "PLUMBLINE_HOME", "JEV_GUARD_CONFIG_DIR", "JEV_GUARD_STATE_DIR",
                "JEV_HOME"):
        env.pop(var, None)
    try:
        proc = subprocess.run(
            [python_exe, hook], input=json.dumps(payload), text=True,
            capture_output=True, timeout=30, env=env)
        return proc.returncode, (proc.stdout or "").strip(), (proc.stderr or "").strip()
    except Exception as exc:
        return 1, "", str(exc)[:300]


def check_hook(r, hook, python_exe):
    r.head("The hook process")
    if not hook or not os.path.isfile(hook):
        r.bad("no hook at %s -- run install\\windows_install.py" % hook)
        return
    if not python_exe:
        r.bad("no usable Python interpreter; set AIRLOCK_PYTHON")
        return

    profile = tempfile.mkdtemp(prefix="airlock-doctor-")
    try:
        code, out, err = run_hook(hook, python_exe, DENY_PAYLOAD,
                                  {"AIRLOCK_MODE": "enforce"}, profile)
        decision = None
        try:
            decision = json.loads(out)["hookSpecificOutput"]["permissionDecision"]
        except Exception:
            pass
        if decision == "deny":
            r.ok("deny: xdg-open blocked in enforce mode")
            try:
                reason = json.loads(out)["hookSpecificOutput"]["permissionDecisionReason"]
                print("        %s" % reason.splitlines()[0])
            except Exception:
                pass
        else:
            r.bad("deny: expected a deny for 'xdg-open', got rc=%s out=%r err=%r"
                  % (code, out[:200], err[:200]))

        code, out, err = run_hook(hook, python_exe, ALLOW_PAYLOAD,
                                  {"AIRLOCK_MODE": "enforce"}, profile)
        if out == "":
            r.ok("allow: 'echo hello' passes through silently, no output")
        else:
            r.bad("allow: expected no output, got %r" % out[:200])

        try:
            proc = subprocess.run([python_exe, hook], input="not json at all",
                                  text=True, capture_output=True, timeout=30)
            if proc.returncode == 0 and not (proc.stdout or "").strip():
                r.ok("fail-open: a malformed payload exits 0 with no output")
            else:
                r.bad("fail-open: expected rc=0 and no output, got rc=%s out=%r"
                      % (proc.returncode, (proc.stdout or "")[:200]))
        except Exception as exc:
            r.bad("fail-open: %s" % str(exc)[:200])

        code, out, err = run_hook(hook, python_exe, DENY_PAYLOAD,
                                  {"AIRLOCK_MODE": "enforce", "AIRLOCK_DISABLE": "1"},
                                  profile)
        if out == "":
            r.ok("kill switch: AIRLOCK_DISABLE=1 makes the hook a complete no-op")
        else:
            r.bad("kill switch: expected no output, got %r" % out[:200])
    finally:
        try:
            import shutil
            shutil.rmtree(profile, ignore_errors=True)
        except Exception:
            pass


def check_paths(r):
    r.head("Paths, mode and key")
    print("  config: %s" % paths.config_dir())
    print("  state:  %s" % paths.state_dir())
    print("  home:   %s" % paths.install_home())
    try:
        resolved = mode_mod.resolve_mode()
        r.ok("resolved mode: %s" % resolved)
        if resolved == "enforce":
            print("        armed: a deny now BLOCKS a tool call")
    except Exception as exc:
        r.bad("mode could not be resolved: %s" % str(exc)[:200])

    key_file = paths.config_dir() / "env"
    if os.path.isfile(str(key_file)):
        has_line = False
        try:
            with open(str(key_file)) as f:
                has_line = any(line.strip().startswith("TYPESAFE_API_KEY=")
                               or line.strip().startswith("export TYPESAFE_API_KEY=")
                               for line in f)
        except Exception:
            pass
        # Only ever whether the LINE is present. The value is never read here,
        # never printed, never logged.
        if has_line:
            r.ok("key file has a TYPESAFE_API_KEY line (value never read here)")
        else:
            r.bad("key file %s exists but has no TYPESAFE_API_KEY line" % key_file)
        from airlock.platform_compat import describe_permissions
        ok, text = describe_permissions(str(key_file))
        (r.ok if ok else r.bad)("key file privacy: %s" % text)
    else:
        r.skip("no key file at %s; the guard fails open and judges nothing" % key_file)


def check_file_search(r):
    r.head("File search (voidtools Everything)")
    result = scope.classify_command(SEARCH_COMMAND, str(paths.install_home()), windows=True)
    if result.get("scope") == "disk_wide" and result.get("program") == "dir":
        r.ok("scope: %r classifies as disk_wide" % SEARCH_COMMAND)
    else:
        r.bad("scope: %r classified as %r" % (SEARCH_COMMAND, result))

    suggestion = policy.filename_search_suggestion(windows=True)
    if "es.exe" in suggestion:
        r.ok("steer: the suggestion names es.exe")
        for line in suggestion.splitlines():
            print("        %s" % line)
    else:
        r.bad("steer: the suggestion does not name es.exe: %r" % suggestion)

    status = everything.status()
    if status["es_path"]:
        r.ok("es.exe found: %s" % status["es_path"])
    else:
        r.skip("es.exe not found; the steer would name a command this machine "
               "cannot run yet")
    if status["service"] is True:
        r.ok("the Everything index is running")
    elif status["service"] is False:
        r.bad("Everything is not running, so its index answers nothing")
    else:
        r.skip("could not tell whether Everything is running")
    if status["advice"]:
        for line in status["advice"].splitlines():
            print("        %s" % line)


def check_daemon(r):
    r.head("Warm daemon")
    r.skip("out of scope on Windows: it listens on a Unix domain socket, which "
           "Windows does not have.")
    print("        Every judgement is the direct HTTPS call instead: about")
    print("        0.9 s cold, against about 0.3 s through a warm daemon on Linux.")


def check_health(r, python_exe):
    r.head("Health check")
    release = wc.resolve_current()
    if not release:
        r.skip("nothing deployed yet, so no release to run the health check from")
        return
    if not python_exe:
        r.skip("no interpreter")
        return
    try:
        proc = subprocess.run([python_exe, "-m", "airlock.health"], cwd=release,
                              capture_output=True, text=True, timeout=60)
    except Exception as exc:
        r.bad("health check would not run: %s" % str(exc)[:200])
        return
    line = (proc.stdout or "").strip().splitlines()
    if not line:
        r.bad("health check printed nothing (rc=%s, %s)"
              % (proc.returncode, (proc.stderr or "")[:200]))
        return
    try:
        data = json.loads(line[-1])
    except Exception:
        r.bad("health check printed something that is not JSON: %r" % line[-1][:200])
        return
    status = data.get("status")
    if status in ("healthy", "degraded"):
        r.ok("health check ran: status=%s, daemon_supported=%s"
             % (status, data.get("daemon_supported")))
    else:
        r.bad("health check status=%s" % status)
    print("        %s" % json.dumps(data.get("last_hour", {})))


def check_wiring(r, launcher):
    r.head("settings.json wiring")
    found = False
    for path in wc.default_settings_files():
        if not os.path.isfile(path):
            r.skip("%s does not exist" % path)
            continue
        # Parse it rather than substring-matching the raw text: in JSON every
        # backslash of a Windows path is written as an escaped pair, so
        # `C:\Users\...` never appears literally in the file and a text
        # search for it always misses.
        try:
            with open(path) as f:
                data = json.load(f)
        except Exception as exc:
            r.bad("%s unreadable or not valid JSON: %s" % (path, str(exc)[:120]))
            continue
        commands = []
        for entries in (data.get("hooks") or {}).values():
            if not isinstance(entries, list):
                continue
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                for h in entry.get("hooks") or []:
                    if isinstance(h, dict) and isinstance(h.get("command"), str):
                        commands.append(h["command"])
        if launcher and any(launcher.lower() in c.lower() for c in commands):
            r.ok("%s registers the airlock hook at %s" % (path, launcher))
            found = True
        elif any("airlock" in c.lower() for c in commands):
            r.bad("%s registers an airlock hook, but not this launcher (%s). "
                  "Re-run the installer with --wire" % (path, launcher))
        else:
            r.skip("%s has no airlock hook (run the installer with --wire)" % path)
    if not found:
        print("        Not wired is a valid state: the installer never edits")
        print("        settings.json unless you pass --wire.")


def main(argv=None):
    if not wc.require_windows("install/windows_doctor.py"):
        return 2
    r = Report()
    release = wc.resolve_current()
    launcher = wc.launcher_path()
    hook = os.path.join(release, "hooks", "airlock.py") if release else None
    python_exe = wc.find_python()

    print("airlock doctor (native Windows)")
    print("  live release: %s" % (release or "NONE DEPLOYED"))
    print("  launcher:     %s" % launcher)
    print("  python:       %s" % (python_exe or "NONE FOUND"))

    check_hook(r, hook, python_exe)
    check_paths(r)
    check_file_search(r)
    check_daemon(r)
    check_health(r, python_exe)
    check_wiring(r, launcher if os.path.isfile(launcher) else None)

    print("\n%d passed, %d failed, %d skipped" % (r.passed, r.failed, r.skipped))
    return 1 if r.failed else 0


if __name__ == "__main__":
    sys.exit(main())
