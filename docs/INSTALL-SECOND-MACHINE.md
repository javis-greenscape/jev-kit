# Installing jev-kit on somebody else's machine

This guide is for installing on a Windows PC that belongs to somebody else --
a colleague's machine, not one you administer day to day. Every judgement in
it follows from that: it is their machine, they did not ask for this, and a
wrong deny on their session is the expensive failure.

Keep the guard in `shadow` mode for a week and read the log before arming
anything. A slow install costs nothing by comparison.

Throughout, "the owner" means the person whose machine it is.

## First: which route

Answer this before anything else, because it decides which of the rest of
this document applies.

- **WSL2 is present** (`wsl -l -v` from PowerShell shows a distribution, or
  they already run a Linux terminal on this machine): follow
  [INSTALL-WSL.md](INSTALL-WSL.md) instead of this document. It
  covers the same ground -- systemd, cloning, the key file, wiring, shadow
  mode -- for exactly this setup, and there is no reason to duplicate it.
- **Claude Code runs natively on Windows, with no WSL**: keep reading. This
  is the case this document actually covers, and it is honestly worse
  supported.

## Native Windows: what works and what does not

Being straight about this up front: **native Windows is UNTESTED.** Nothing
in this repository has been run there. What follows is what should work
based on what each piece actually is, not a result anyone has confirmed.

The `PreToolUse` hook itself is plain, stdlib-only Python 3. If Python 3.10+
is installed on Windows and reachable from wherever Claude Code's
`settings.json` calls it, the hook process itself should run. That is the
one piece with a real chance of working as-is.

Everything else assumes a Unix environment it will not find on native
Windows:

| Piece | Why it does not apply |
|---|---|
| The warm-connection daemon | Listens on a Unix domain socket. There is no equivalent wired up for Windows named pipes; skip `--daemon`. |
| The health timer, the tuning timer, the filesearch timer | All systemd user units. There is no systemd on native Windows; skip `--monitoring`'s and `--tuning`'s timer installation and `--filesearch` entirely, and run `monitoring/run_health_check.sh` by hand if you want it at all (it is a bash script, so it needs a shell that can run it -- Git Bash, if installed). |
| `filesearch/` (plocate) | plocate does not exist on Windows. There is no substitute installed by this repository. If the owner needs fast filename search on the Windows side, that is **Everything's `es.exe`** command-line tool -- separate, Windows-native, not part of this repository. |
| `browser/`, `review/` | Both clone and build Node/Python tooling with Unix assumptions (`uv`, nvm-selected Node versions, POSIX paths). Do not attempt these on native Windows without testing first. |
| `claude-update/`'s timer | A systemd user unit again. The script installs and can be run by hand, but nothing schedules it; `--claude-update` will report the timer as skipped. |
| `belay/`'s wrapper | `belay/install.sh` writes a bash wrapper to `$HOME/bin` and clones with `git`. It needs a shell that can run bash (Git Bash, if installed); on WSL it is unremarkable. |
| `compaction/` | The plugin itself is Claude Code's own function-hook machinery and is not Unix-specific, but `compaction/install.sh` is a bash script. Same caveat as belay. |

So the realistic scope on native Windows is: the hook itself, in shadow
mode, calling out over HTTPS with no warm daemon (the same fallback path
WSL-without-systemd uses, just permanently rather than until systemd is
turned on). That is a legitimate, useful subset -- it is the part that
actually reads tool calls and can eventually deny something -- but it is not
the full install this repository otherwise describes, and it has not been
run on Windows to confirm even that much works. Test it yourself before
relying on it, and say plainly to the owner that it is unproven.

## The rest of the install (both routes, adjusted)

### 1. Clone the repository

On the WSL route, follow INSTALL-WSL.md's cloning step. On native Windows,
`git clone https://github.com/jonathanavis96/jev-kit.git` works the same way
from a Windows shell if `git` is installed for Windows; the repository itself
has no OS-specific content.

### 2. Create the key file

Same rule everywhere: **type the key into the file with an editor, never
paste it into a Claude session.** On native Windows there is no
`~/.config/jev-kit/env` convention to lean on automatically -- pick a
path outside any repository, mode-restricted as far as Windows permissions
allow, and set `AIRLOCK_KEY_FILE` in `install/config.env` to point at it.
On the WSL route, follow INSTALL-WSL.md's key-file step exactly.

### 3. Install

WSL route, the recommended set:

```bash
install/install.sh --guard --daemon --monitoring --filesearch \
                   --claude-update --belay
```

`--claude-update` is the hourly idle-only Claude Code updater: it only
updates when no headless run is alive and no session transcript has changed
in the last 30 minutes, so it never interrupts the owner mid-session. It sends
nothing anywhere; it is `npm install -g` on a timer with an idle check.

`--belay` is a Stop hook, and it is worth explaining to the owner in plain words
rather than by name. When an agent announces it has finished, belay checks
whether that turn actually changed files and whether any check has passed
since. If files changed and no passing check stands behind the claim, it asks
Jev a few yes/no questions about what the transcript actually shows, and if
the claim looks unsupported it sends the agent back to verify instead of
letting the turn end. At most three times per session, and it fails open --
no key, a slow answer or any error and the turn simply ends as normal. What
leaves the PC is small and redacted: the task text, the final message and the
check command lines, through a 13-rule secret redactor, capped at a few
thousand characters. No diffs, no file contents.

Native Windows, given the table above: start with the guard alone and accept
that it runs cold (no daemon), for example

```bash
install/install.sh --guard --no-systemd --claude-update --belay
```

then confirm with `install/doctor.sh` what actually came up versus what was
skipped -- it reports each optional piece as "skipped", not as a failure,
when it is genuinely not installed. On native Windows the claude-update timer
is one of those skips: the script installs, nothing schedules it.

### 4. The compaction plugin -- ask the owner first, then install it

Recommended, and a separate step on purpose: `install/install.sh` never
installs it for you, even with `--compaction`. It runs through the
component's own installer:

```bash
less compaction/README.md
compaction/install.sh
```

**What it sends off this PC, in one sentence:** up to roughly 25,000 tokens
of raw tool inputs and tool-result text per request -- file contents, command
output, fetched pages -- truncated only for size, with no redaction pass
anywhere in the plugin's source.

**The owner should agree to that before it is run**, in those words, not as a
line item in a list of components. It is their machine and a much larger
exposure than anything else installed here. What they get for it is that long sessions
stay inside their context window instead of degrading -- a measured manual
compaction elsewhere took a session from 49,288 to 23,111 tokens
in 906 ms. It needs Claude Code 2.1.274 or later with function hooks enabled.
If they would rather not, skip this step; everything else above works without
it, and nothing else in the recommended set sends anything unredacted.

### 5. Shadow mode, for a week, not a day

This is somebody else's machine, and they did not ask for this. Leave the mode file at
its default (`shadow`) and do not touch it:

```bash
cat ~/.config/airlock/mode      # should read "shadow"
```

Read the shadow log after a few days before even discussing arming it:

```bash
cat "$(python3 -c 'from airlock import paths; print(paths.state_dir())')/shadow.jsonl" | tail -50
```

Only after that, and only after telling the owner what would have blocked and
why, consider:

```bash
echo enforce > ~/.config/airlock/mode
```

### 6. The kill switch -- tell the owner about this one specifically

```bash
touch ~/.config/airlock/disabled
```

makes the hook a complete no-op until the file is removed, no matter what
mode is set. They should know this exists and where it is, independent of
whether they ever plan to use it -- it is their machine.

### 7. Doctor

```bash
install/doctor.sh
```

reports each piece as pass, fail, or skip; a skip for something you know is
not installed (the daemon on native Windows, filesearch anywhere on native
Windows) is expected, not a problem to chase.

## The owner's data position, plainly

Whatever gets installed here changes what leaves this PC on every guarded
Claude Code session, so it is worth saying exactly what, without hedging:

- **The guard itself** sends TypeSafe redacted summaries of individual tool
  calls -- the small, genuinely ambiguous fraction the code-only pre-filter
  cannot decide alone -- never a raw tool result, and every string is passed
  through `airlock/redact.py` first, which replaces token-shaped strings
  with `[REDACTED]` before anything leaves. This is on whenever the guard is
  installed and not disabled; it is the core of what this repository is.
- **`belay/` (the Stop hook)** sends the task text, the final assistant
  message and check command lines, through the same class of redactor, capped
  at a few thousand characters. No diffs, no file contents, no tool inputs,
  and only on a turn that changed files with no passing check behind the
  claim.
- **`claude-update/`** sends nothing off the machine at all. It is an idle
  check and `npm install -g`.
- **`compaction/` (fast-jev-compaction) is recommended but sends far more,
  and is never installed for you.** It sends up to about 25,000 tokens of raw
  tool inputs and tool-result text per request, **unredacted** -- there is no
  redaction pass in that plugin at all. Step 4 above is deliberately a
  separate conversation with the owner about what that means for whatever
  they have open in a session at the time. See `compaction/README.md` for the full
  picture.

## Troubleshooting

| Symptom | Cause |
|---|---|
| Everything about the daemon reports "skipped" | Expected on native Windows, and on WSL before systemd is turned on. The client falls back to a direct HTTPS call automatically; the guard still works, just slower per call. |
| A hook silently never fires | Not usually the `~` itself: hook commands run through a shell, so a leading `~/` in a command like `bash ~/.claude/hooks/...` on the WSL route generally IS expanded (measured under WSL with Claude Code 2.1.278, 2026-09-19 -- see `docs/INSTALL-WSL.md`'s troubleshooting table). `$HOME`/`$AIRLOCK_HOME`-style variables in the JSON are the part that is not reliably expanded. Write absolute paths anyway -- untested on native Windows, and a hook invoked without a shell has nothing to expand against -- but look elsewhere first: the wrong config directory, a non-executable script, or a `matcher` that does not match. |
| `ask` always behaves like a deny | Expected in any headless run -- there is nobody to answer it, so it degrades to deny (verified against Claude Code 2.1.272). No rule ships with `ask` by default. |
| Nothing is scheduled at all | Native Windows has no systemd; that is not a bug to fix, it is the platform. Run `monitoring/run_health_check.sh` by hand (via Git Bash or WSL) if you want a health check at all. |
| Unsure whether something installed actually works | Run `install/doctor.sh`. It runs real processes and reports pass/fail/skip; it does not just check that files exist. |
