# Migrating to airlock

The guard component has been renamed twice: it was `jev-guard`, then
`plumbline`, and is now `airlock`. The first rename was forced -- a public
project, [leepokai/jev-guard](https://github.com/leepokai/jev-guard), already
uses that name.

**Nothing breaks before you migrate.** `airlock/paths.py` resolves each
directory newest-name-first and then through every older name in turn --
`airlock`, else `plumbline`, else `jev-guard` -- so a machine whose
`~/.config/plumbline/mode` (or `~/.config/jev-guard/mode`) says `enforce` keeps
enforcing. All three hook paths work, all three package names import the same
objects, every `PLUMBLINE_*`/`JEV_GUARD_*`/`JEV_TUNE_*`/`JEV_HOME` variable is
still accepted, all three override stamps (`[airlock-ok: ...]`,
`[plumbline-ok: ...]`, `[jev-ok: ...]`) are honoured, and the kill-switch file
is honoured under any of the three config directories.

To make the new names the real ones:

```bash
install/migrate-to-airlock.sh --dry-run
install/migrate-to-airlock.sh
```

It moves the three directories, is idempotent, never merges two real
directories (it stops and says what it found), never deletes, and leaves both
old names behind as symlinks so anything not yet repointed keeps reading the
same files. It also prints the `git worktree repair` commands for the auto-tune
worktree that lives under the state directory, whose absolute paths `mv` does
not rewrite. Then repoint `settings.json` (`install/wire.sh --print`) and the
systemd units, and restart them.

`install/migrate-from-jev-guard.sh` still exists and is a thin wrapper around
the same script, so an older runbook keeps working.

## The key file is separate, and did not move

The `TYPESAFE_API_KEY` is read by every component in the kit, not only the
guard, so its default sits at `~/.config/jev-kit/env` rather than under the
guard's directory. The guard-era `~/.config/airlock/env` is still resolved,
straight after it, for good: **an existing install needs no action.** See
[install.md](install.md#the-api-key).
