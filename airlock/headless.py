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
import time

#: The rule id and the action a headless machine wants it set to.
R6_RULE_ID = "R6-gui-or-browser"
R6_HEADLESS_ACTION = "deny"


def _run_loginctl():
    """stdout of `loginctl list-sessions --no-legend`, or None if unavailable.

    None means "no evidence", never "headless". Any failure -- binary absent,
    no logind, D-Bus unreachable, a hang -- returns None.

    `shutil` and `subprocess` are imported here, not at module top: this is
    the only code in this module that needs them, and headless.py is now
    imported unconditionally on the hook's hot path (for is_small_host /
    cpu_count) whether or not any rule ever fires. A module-level import of
    both cost enough to push an already-tight entry point over its 100ms
    budget (measured 91.5ms -> 120.5ms median; see rules.py's import of this
    module and airlock/tests/test_entry_point_is_fast)."""
    try:
        import shutil
        import subprocess
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
        import subprocess
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


# --- host capacity ------------------------------------------------------------

#: Cores at/below which a box counts as "small" for R3's uncapped-run warning.
#: Measured 2026-09-20: gs, the shared VPS R3's messages were written for, has
#: `nproc` = 4 (7GB RAM) and should keep warning; MasterRig, Jonathan's WSL
#: workstation, has `nproc` = 12 (15GB RAM) and should not. 6 leaves headroom
#: on both sides of that gap.
SMALL_HOST_CPU_THRESHOLD = 6


def _affinity_cpu_count():
    """CPUs this process may actually schedule on, per its affinity mask
    (`taskset`, a container's `--cpuset-cpus`). None when the platform has
    no `os.sched_getaffinity` (macOS, native Windows) or it fails."""
    try:
        return len(os.sched_getaffinity(0))
    except Exception:
        return None


def _proc_cgroup_relpaths(proc_cgroup="/proc/self/cgroup"):
    """(v2_relpath, v1_cpu_relpath) for THIS process, each "/" when unknown.

    A process is rarely in the root cgroup: under systemd it sits in a slice
    or a scope, and that is where a `CPUQuota=` lands. Reading only the mount
    root's `cpu.max` therefore sees `max` on a capped host and reports the
    machine as unconstrained.
    """
    v2 = v1 = "/"
    try:
        with open(proc_cgroup) as f:
            for line in f:
                parts = line.strip().split(":", 2)
                if len(parts) != 3:
                    continue
                hid, controllers, path = parts
                if hid == "0" and controllers == "":
                    v2 = path or "/"
                elif "cpu" in controllers.split(","):
                    v1 = path or "/"
    except Exception:
        pass
    return v2, v1


def _ancestor_dirs(root, relpath):
    """Every cgroup directory from `relpath` up to `root`, nearest first."""
    parts = [p for p in (relpath or "/").split("/") if p]
    out = []
    while True:
        out.append(os.path.join(root, *parts) if parts else root)
        if not parts:
            return out
        parts.pop()


def _cgroup_mount(cgroup_root, mountinfo, v2=True):
    """(mountpoint, mount_root) for the visible cgroup hierarchy, or None.

    `/proc/self/cgroup` gives a path in the HIERARCHY, which is not the path
    under the mount point when only a subtree is mounted -- a container
    without its own cgroup namespace sees mountinfo root `/docker/abc` at
    `/sys/fs/cgroup`, and its own `/docker/abc/child` lives at
    `/sys/fs/cgroup/child`. mountinfo's fields 4 and 5 are exactly that
    translation. For v1 this also finds the CPU controller wherever it is
    mounted, including the common `cpu,cpuacct` pairing.

    Only a mount at or under `cgroup_root` is accepted, so a test passing a
    temporary directory gets no match and the plain layout is used.
    """
    try:
        with open(mountinfo) as f:
            lines = f.readlines()
    except Exception:
        return None
    for line in lines:
        try:
            left, right = line.split(" - ", 1)
            fields = left.split()
            mount_root, mountpoint = fields[3], fields[4]
            rfields = right.split()
            fstype, super_opts = rfields[0], rfields[-1]
        except Exception:
            continue
        if mountpoint != cgroup_root and not mountpoint.startswith(
                cgroup_root.rstrip("/") + "/"):
            continue
        if v2:
            if fstype == "cgroup2":
                return mountpoint, mount_root
        elif fstype == "cgroup" and "cpu" in super_opts.split(","):
            return mountpoint, mount_root
    return None


def _mount_relative(cgroup_path, mount_root):
    """`cgroup_path` expressed relative to a mount whose root is
    `mount_root`, or None when the path is outside that subtree."""
    mount_root = mount_root or "/"
    if mount_root == "/":
        return cgroup_path or "/"
    if cgroup_path == mount_root:
        return "/"
    if cgroup_path.startswith(mount_root.rstrip("/") + "/"):
        return cgroup_path[len(mount_root.rstrip("/")):]
    return None


def _read_quota_count(directory, v2=True):
    """CPUs implied by one cgroup directory's quota, or None for no limit."""
    try:
        if v2:
            with open(os.path.join(directory, "cpu.max")) as f:
                quota_str, period_str = f.read().split()
            if quota_str == "max":
                return None
            quota, period = int(quota_str), int(period_str)
        else:
            with open(os.path.join(directory, "cpu.cfs_quota_us")) as f:
                quota = int(f.read().strip())
            with open(os.path.join(directory, "cpu.cfs_period_us")) as f:
                period = int(f.read().strip())
    except Exception:
        return None
    if quota <= 0 or period <= 0:
        return None
    return max(1, -(-quota // period))  # ceil division, no float/math import


def _cgroup_cpu_quota_count(cgroup_root="/sys/fs/cgroup",
                            proc_cgroup="/proc/self/cgroup",
                            mountinfo="/proc/self/mountinfo"):
    """CPUs implied by a cgroup CPU quota (a container's `--cpus=N` or a
    systemd `CPUQuota=`, neither of which an affinity mask sees).

    Walks this process's own cgroup and every ancestor up to the mount point,
    in both the v2 layout (`cpu.max`) and the v1 one (`cpu.cfs_quota_us` /
    `cpu.cfs_period_us`), and returns the TIGHTEST limit found, since an
    ancestor's cap binds its descendants. Hierarchy paths are translated
    through mountinfo where it is readable. Absent, `max`, unreadable or
    non-positive all mean "no evidence", never a count.
    """
    v2_path, v1_path = _proc_cgroup_relpaths(proc_cgroup)
    counts = []
    for path, v2, fallback_base in (
            (v2_path, True, cgroup_root),
            (v1_path, False, os.path.join(cgroup_root, "cpu"))):
        mount = _cgroup_mount(cgroup_root, mountinfo, v2=v2)
        if mount:
            base, rel = mount[0], _mount_relative(path, mount[1])
            if rel is None:
                continue
        else:
            base, rel = fallback_base, path
        for directory in _ancestor_dirs(base, rel):
            n = _read_quota_count(directory, v2=v2)
            if n:
                counts.append(n)
    return min(counts) if counts else None


def cpu_count(override=None):
    """Number of CPUs available to this process.

    `override` is the injection point every caller forwards, matching
    `is_wsl`'s `env`/`proc_version` arguments: pass an int to force the
    answer in a test. Otherwise the SMALLEST of: the affinity mask, a
    cgroup CPU quota, and `os.cpu_count()` -- a host's logical core count
    overstates what a constrained process (a container capped at `--cpus`,
    a taskset job) can actually use, which is exactly the case
    is_small_host() below exists to catch. Falls back to 2 (a conservative
    small number) if none of the three can tell."""
    if override is not None:
        return override
    candidates = []
    n = _affinity_cpu_count()
    if n:
        candidates.append(n)
    n = _cgroup_cpu_quota_count()
    if n:
        candidates.append(n)
    try:
        n = os.cpu_count()
    except Exception:
        n = None
    if n:
        candidates.append(n)
    return min(candidates) if candidates else 2


def is_small_host(cpus=None):
    """True when this machine has few enough cores that an uncapped test
    suite or build genuinely contends with everything else running on it.

    `cpus` is the injection point for tests: pass an int to force the core
    count instead of detecting it. See `SMALL_HOST_CPU_THRESHOLD`."""
    n = cpus if cpus is not None else cpu_count()
    return n <= SMALL_HOST_CPU_THRESHOLD


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
