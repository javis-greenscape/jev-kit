# belay: the jev-belay Stop hook, wrapped

[valentynkit/jev-belay](https://github.com/valentynkit/jev-belay), pinned at
commit `98f39e0`. On the Claude Code **Stop** event it looks at the last
turn's mutations and check commands, and -- only if there is evidence of a
change with no fresh passing check -- asks TypeSafe's Jev model four
questions about whether the final message overclaims completion. See
`docs/community-vetting.md` for the full writeup: **ADOPT AS IS**, scoped to
one account at a time.

What it sends off the machine, per the vetting report: the task text, the
final assistant message, and check command lines, all run through a 13-rule
secret redactor first, capped at 1,500 + 2,000 characters. No diffs, no file
contents, no tool inputs.

## What gets installed

| Item | Goes to | What it does |
|---|---|---|
| a pinned clone | `~/.local/share/jev-belay/releases/<sha>/` | The upstream source at commit `98f39e0` |
| `current` symlink | `~/.local/share/jev-belay/current` | Flipped atomically at the release; re-pinning is cloning a new sha and re-flipping |
| `run.sh`, installed | `$HOME/bin/airlock-belay-run` | The wrapper the Stop hook actually calls |

Nothing is added to any `settings.json`. `install.sh` prints the block to add
and stops there -- the same posture as the rest of this repository.

## Install

```bash
belay/install.sh
```

Then add the printed Stop hook to **one** account's `settings.json` --
`~/.claude*/settings.json`, whichever account you are testing with -- and set
`JEV_BELAY_LOG=1` (the wrapper already sets it). Read
`~/.claude/belay/decisions.jsonl` for a week before trusting a block, and
before adding it to any other account.

## The key

`run.sh` never hard-codes a key-file path: it reads `AIRLOCK_KEY_FILE`, the
same variable `install/config.env` sets for every other component in this
repository, defaulting to `~/.config/airlock/env`. With no key loadable it
fails open -- exits 0, does nothing -- exactly like the guard.

## Absolute paths, always

Claude Code hook commands do not expand `~`. If a hook command contains it,
the hook silently never runs -- no error, just nothing happening. That is why
`install.sh` prints an absolute path (`$HOME/bin/airlock-belay-run`
resolved, not written literally as `~/bin/...`) and why the wrapper itself
resolves its own directory with `$(cd "$(dirname ...)" && pwd)` rather than
assuming where it was called from.

## Re-pinning

Edit `UPSTREAM_COMMIT` in `belay/install.sh` and re-run it. The old release
under `releases/<old-sha>/` is left in place; nothing here deletes a release,
so rolling back is flipping `current` back by hand:

```bash
ln -sfn ~/.local/share/jev-belay/releases/<old-sha> ~/.local/share/jev-belay/current
```

## Uninstall

Remove the Stop hook entry from the account's `settings.json`, then delete
`$HOME/bin/airlock-belay-run` and `~/.local/share/jev-belay/`. Nothing else
on the machine references it.
