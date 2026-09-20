# filesearch: a per-user plocate index of `$HOME`

`find ~ -name '<something>'` walks the whole home directory. On a box with a
few hundred thousand files in it that is minutes of disk I/O for a question
an index answers in milliseconds. This component installs that index.

## What gets installed

| File | Goes to | What it does |
|---|---|---|
| `airlock-filesearch.service` | `~/.config/systemd/user/` | One `updatedb` run over `$HOME` into `~/.cache/plocate/home.db` |
| `airlock-filesearch.timer` | `~/.config/systemd/user/` | Fires hourly, with up to 120 s of jitter, and catches up after a reboot |

Nothing is installed outside `$HOME`, nothing needs root, and the system-wide
`/var/lib/plocate` database is untouched.

The index skips `.git`, `node_modules`, `.pnpm-store`, `__pycache__` and
`.venv`, which is most of the file count on a development box and none of the
files anyone searches for by name.

## Install

```bash
filesearch/install.sh
```

`--no-run` skips the first index build. `--no-systemd` installs nothing and
just builds the index once, for a machine with no systemd user session (WSL
without `systemd=true` in `/etc/wsl.conf`); there, schedule it yourself or
re-run the script when the index feels stale.

`plocate` itself is a system package and is the one thing this script will
not install for you:

```bash
sudo apt install plocate
```

## Use

```bash
plocate -d ~/.cache/plocate/home.db -i '<pattern>'
```

If this component is not installed, the guard falls back to plocate's own
`/var/lib/plocate/plocate.db` and suggests `plocate -i '<pattern>'`, which
indexes whatever `updatedb` was configured to index rather than `$HOME`
alone. With no plocate database at all, and on WSL with no `es` on PATH,
the guard suggests nothing and allows the crawl: a deny naming a tool the
machine does not have takes away the only command that would have worked.

Rebuild it on demand when a file made in the last hour is missing:

```bash
systemctl --user start airlock-filesearch.service
```

### Under WSL: this index only covers `$HOME`

`/mnt/<drive>` is a different filesystem, on the Windows host, and this
index never reaches it. A root under `/mnt/c/...` needs `es`, voidtools
Everything's client, on PATH under its bare name in WSL. A miss from
`plocate` on a `/mnt/<drive>` path means nothing; try `es` there instead.

## Tell the agent

An index nothing knows about is used by nothing. `CLAUDE.md.snippet` is the
paragraph to paste into the machine's `CLAUDE.md` or `AGENTS.md`; it says the
index exists, how to query it, what it prunes, and what to do when a
just-created file is not in it yet.
