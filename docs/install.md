# Install: the rest of the detail

The three-command quickstart is in [README.md](../README.md#quickstart). This
is everything it leaves out.

## With a config file

```bash
git clone https://github.com/jonathanavis96/jev-kit.git ~/code/jev-kit
cd ~/code/jev-kit

cp install/config.env.example install/config.env
$EDITOR install/config.env          # the key file path, and which Claude
                                    # account the tuning judge spends

install/install.sh                  # guard + session check + daemon +
                                    # monitoring + filesearch
install/doctor.sh                   # prove each piece actually runs
```

Three platform guides live beside this one:

- [INSTALL-WSL.md](INSTALL-WSL.md): a Windows PC running WSL2 Ubuntu. It is
  also the closest thing here to a step-by-step for a Linux server.
- [INSTALL-WINDOWS.md](INSTALL-WINDOWS.md): native Windows, no WSL. The core of
  the kit, with Everything in place of `plocate` and no daemon.
- [INSTALL-SECOND-MACHINE.md](INSTALL-SECOND-MACHINE.md): a machine that
  belongs to somebody else, kept in shadow mode for a week before anything is
  armed.

`install.sh` checks its prerequisites before touching anything (Python >= 3.10,
a reachable systemd user session, `plocate`, `node`, `uv`, `git`), runs the unit
tests, deploys an immutable release to `$AIRLOCK_HOME/releases/<sha>/`, flips
`current` at it atomically, and **prints** the `settings.json` edit rather than
making it. Apply the edit only when you mean to:

```bash
install/install.sh --guard --wire ~/.claude/settings.json
```

Every file it edits is backed up, timestamped, in place first.

Components are flags: `--guard --daemon --tuning --monitoring --filesearch
--browser --browse-mcp --review --shim --claude-update --belay --compaction`,
or `--all`.
`--check-only` prints the plan and installs nothing.

The **session check** rides with the guard and is on by default on every
platform (`--no-session-check` leaves it out). It is a `SessionStart` hook
that reads local state and tells you, at the moment you start a session, when
the guard has stopped judging. It prints nothing at all when it has not. The
guard fails open, so without it a dead guard is silent, and on a workstation
nothing outside the machine can notice. To add it to an install
that predates it, re-run the wire step:

```bash
install/install.sh --guard --session-check --wire ~/.claude/settings.json
```

The **browse unlock** rides with the guard the same way, and is on by default
(`install/wire.sh --no-browse-unlock` leaves it out). It is a `PostToolUse`
hook on the single tool name `mcp__browse__browse`, and it is the only thing
that lets `R11-browse-via-jev` stand aside when the kit's own `browse` tool
has given up. Without it, a session whose `browse` call fails has no browser
at all. The same re-run of the wire step adds it to an older install.

Nothing in this repository writes outside `$HOME`, and nothing runs `sudo`. The
one thing an installer will not do for you is install a system package
(`plocate`); it tells you the command and stops.

## The API key

A **TypeSafe API key**, for the Jev judgements. Nothing else in the kit is a
secret.

Every component reads the same key, so its default location is kit-level and
not under the guard's own directory:

```bash
mkdir -p ~/.config/jev-kit && chmod 700 ~/.config/jev-kit
touch ~/.config/jev-kit/env && chmod 600 ~/.config/jev-kit/env
$EDITOR ~/.config/jev-kit/env        # one line: TYPESAFE_API_KEY=...
```

An install whose key is already at `~/.config/airlock/env` keeps working with
no action at all: that path is still resolved, straight after the kit path,
for good. A different path entirely is fine too: set `AIRLOCK_KEY_FILE` (or
`JEVKIT_KEY_FILE`) in `install/config.env`.

**Never print the key.** Not into a terminal you are reading, not into a file
you then display, not into a commit message, not to confirm it. The installer
checks only that the file has a `TYPESAFE_API_KEY=` line; `airlock/keyfile.py`
reads the value with plain Python file I/O and never shells out, so the key
never reaches a command line or a log. It is never logged, printed, or written
anywhere. Nothing in this repository contains a key, and nothing here should
ever be made to print one.

Without a key the guard still installs and still fails open. It simply judges
nothing, so only the code-only rules (which need no key and no network) fire.

### The resolution order, and where it is written down

**The resolution order lives in exactly one place: the module docstring of
`airlock/keyfile.py`.** In outline: the environment, then `AIRLOCK_KEY_FILE`
or `JEVKIT_KEY_FILE`, then the kit default `~/.config/jev-kit/env`, then the
guard-era default `~/.config/airlock/env`, then the path recorded in the
pointer file `~/.config/airlock/keyfile.path`, then
`AIRLOCK_LEGACY_KEY_FILES`. Do not restate the order anywhere else; read it
there.

There are two implementations of that order and no third: `airlock/keyfile.py`
for Python (the guard, the daemon, the health check, the eval) and
`install/keyfile.sh` for the shell, which `install/install.sh`, `belay/run.sh`,
`belay/install.sh`, `monitoring/run_health_check.sh` and
`compaction/install.sh` all source rather than each keeping a copy.

`install/install.sh` writes the pointer file because a Claude Code hook runs
with a bare environment and never sources `install/config.env`. On a machine
whose key is not at either default path, there is otherwise no way to tell the
hook where it is. The pointer holds a path, never a value.

Whoever can write that pointer chooses which file the hook parses for a secret,
so it is followed only under four conditions. It and its directory must be
owned by you, must not be group- or world-writable, and it must name an
absolute path to an existing regular file. Otherwise it is ignored, resolution
carries on, and `install/doctor.sh` says why.

R1 protects both defaults, the pointer, and whatever it names, resolved at
hook time so a pointer written after a release was deployed is still covered.

## Verify

```bash
install/doctor.sh
```

`doctor.sh` does not check that files exist. It runs one real deny and one
real allow through the actual hook process against a throwaway `HOME`, pings
the daemon socket, queries the `plocate` index, and runs the health check end
to end. A component that is not installed is reported `skip`, not `FAIL`.

To prove a deny with your own hands, pipe a fake PreToolUse event at the hook.
R5 (`sudo`) is used here rather than R6 (`xdg-open`) because R5 is on by
default everywhere and R6 is not. The next section covers R6:

```bash
printf '%s' '{"session_id":"t","cwd":"/tmp","tool_name":"Bash","tool_input":{"command":"sudo systemctl restart nginx"}}' \
  | AIRLOCK_MODE=enforce python3 ~/.local/share/airlock/current/hooks/airlock.py
```

That must print JSON containing `"permissionDecision": "deny"`. An ordinary
command must print **nothing at all**:

```bash
printf '%s' '{"session_id":"t","cwd":"/tmp","tool_name":"Bash","tool_input":{"command":"echo hello"}}' \
  | AIRLOCK_MODE=enforce python3 ~/.local/share/airlock/current/hooks/airlock.py
```

## Headless machines, and rule R6

**R6 (`R6-gui-or-browser`) is off by default on every platform.** It blocks
`xdg-open`, `wslview`, `explorer.exe`, `open`, `start` and the browser
binaries, and tells the agent to print the URL or use Playwright headless
instead. That is right on a server with no desktop and wrong on a laptop. Most
people run Claude Code on a laptop, so it ships off, and a headless machine
turns it on.

There are three ways to turn it on, and they all write the same one entry:

1. **The installer detects it.** `install/install.sh --guard` calls
   `airlock/headless.py`, and when the machine is headless it merges
   `{"R6-gui-or-browser": "deny"}` into `$AIRLOCK_CONFIG_DIR/rules.json` and
   says so in one line. The existing file is backed up first, every other
   entry in it is preserved, and **an R6 value you have already set is never
   overwritten**, an explicit `"off"` included.
2. **Force it either way:** `install/install.sh --headless` turns it on
   without consulting the detection; `--no-headless` never touches R6 at all.
3. **On an existing install, one command:**

   ```bash
   (cd ~/.local/share/airlock/current && python3 -m airlock.headless merge ~/.config/airlock/rules.json)
   ```

   It prints `written`, `already` or `error` and a detail, and it is
   idempotent. To turn R6 back off, set it to `"off"` in the same file (or
   delete the entry).

The detection in `airlock/headless.py` is **conservative**, because the two
errors are not symmetric. A false "headless" arms a rule against something the
user can legitimately do. A false "desktop" only leaves the shipped default in
place, and one documented command corrects that. All of this has to be true:

- the platform is Linux (macOS is a desktop system; native Windows says
  nothing useful in these variables);
- `$DISPLAY` and `$WAYLAND_DISPLAY` are both unset or empty;
- it is not WSL, because a WSL distribution reaches a Windows desktop through
  WSLg or `explorer.exe` and so counts as a desktop machine;
- `$XDG_SESSION_TYPE` does not say `x11` or `wayland`;
- if `loginctl` exists **and answers**, it reports no graphical or seated
  session. A `loginctl` that is missing, errors or prints nothing is no
  evidence either way and never on its own makes a machine "headless".

`install/doctor.sh` reports which way R6 is set on this machine, whether that
came from `rules.json` or from the built-in default, and what the detection
thinks of the machine.

## Modes, and how to kill it

Three modes, resolved from `AIRLOCK_MODE`, else the first word of
`$AIRLOCK_CONFIG_DIR/mode`, else the default:

| Mode | Behaviour |
|---|---|
| `shadow` | **the default.** Runs the identical rules table, logs what enforce *would* have done, emits nothing |
| `enforce` | A `deny` blocks the tool call; a `warn` returns advice without blocking |
| `off` | Complete no-op |

```bash
echo enforce > ~/.config/airlock/mode     # arm it
echo shadow  > ~/.config/airlock/mode     # back to log-only
python3 -m airlock.report                 # what shadow mode would have done
```

**Kill switch**, which beats the mode entirely:

```bash
export AIRLOCK_DISABLE=1                  # this shell
touch ~/.config/airlock/disabled          # this machine, until removed
echo off > ~/.config/airlock/mode         # complete no-op
```

Any of the three makes the hook do nothing. To remove it altogether, delete
the `PreToolUse` entry from `settings.json` (every file `--wire` touched has a
timestamped backup beside it).

Per-rule overrides live in `~/.config/airlock/rules.json`; see
[rules.md](rules.md#turning-rules-off-or-down).

## Rollback

```bash
install/rollback.sh                       # previous release, no other edits
```

`rollback.sh` repoints `$AIRLOCK_HOME/current` at the release before the
current one. `settings.json` and the systemd units already reference
`current`, so it takes effect on the next tool call.

## If you are an AI agent

You were probably pointed at this repository and told to install it. Do these
in order:

0. **On native Windows (no WSL), read
   [INSTALL-WINDOWS.md](INSTALL-WINDOWS.md) instead of this list and follow
   it.** The installer is Python, not bash
   (`py -3 install\windows_install.py --check-only`); the hook command has two
   shapes and the installer picks between them by detecting Git for Windows,
   so do not hand-write it; the warm daemon does not exist there, and a doctor
   reporting it as `skip` is correct rather than a fault; `es.exe` (voidtools
   Everything) replaces `plocate` for file search, so **detect it, never
   install it**, and if it is missing say so and let the human decide; and the deny you
   prove is R1 (an attempt to print the key file), because R6 (GUI/browser) is
   `off` by default everywhere. Inside WSL, this list applies unchanged.
1. Read this file and `install/README.md`. Do not start by reading the whole
   repository.
2. Run `install/install.sh --check-only` and report the plan and any
   prerequisite warnings to the human before installing anything.
3. **Ask the human for the `TYPESAFE_API_KEY`** and tell them where to put it
   (above). Do not ask them to paste it into your session. If they do anyway,
   do not echo it back, do not write it into a file you then display, and tell
   them it should be rotated.
4. Run `install/install.sh`, then `install/doctor.sh`, and report the real
   output. A download that succeeded is not a capability that works.
5. **Before wiring, back up every `settings.json` you are about to touch**, to
   a timestamped sibling. `install/wire.sh --apply` does this for you; if you
   edit by hand, do it yourself first.
6. Use **absolute paths** in the hook command: `python3
   /home/<user>/.local/share/airlock/current/hooks/airlock.py`, never
   `python3 hooks/airlock.py`. A leading `~` in a hook command IS expanded
   (Claude Code runs hook commands through a shell). But a hook invoked
   without a shell, or with a different `$HOME`, has nothing to expand
   against, and `$HOME`/`$AIRLOCK_HOME`-style variables in the JSON are not
   reliably expanded either way. Write the path out in full regardless.
7. **Prove a deny works** with the piped fake event above before telling
   anyone the install is done.
8. Leave it in `shadow`. Arming it is the human's decision.

Do **not** do any of these without asking first:

- **Turning on compaction** (`--compaction`). It sends raw, unredacted tool
  inputs and results off the machine, up to roughly 25,000 tokens per
  request: far more than anything else here. Read `compaction/README.md` and
  get an explicit yes.
- **Enabling `enforce` on a machine that is not yours.** A deny blocks
  somebody else's tool call. Shadow first, for long enough that the log says
  something.
- **Installing `--review` or `--belay`** on a machine where cloning
  third-party repositories needs approval. Each clones a pinned external
  repository; `--belay` is in the default set, so use explicit component flags
  if that matters. `--browser` no longer clones anything: the browser agent is
  vendored at `vendor/jev-ultrafast` and that flag only syncs its Python
  dependencies with `uv`.
- **Rewriting or deleting an existing `settings.json` hook entry** that is not
  airlock's.

## Install on WSL

WSL does not run systemd unless you turn it on. Inside the distribution:

```ini
# /etc/wsl.conf
[boot]
systemd=true
```

then, from Windows, `wsl --shutdown` and start the distribution again.

`install.sh` detects the absence of a systemd user session, prints those
instructions, and **degrades rather than failing**. In no-systemd mode:

- there is no warm daemon, so `airlock/client.py` makes a direct HTTPS call
  per judgement (roughly 0.9 s instead of 0.3 s). The enforce-mode budget still
  applies and it still fails open.
- there is no health-check timer and no tuning timer. Run
  `monitoring/run_health_check.sh` and `tuning/tune.sh` by hand or from cron.
- there is no hourly `plocate` refresh. Rebuild the index with
  `filesearch/install.sh --no-systemd`.

The guard itself is unaffected: it is one Python process per tool call and
needs nothing scheduled.

The step-by-step is [INSTALL-WSL.md](INSTALL-WSL.md), which is also the
closest thing to a step-by-step for a plain Linux server. For installing on a
machine that belongs to somebody else, kept in shadow mode for a week before
arming anything, see [INSTALL-SECOND-MACHINE.md](INSTALL-SECOND-MACHINE.md).

## Tuning and promotion

`tuning/tune.py` runs on a timer and reads recent rows out of the shadow log.
A judge model scores whether the guard's verdict was right, and the criteria in
`airlock/questions.py` are rewritten where it was not. It then runs the eval
and commits the result to an `auto-tune` branch **in its own git worktree**. It
never touches the main working tree and never merges.

Promotion to `main` is a separate, deliberate step:

```bash
tuning/promote.sh          # runs the unit tests on auto-tune, then fast-forwards
install/deploy.sh          # export that commit and flip `current` at it
```

Auto-promotion exists but is off unless `$AIRLOCK_CONFIG_DIR/auto-promote`
exists, so it stays a per-machine choice.

`tuning/calibrate.sh` exports the eval's predictions in the format `jevcal`
expects, and writes a threshold lock file under `eval/`. Tune against observed
correctness, never against reported confidence: a model's confidence and its
accuracy are different measurements, and the published measurement cited in
[CREDITS.md](CREDITS.md) shows how far apart they can get.

## Tests

```bash
python3 -m unittest discover -s tests
```

The HTTP call is fully mocked, so this needs no key and makes no network calls.

Not part of `discover`, because each of these makes real calls or real
processes:

```bash
python3 tests/latency_hook.py 200            # real hook processes, timed
set -a; . "$AIRLOCK_KEY_FILE"; set +a
python3 tests/live_smoke.py                  # a handful of real Jev calls
python3 -m airlock.eval --json               # the labelled cases, real calls
python3 -m airlock.report                    # summarise the shadow log
```
