"""Is this machine headless, and R6's one entry in rules.json.

R6 (`R6-gui-or-browser`) blocks `xdg-open`, `wslview`, `explorer.exe` and the
browser binaries. It is `off` by default on every platform, because most
people run Claude Code on a machine that has a desktop and opening a browser
there is an ordinary thing to do. A machine that really is headless -- this
kit's own cloud server, a CI runner, a Server Core box -- turns it on with one
entry in `~/.config/airlock/rules.json`:

    {"R6-gui-or-browser": "deny"}

`install/install.sh` writes exactly that entry when `detect_headless` says the
machine is headless, and `--headless` / `--no-headless` force either answer.

The detection is deliberately CONSERVATIVE, because the two errors are not
symmetric. Calling a desktop machine "headless" arms a rule that will block a
thing the user can legitimately do, on a box where nobody expects a guard to
have an opinion about browsers; calling a headless machine "desktop" merely
leaves R6 off, which is the shipped default anyway and costs one documented
command to correct. So every uncertainty resolves to "not headless".

What has to be true, all of it:

  - the platform is Linux. macOS is a desktop operating system and native
    Windows says nothing useful in these variables; both answer "not headless".
  - `DISPLAY` is unset or empty, and so is `WAYLAND_DISPLAY`.
  - it is not WSL. A WSL distribution with no `DISPLAY` still reaches a
    Windows desktop through WSLg or `explorer.exe`, so it is a desktop
    machine wearing a Linux hat. `/proc/version` naming Microsoft, or
    `WSL_DISTRO_NAME` / `WSL_INTEROP` being set, is enough.
  - `XDG_SESSION_TYPE` does not say `x11` or `wayland`.
  - if `loginctl` exists AND answers, no session of type `x11`/`wayland` or
    class `user` with a seat is present. A `loginctl` that is missing, times
    out, errors or prints nothing is NO evidence in either direction, and the
    other checks decide -- it is an extra way to say "desktop", never the
    only reason to say "headless".

Every function takes its inputs by argument so the whole matrix is exercised
from the Linux test suite with no machine of the relevant kind: `env` is the
environment mapping, `system` the `platform.system()` string, `proc_version`
the text of /proc/version, and `loginctl` a callable returning its stdout (or
None for "not available").
"""
import json
import os
import shutil
import subprocess
import time

#: The rule id and the action a headless machine wants it set to.
R6_RULE_ID = "R6-gui-or-browser"
R6_HEADLESS_ACTION = "deny"


def _run_loginctl():
    """stdout of `loginctl list-sessions --no-legend`, or None if unavailable.

    None means "no evidence", never "headless". Any failure -- binary absent,
    no logind, D-Bus unreachable, a hang -- returns None."""
    try:
        if not shutil.which("loginctl"):
            return None
        out = subprocess.run(
            ["loginctl", "list-sessions", "--no-legend"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=5,
        )
        if out.returncode != 0:
            return None
        return out.stdout.decode("utf-8", "replace")
    except Exception:
        return None


def _loginctl_show(session_id):
    try:
        out = subprocess.run(
            ["loginctl", "show-session", session_id, "-p", "Type", "-p", "Class", "-p", "Seat"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=5,
        )
        if out.returncode != 0:
            return ""
        return out.stdout.decode("utf-8", "replace")
    except Exception:
        return ""


def loginctl_says_desktop(sessions_text, show=None):
    """True when logind reports a graphical or seated session.

    `sessions_text` is the output of `loginctl list-sessions --no-legend`;
    `show` is a callable session_id -> the output of `loginctl show-session`.
    Returns False for None/empty input, which is the "no evidence" answer."""
    if not sessions_text:
        return False
    show = show or _loginctl_show
    for line in sessions_text.splitlines():
        parts = line.split()
        if not parts:
            continue
        sid = parts[0]
        # The list output itself carries a SEAT column on most versions. A
        # session sitting on seat0 is somebody at a physical screen.
        if any(p.startswith("seat") for p in parts[1:]):
            return True
        try:
            props = show(sid)
        except Exception:
            props = ""
        for line2 in (props or "").splitlines():
            k, _, v = line2.partition("=")
            if k.strip() == "Type" and v.strip().lower() in ("x11", "wayland", "mir"):
                return True
            if k.strip() == "Seat" and v.strip():
                return True
    return False


def is_wsl(env=None, proc_version=None):
    """True inside a WSL distribution, which reaches a Windows desktop."""
    env = os.environ if env is None else env
    if env.get("WSL_DISTRO_NAME") or env.get("WSL_INTEROP") or env.get("WSLENV"):
        return True
    if proc_version is None:
        try:
            with open("/proc/version", "r") as f:
                proc_version = f.read()
        except Exception:
            proc_version = ""
    return "microsoft" in (proc_version or "").lower()


def detect_headless(env=None, system=None, proc_version=None, loginctl=None):
    """(headless: bool, reason: str) for THIS machine, conservatively.

    `loginctl` is a callable returning the `list-sessions` output, or None for
    "do not consult logind". Pass `loginctl=lambda: None` to simulate a box
    where the binary is absent. Never raises."""
    try:
        env = os.environ if env is None else env
        if system is None:
            import platform as _platform
            system = _platform.system()
        system = (system or "").lower()

        if system != "linux":
            return (False, "not Linux (platform.system() = %s); R6 stays off"
                    % (system or "unknown"))

        if is_wsl(env=env, proc_version=proc_version):
            return (False, "WSL: a Windows desktop is one `explorer.exe` away")

        for var in ("DISPLAY", "WAYLAND_DISPLAY"):
            if (env.get(var) or "").strip():
                return (False, "$%s is set, so there is a display" % var)

        stype = (env.get("XDG_SESSION_TYPE") or "").strip().lower()
        if stype in ("x11", "wayland", "mir"):
            return (False, "XDG_SESSION_TYPE=%s, so there is a display" % stype)

        if loginctl is None:
            loginctl = _run_loginctl
        try:
            sessions = loginctl()
        except Exception:
            sessions = None
        if sessions is None:
            return (True, "Linux, no $DISPLAY, no $WAYLAND_DISPLAY, not WSL; "
                          "loginctl unavailable, so it was not consulted")
        if loginctl_says_desktop(sessions):
            return (False, "loginctl reports a graphical or seated session")
        return (True, "Linux, no $DISPLAY, no $WAYLAND_DISPLAY, not WSL, and "
                      "loginctl reports no graphical or seated session")
    except Exception as exc:  # never let detection break an install
        return (False, "detection failed (%s); assuming a desktop" % exc.__class__.__name__)


# --- the rules.json merge ----------------------------------------------------

def merge_rule(path, rule_id=R6_RULE_ID, action=R6_HEADLESS_ACTION, backup=True):
    """Set one rule id in a rules.json, preserving everything else.

    Returns (status, detail):
      "already"   the file already sets this rule id; NOTHING was written,
                  whatever the value. A user's explicit choice always wins,
                  including an explicit "off".
      "written"   the entry was added (file created, or merged into the
                  existing object); detail is the backup path or "".
      "error"     could not write; detail is the reason.

    A file that exists but is not JSON, or whose JSON is not an object, is
    never rewritten -- overwriting it would lose whatever the user meant by
    it. The nested `{"rules": {...}}` shape airlock/rules.py also accepts is
    preserved when that is the shape already on disk."""
    data = None
    existed = os.path.exists(path)
    if existed:
        try:
            with open(path, "r") as f:
                text = f.read()
        except Exception as exc:
            return ("error", "cannot read %s (%s)" % (path, exc.__class__.__name__))
        if text.strip():
            try:
                data = json.loads(text)
            except Exception:
                return ("error", "%s is not valid JSON; not touching it" % path)
            if not isinstance(data, dict):
                return ("error", "%s is not a JSON object; not touching it" % path)
        else:
            data = {}
    if data is None:
        data = {}

    nested = isinstance(data.get("rules"), dict)
    table = data["rules"] if nested else data
    if rule_id in table:
        return ("already", "%s is already set to %r in %s"
                % (rule_id, table[rule_id], path))

    table[rule_id] = action
    if nested:
        data["rules"] = table
    else:
        data = table

    backup_path = ""
    try:
        if existed and backup:
            backup_path = "%s.bak.%s" % (path, time.strftime("%Y%m%d-%H%M%S"))
            with open(path, "rb") as src, open(backup_path, "wb") as dst:
                dst.write(src.read())
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)
        tmp = path + ".tmp.%d" % os.getpid()
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2, sort_keys=True)
            f.write("\n")
        os.replace(tmp, path)
        try:
            os.chmod(path, 0o600)
        except Exception:
            pass
    except Exception as exc:
        return ("error", "cannot write %s (%s)" % (path, exc.__class__.__name__))
    return ("written", backup_path)


def main(argv=None):
    """`python3 -m airlock.headless detect|merge <path>`, for install.sh.

    detect: prints the reason on stdout, exits 0 when headless, 1 when not.
    merge:  prints "<status>\t<detail>" and exits 0 unless the status is
            "error"."""
    import sys
    argv = list(sys.argv[1:] if argv is None else argv)
    cmd = argv[0] if argv else "detect"
    if cmd == "detect":
        headless, reason = detect_headless()
        sys.stdout.write(reason + "\n")
        return 0 if headless else 1
    if cmd == "merge":
        if len(argv) < 2:
            sys.stderr.write("usage: merge <rules.json path>\n")
            return 2
        status, detail = merge_rule(argv[1])
        sys.stdout.write("%s\t%s\n" % (status, detail))
        return 1 if status == "error" else 0
    sys.stderr.write("usage: detect | merge <path>\n")
    return 2


if __name__ == "__main__":
    import sys
    sys.exit(main())
