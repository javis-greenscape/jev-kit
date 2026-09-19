"""Resolve which git checkout the unattended tuning loop treats as "the repo".

Why this exists
================

`tuning/tune.sh`, `tuning/tune.py` and `tuning/promote.sh` all need a real git
checkout with a `main` branch and a `.git`: the tuning loop keeps an
`auto-tune` branch in a worktree of it, and `promote.sh` fast-forwards its
`main` from that worktree. The systemd unit, though, runs the DEPLOYED
release at `$AIRLOCK_HOME/current/tuning/tune.sh` -- a plain exported
directory tree with no `.git` at all, because `install/deploy.sh` exports a
commit rather than cloning it. Deriving "the repo" from the running script's
own location (as tune.sh used to) works only by accident, on a machine that
never deployed a release and still runs tuning straight out of a checkout.

RESOLUTION ORDER -- the one place it is written down
=====================================================

There is one implementation per language and no third copy: this module for
Python (`tune.py`) and `install/repo-path.sh` for the shell (`tune.sh`,
`promote.sh`). Change the order here and there, together, or not at all.

  1. `AIRLOCK_TUNE_REPO` in the environment -- an explicit override, honoured
     as given, no trust checks (the operator already chose it).
  2. the path recorded in the POINTER FILE `$AIRLOCK_CONFIG_DIR/repo.path`,
     if the pointer passes the checks below.
  3. the resolving script's own parent directory, if IT is a git checkout
     (has a `.git`).
  4. otherwise `None`. Tuning is optional: every caller logs one clear line
     and exits 0 rather than failing a timer noisily.

Why a pointer file at all
-------------------------

`install/install.sh` runs from a real checkout and knows which one it was run
from; a deployed release under `$AIRLOCK_HOME/current` does not, because it is
an exported tree, not a clone. So the installer records the checkout's PATH
(never anything else) into `$AIRLOCK_CONFIG_DIR/repo.path`, and step 2 above
reads it.

That makes the pointer a security-relevant file, exactly like
`keyfile.path`: whoever can write it chooses which git checkout the tuning
loop commits to and which `main` `promote.sh` fast-forwards. So it is trusted
only when:

  * the pointer file is a regular file (not a symlink, not a fifo), owned by
    the current uid, and not group- or world-writable;
  * its directory is owned by the current uid and not group- or
    world-writable (a writable directory means the pointer can be replaced
    wholesale);
  * the recorded path is ABSOLUTE after `~` expansion;
  * the recorded path is an existing directory containing a `.git`.

Any check failing means the pointer is ignored and resolution carries on at
step 3, exactly as if the pointer did not exist. Nothing raises, nothing
blocks. The reason is recorded in `pointer_diagnostics()` (path names only,
nothing sensitive) so `install/doctor.sh` can say why a pointer is not being
honoured.
"""
import os
import stat

from . import paths

# A one-line file holding the PATH of the tuning repo checkout, never its
# contents.
POINTER_FILE = "repo.path"

ENV_VAR = "AIRLOCK_TUNE_REPO"

# Group- or world-writable bits. Either on a pointer file (or its directory)
# means somebody other than the owner can choose which checkout we resolve.
_WRITABLE_BY_OTHERS = stat.S_IWGRP | stat.S_IWOTH

_DIAGNOSTICS = []


def _note(message):
    """Record why the pointer was refused or is worth mentioning. Paths only,
    never file contents, so this is always safe to print."""
    try:
        if message not in _DIAGNOSTICS:
            _DIAGNOSTICS.append(message)
        if os.environ.get("AIRLOCK_DEBUG") == "1":
            import sys
            sys.stderr.write("airlock repo_path: %s\n" % message)
    except Exception:
        pass


def pointer_diagnostics():
    """Everything _note() has recorded in this process, oldest first."""
    return tuple(_DIAGNOSTICS)


def reset_diagnostics():
    del _DIAGNOSTICS[:]


def pointer_file_path():
    """Absolute path of the pointer file, whether or not it exists, or None if
    even the config directory cannot be resolved. Never raises."""
    try:
        return str(paths.config_file(POINTER_FILE))
    except Exception:
        return None


def _owned_and_unwritable(st, what, where):
    """True iff `st` is owned by this uid and not group/world writable."""
    try:
        if st.st_uid != os.getuid():
            _note("ignoring pointer: %s %s is owned by uid %d, not %d"
                  % (what, where, st.st_uid, os.getuid()))
            return False
        if st.st_mode & _WRITABLE_BY_OTHERS:
            _note("ignoring pointer: %s %s is group- or world-writable (mode %o)"
                  % (what, where, stat.S_IMODE(st.st_mode)))
            return False
    except Exception:
        return False
    return True


def is_git_checkout(directory):
    """True iff `directory` exists and contains a `.git` (dir, for an
    ordinary checkout, or file, for a linked worktree)."""
    try:
        return os.path.exists(os.path.join(directory, ".git"))
    except Exception:
        return False


def pointer_target(check=True):
    """The path recorded in the pointer file, or None.

    With `check` true (the default, and what resolution uses) every trust
    check in the module docstring must pass. With `check` false the recorded
    path is returned whether or not it is trustworthy. Never raises.
    """
    try:
        pointer = pointer_file_path()
    except Exception:
        return None
    if not pointer:
        return None
    try:
        st = os.lstat(pointer)
    except Exception:
        return None

    if check:
        if not stat.S_ISREG(st.st_mode):
            _note("ignoring pointer: %s is not a regular file" % pointer)
            return None
        if not _owned_and_unwritable(st, "pointer file", pointer):
            return None
        parent = os.path.dirname(pointer) or "."
        try:
            dir_st = os.lstat(parent)
        except Exception:
            return None
        if not _owned_and_unwritable(dir_st, "pointer directory", parent):
            return None

    try:
        with open(pointer, "r") as f:
            recorded = f.readline().strip()
    except Exception:
        return None
    if not recorded or recorded.startswith("#"):
        return None
    try:
        target = os.path.expanduser(recorded)
    except Exception:
        return None
    if not target:
        return None

    if check:
        if not os.path.isabs(target):
            _note("ignoring pointer: recorded path %r is not absolute" % target)
            return None
        if not os.path.isdir(target):
            _note("pointer names %s, which is not an existing directory; falling back" % target)
            return None
        if not is_git_checkout(target):
            _note("ignoring pointer: %s is not a git checkout (no .git)" % target)
            return None
    return target


def resolve_repo(script_dir):
    """The repository the tuning loop should operate on, or None.

    `script_dir` is the directory the caller (tune.py / tune.sh / promote.sh)
    itself lives in -- passed in rather than derived here, so this stays a
    pure function the tests can drive with a tempdir.

    Order: AIRLOCK_TUNE_REPO env (honoured as given, no checks) -> the
    repo.path pointer (checked) -> script_dir's own parent, if that is a git
    checkout -> None.
    """
    override = os.environ.get(ENV_VAR)
    if override:
        try:
            return os.path.expanduser(override)
        except Exception:
            return override

    pointed = pointer_target()
    if pointed:
        return pointed

    try:
        self_repo = os.path.dirname(os.path.abspath(str(script_dir)))
    except Exception:
        return None
    if is_git_checkout(self_repo):
        return self_repo

    return None


def record_repo_path(config_dir, repo_dir):
    """Write the pointer file. Path only, mode 600, called by the installer
    (which already knows the checkout it was run from). Never raises; returns
    True on success."""
    try:
        pointer = os.path.join(str(config_dir), POINTER_FILE)
        fd = os.open(pointer, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(fd, "w") as f:
                f.write(str(repo_dir) + "\n")
        finally:
            pass
        os.chmod(pointer, 0o600)
        return True
    except Exception:
        return False
