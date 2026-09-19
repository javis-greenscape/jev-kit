"""Detect voidtools Everything and its command-line client. Never install it.

Everything is the Windows counterpart of the `plocate` index airlock installs
on Linux: it keeps a live index of every NTFS volume, so a filename question
it answers is instant where a `dir /s` or a `Get-ChildItem -Recurse` is a
crawl of the whole disk. That is the same argument `filesearch/` makes on
Linux, with one difference that matters: on Linux airlock BUILDS the index
(an `updatedb` run and a user timer). On Windows it does not, and must not.
Everything is third-party software with its own installer and its own service;
installing or configuring somebody's software is not a guard's job. So this
module only ever answers three questions:

  1. is `es.exe`, the command-line client, on this machine and where;
  2. is the Everything service or process actually running, so the index is
     live rather than merely installed;
  3. if either is missing, what does the human have to do about it.

Two separate downloads are involved and conflating them is the usual
confusion: the Everything application (the GUI and the service, which is what
maintains the index) and the ES command-line client (a single `es.exe`, a
separate download). A machine can perfectly well have the first and not the
second, which is exactly what the machine this was tested on had.

A WSL session on the same physical machine needs Everything too: `plocate`
only ever indexes `$HOME` on the Linux side, so a root under `/mnt/<drive>`
has no Linux index answering for it at all, and `es` (the same client,
reached under its bare name on PATH in WSL) is the only thing that can.

Every function takes injectable `env` and `runner` arguments, so the whole
module is unit tested on Linux with no Windows machine involved.
"""
import os
import subprocess

# An explicit override, for a machine that keeps es.exe somewhere of its own.
ES_PATH_ENV = ("AIRLOCK_ES_PATH", "ES_PATH")

ES_EXE = "es.exe"

# Where the two downloads put themselves, most likely first. Everything 1.5
# uses a versioned directory name, hence the two spellings.
_INSTALL_SUBDIRS = (
    ("ProgramFiles", "Everything"),
    ("ProgramFiles", "Everything 1.5a"),
    ("ProgramFiles(x86)", "Everything"),
    ("ProgramFiles(x86)", "Everything 1.5a"),
    ("LOCALAPPDATA", "Programs\\Everything"),
    ("LOCALAPPDATA", "Programs\\ES"),
    ("USERPROFILE", "bin"),
    ("USERPROFILE", "tools"),
)

DOWNLOAD_ADVICE = (
    "Everything's command-line client `es.exe` was not found.\n"
    "  It is a SEPARATE download from the Everything application itself: the\n"
    "  application maintains the index, `es.exe` is what queries it from a\n"
    "  command line. Get both from https://www.voidtools.com/ (the ES client is\n"
    "  under Downloads > Command-line Interface), put es.exe anywhere on PATH,\n"
    "  and re-run the doctor. Set AIRLOCK_ES_PATH to its full path instead if\n"
    "  you would rather not change PATH.\n"
    "  airlock will not install it for you: it is third-party software with its\n"
    "  own service, and installing somebody's software is not a guard's job."
)

SERVICE_ADVICE = (
    "Everything is installed but its index does not appear to be running.\n"
    "  Start the Everything application (or its service) once; it builds the\n"
    "  index in the background and keeps it live from then on. Until it is\n"
    "  running, `es.exe` returns nothing and the file-search steer would send\n"
    "  a session to a tool that answers nothing."
)


def _env(env=None):
    return os.environ if env is None else env


def _exists(path):
    try:
        return bool(path) and os.path.isfile(path)
    except Exception:
        return False


def candidate_paths(env=None, pathsep=None):
    """Every place es.exe is looked for, in order. Pure: touches no disk.

    `pathsep` defaults to os.pathsep, which is ";" on Windows where this runs
    for real. It is a parameter because the unit tests run on Linux, where
    os.pathsep is ":" and a Windows PATH entry such as `C:\bin` would be
    split down the middle at the drive letter.
    """
    env = _env(env)
    pathsep = os.pathsep if pathsep is None else pathsep
    out = []
    for var in ES_PATH_ENV:
        value = env.get(var)
        if value:
            out.append(value)
    for entry in (env.get("PATH") or "").split(pathsep):
        entry = entry.strip().strip('"')
        if entry:
            out.append(os.path.join(entry, ES_EXE))
    for var, subdir in _INSTALL_SUBDIRS:
        base = env.get(var)
        if base:
            out.append(os.path.join(base, subdir, ES_EXE))
    return out


def find_es(env=None, exists=None, pathsep=None):
    """Full path to es.exe, or None. `exists` is injectable for tests."""
    exists = exists or _exists
    for candidate in candidate_paths(env, pathsep):
        if exists(candidate):
            return candidate
    return None


def _run(runner, argv):
    if runner is not None:
        return runner(argv)
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=10)
        return proc.returncode, (proc.stdout or "") + (proc.stderr or "")
    except Exception as exc:
        return 1, str(exc)[:200]


def service_running(runner=None, env=None):
    """Is the Everything index live?

    Two probes, cheapest first. `sc query Everything` answers for the service
    install; a per-user install has no service, so `tasklist` for
    Everything.exe is the fallback. Returns True, False, or None when neither
    probe could be run at all (which is what happens on Linux, and is
    reported as "unknown" rather than as "no").
    """
    code, out = _run(runner, ["sc", "query", "Everything"])
    text = (out or "").lower()
    if code == 0 and "running" in text:
        return True
    reachable = code == 0 or "1060" in text or "does not exist" in text

    code, out = _run(runner, ["tasklist", "/FI", "IMAGENAME eq Everything.exe", "/NH"])
    text = (out or "").lower()
    if code == 0 and "everything.exe" in text:
        return True
    if code == 0 or reachable:
        return False
    return None


def status(env=None, runner=None, exists=None, pathsep=None):
    """One dict the doctor and the installer both print.

    {"es_path": str|None, "service": True|False|None, "ok": bool,
     "advice": str|None}
    """
    es_path = find_es(env=env, exists=exists, pathsep=pathsep)
    service = service_running(runner=runner, env=env)
    advice = None
    if es_path is None:
        advice = DOWNLOAD_ADVICE
    elif service is False:
        advice = SERVICE_ADVICE
    return {
        "es_path": es_path,
        "service": service,
        "ok": bool(es_path) and service is not False,
        "advice": advice,
    }
