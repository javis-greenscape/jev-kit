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

install/install.sh                  # guard + daemon + monitoring + filesearch
install/doctor.sh                   # prove each piece actually runs
```

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
--browser --review --shim --claude-update --belay --compaction`, or `--all`.
`--check-only` prints the plan and installs nothing.

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
with a bare environment and never sources `install/config.env`, so on a machine
whose key is not at either default path there is otherwise no way to tell the
hook where it is. The pointer holds a path and never a value. Because whoever
can write it chooses which file the hook parses for a secret, it is followed
only when it and its directory are owned by you and are not group- or
world-writable, and when it names an absolute path to an existing regular file;
otherwise it is ignored, resolution carries on, and `install/doctor.sh` says
why. R1 protects both defaults, the pointer, and whatever it names, resolved at
hook time so a pointer written after a release was deployed is still covered.

## Verify

```bash
install/doctor.sh
```

`doctor.sh` does not check that files exist. It runs one real deny and one
real allow through the actual hook process against a throwaway `HOME`, pings
the daemon socket, queries the `plocate` index, and runs the health check end
to end. A component that is not installed is reported `skip`, not `FAIL`.

To prove a deny with your own hands, pipe a fake PreToolUse event at the hook:

```bash
printf '%s' '{"session_id":"t","cwd":"/tmp","tool_name":"Bash","tool_input":{"command":"xdg-open https://example.com"}}' \
  | AIRLOCK_MODE=enforce python3 ~/.local/share/airlock/current/hooks/airlock.py
```

That must print JSON containing `"permissionDecision": "deny"`. An ordinary
command must print **nothing at all**:

```bash
printf '%s' '{"session_id":"t","cwd":"/tmp","tool_name":"Bash","tool_input":{"command":"echo hello"}}' \
  | AIRLOCK_MODE=enforce python3 ~/.local/share/airlock/current/hooks/airlock.py
```

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

1. Read this file and `install/README.md`. Do not start by reading the whole
   repository.
2. Run `install/install.sh --check-only` and report the plan and any
   prerequisite warnings to the human before installing anything.
3. **Ask the human for the `TYPESAFE_API_KEY`** and tell them where to put it
   (above). Do not ask them to paste it into your session. If they do anyway,
   do not echo it back, do not write it into a file you then display, and say
   plainly that it should be rotated.
4. Run `install/install.sh`, then `install/doctor.sh`, and report the real
   output. A download that succeeded is not a capability that works.
5. **Before wiring, back up every `settings.json` you are about to touch**, to
   a timestamped sibling. `install/wire.sh --apply` does this for you; if you
   edit by hand, do it yourself first.
6. Use **absolute paths** in the hook command: `python3
   /home/<user>/.local/share/airlock/current/hooks/airlock.py`, never
   `python3 hooks/airlock.py`. A leading `~` in a hook command IS expanded
   (Claude Code runs hook commands through a shell) -- but a hook invoked
   without a shell, or with a different `$HOME`, has nothing to expand
   against, and `$HOME`/`$AIRLOCK_HOME`-style variables in the JSON are not
   reliably expanded either way, so write the path out in full regardless.
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
- **Installing `--browser`, `--review` or `--belay`** on a machine where
  cloning third-party repositories needs approval. Each clones a pinned
  external repository; `--belay` is in the default set, so use explicit
  component flags if that matters.
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

`tuning/tune.py` runs on a timer, reads recent rows out of the shadow log,
has a judge model score whether the guard's verdict was right, rewrites the
criteria in `airlock/questions.py` where it was not, runs the eval, and
commits the result to an `auto-tune` branch **in its own git worktree**. It
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

Not part of `discover`, and deliberately so:

```bash
python3 tests/latency_hook.py 200            # real hook processes, timed
set -a; . "$AIRLOCK_KEY_FILE"; set +a
python3 tests/live_smoke.py                  # a handful of real Jev calls
python3 -m airlock.eval --json               # the labelled cases, real calls
python3 -m airlock.report                    # summarise the shadow log
```
