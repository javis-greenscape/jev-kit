# claude-update: update Claude Code only when nobody is using it

`npm install -g` replacing the package on disk does not disturb a `claude`
process already running, but it is still worth not doing while a session is
mid-thought. This component runs an hourly check and only updates when the
machine looks idle.

"In use" means either of:

- a headless `claude -p` / `claude --print` run is alive (`pgrep`), or
- any session transcript under `$HOME/.claude/projects` has changed in the
  last `CLAUDE_UPDATE_IDLE_MIN` minutes (default 30).

An interactive session that is open but idle for longer than that does not
block the update -- only genuine recent activity does.

## What gets installed

| File | Goes to | What it does |
|---|---|---|
| `claude-auto-update` | `$HOME/bin/` | The idle check and the update itself |
| `claude-auto-update.service` | `~/.config/systemd/user/` | Runs the script once |
| `claude-auto-update.timer` | `~/.config/systemd/user/` | Fires hourly, up to 5 minutes of jitter |

## Install

```bash
claude-update/install.sh
```

On a machine with no systemd user session, `install.sh` copies the script to
`$HOME/bin` and prints a cron line instead of failing:

```bash
claude-update/install.sh --no-systemd
```

## The npm prefix

The script assumes an npm-global install and finds its prefix in this order:

1. `CLAUDE_UPDATE_PREFIX`, if set.
2. `$HOME/.npm-global`, if a `claude` binary lives there.
3. `npm config get prefix`.

If the resolved `claude` binary is not under that prefix at all, the script
treats it as a **native (non-npm) install** -- the standalone installer, or a
platform package -- and does nothing. That is not a fallback path worth
building here: a native install updates itself a different way, and this
script would only get in the way by trying. It logs the skip and exits 0.

## Logs and rollback

Every run appends one line to `$HOME/logs/claude-update/auto.log`, including
a ready-to-paste rollback command when it actually updates:

```bash
npm install -g --prefix "$PREFIX" @anthropic-ai/claude-code@<old-version>
```

## Config

| Variable | Default | Meaning |
|---|---|---|
| `CLAUDE_UPDATE_IDLE_MIN` | `30` | Minutes of transcript inactivity required before updating |
| `CLAUDE_UPDATE_PROJECTS` | `$HOME/.claude/projects` | Where session transcripts are watched. Point this at a different account's tree if that is the one npm-managed install being kept current |
| `CLAUDE_UPDATE_PREFIX` | auto-detected | Force the npm prefix instead of detecting it |

Set these in the systemd unit's `Environment=` lines, or export them before
running the script by hand.
