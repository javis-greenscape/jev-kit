# Deploying from a release, not the development checkout

Today `settings.json` hooks, `airlock-daemon.service`, and `airlock-tune.service`
all point straight into a development checkout (`~/code/typesafe` or a
worktree of it), so every merge changes what a live session is running
mid-session. These three scripts separate "build a release" from "point
things at it", and neither of the first two ever edits a settings.json or a
systemd unit.

## `deploy.sh`

Run from any worktree of this repo. It finds the MAIN checkout via `git
worktree list`, never a hard-coded path, and takes its current `main` commit.
It runs the full unit test suite against a clean `git archive` export of that
commit rather than the possibly-dirty working tree.

Only then does it export to
`${AIRLOCK_HOME:-$HOME/.local/share/airlock}/releases/<shortsha>/` and flip
`$AIRLOCK_HOME/current` to point at it. The flip is atomic, a symlink rename on
the same filesystem, so nothing ever observes `current` missing or
half-written.

It keeps the newest 5 releases and never deletes the one `current` points at.
It prints the paths a settings.json hook and a systemd `WorkingDirectory=`
should reference.

```bash
AIRLOCK_HOME=$HOME/.local/share/airlock install/deploy.sh
```

## `rollback.sh`

Points `$AIRLOCK_HOME/current` at the release immediately before the one it
currently points at. Nothing else needs to change. Settings.json and the units
already reference `$AIRLOCK_HOME/current`, so a rollback takes effect on the
next tool call, or daemon restart, with no further edits.

```bash
install/rollback.sh
```

## `wire.sh`

Prints the edit with `--print`, or applies it with `--apply` after a
timestamped backup of each *existing* file. The edit wires a settings.json's
airlock `PreToolUse` hook at `$AIRLOCK_HOME/current/hooks/airlock.py`. It ADDS
the entry when none is there yet, since a first install has nothing to
repoint, and repoints an airlock/plumbline/jev_guard hook command that already
points somewhere else.

A settings.json that does not exist yet is created outright, with just the
entries this run adds. Every existing key and hook survives, and invalid JSON
is refused with no write. Settings.json paths are always given as explicit
arguments. This script never guesses which account trees exist on the box.

```bash
install/wire.sh --print  ~/.claude/settings.json ~/.claude-<account>/settings.json
install/wire.sh --apply  ~/.claude/settings.json ~/.claude-<account>/settings.json
```

Two flags add more than the `PreToolUse` entry, same idempotent rules:

```bash
install/wire.sh --apply --belay ~/.claude/settings.json            # also add the belay Stop hook
install/wire.sh --apply --function-hooks ~/.claude/settings.json   # also set the function-hooks env var
```

- `--belay` adds the `Stop` hook (matcher `"*"`, command
  `<HOME>/bin/airlock-belay-run`, timeout 25), but only when that wrapper file
  actually exists on this machine. Otherwise `wire.sh` says so and leaves
  `Stop` alone. `install/install.sh --wire` passes `--belay` automatically
  whenever `--belay` is one of the components selected for that run (it is
  in the installer's default set).
- `--function-hooks` sets `"env": {"CLAUDE_CODE_ENABLE_FUNCTION_HOOKS": "1"}`.

`--apply` was not run as part of this change, and neither script here touches
an installed systemd unit. That cutover is the owner's call, made once.

## No hard-coded paths

Nothing in these scripts assumes any particular user's home directory: `$AIRLOCK_HOME` derives from `$HOME` unless overridden, and the
main repo is found via `git worktree list` from the script's own location.

## Native Windows: `windows_install.py`, `windows_doctor.py`, `windows_uninstall.py`

The three scripts above are bash and are Linux-only. Their Windows
counterparts are Python, because a native Windows install must not require
Git for Windows to install a guard whose whole purpose is to run without
extra dependencies. Full guide:
[docs/INSTALL-WINDOWS.md](../docs/INSTALL-WINDOWS.md).

```cmd
py -3 install\windows_install.py --check-only
py -3 install\windows_install.py --wire
py -3 install\windows_doctor.py
py -3 install\windows_uninstall.py --check-only
```

Thin `.cmd` and `.ps1` launchers (`windows-install.cmd`,
`windows-doctor.ps1`, ...) do nothing but find a Python interpreter and pass
every argument through.

`windows_install.py` does deploy.sh's job and wire.sh's job in one pass, and
it reuses `_wire.py` for the settings.json edit (behind
`HOOK_COMMAND_QUOTED=1`, which selects the quoted Windows hook-command
shape). It does NOT use `deploy.sh`'s symlink, because Windows does not give
an ordinary user symlink privilege. So `current` is a directory junction when
the volume allows one, and a `current.txt` text pointer always, with
`settings.json` pointing at a stable launcher that follows whichever is
there. `install/windows_common.py` explains that decision in full.

Rollback on Windows is one line of `current.txt`; `rollback.sh` is not used.
