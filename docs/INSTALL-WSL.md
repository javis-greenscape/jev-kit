# Installing jev-kit on WSL

This guide is for a Windows PC running WSL2 Ubuntu, where Claude Code and its
config live inside the WSL distribution rather than on the Windows side.
Everything below runs from a WSL terminal (`wsl` from PowerShell, or a Windows
Terminal WSL tab) unless a step says otherwise.

If you are installing on a headless Linux server instead, the same steps
apply except section 1, which exists only because WSL does not start systemd
by default.

A workstation often has **several** Claude config directories rather than one,
and may use per-repository git hooks. See [Finding every Claude config
directory](#5-finding-every-claude-config-directory) before you get to wiring
the hook in. Do that step once you know the list, not before.

## 1. Turn on systemd

WSL2 does not run systemd unless told to. Without it there is no warm
daemon, no health timer, no tuning timer and no hourly filesearch index --
`install/install.sh` still installs and the guard still works, just slower
per call and with nothing scheduled. To get systemd:

Inside the distribution, edit (or create) `/etc/wsl.conf`:

```ini
[boot]
systemd=true
```

Then, from Windows (PowerShell, not inside WSL):

```powershell
wsl --shutdown
```

and start the distribution again. Confirm it took:

```bash
systemctl --user show-environment
```

If that fails, `install/install.sh` will detect the missing session itself,
print the same instructions, and degrade to no-systemd mode rather than
failing -- see [Install on WSL](install.md#install-on-wsl) in the install
guide for exactly what no-systemd mode costs you. It is a legitimate way to
run this for a while if you are not ready to touch `/etc/wsl.conf` yet.

## 2. Clone the repository

```bash
mkdir -p ~/code
git clone https://github.com/jonathanavis96/jev-kit.git ~/code/jev-kit
cd ~/code/jev-kit
```

Nothing in this repository needs a credential to clone, and nothing in it
should ever hold one. If you are installing from a private fork instead, clone
that; the rest of this guide is unchanged.

## 3. Create the key file

The guard, and every optional component that talks to TypeSafe, reads
`TYPESAFE_API_KEY` from a key file rather than a hard-coded value.

**This step is not optional.** Check whether a key is already in place before
you install, because `install/install.sh --check-only` reports a missing key
and then still installs happily:

```
warn  no TYPESAFE_API_KEY found (env, or /home/<you>/.config/jev-kit/env).
warn    the guard still installs and still fails open; it just judges nothing.
```

Without the key the install is close to pointless and is actively
mis-weighted: every rule with a fuzzy half (R1 secret exposure, R4, R10) asks
Jev and so fails open, while the code-only rules (R5 sudo, R6 GUI, R9 commit
secret, the two legacy R8 guards) still hard-deny. Arming `enforce` in that
state blocks ordinary workstation actions while doing nothing at all about
secrets. Get the key in place first, or stay in `shadow`.

Create it mode 600 at the default path. One key serves every component in
the kit, so it is kit-level and not under the guard's own directory:

```bash
mkdir -p ~/.config/jev-kit
chmod 700 ~/.config/jev-kit
touch ~/.config/jev-kit/env
chmod 600 ~/.config/jev-kit/env
```

If you installed an earlier version and your key is already at
`~/.config/airlock/env`, leave it there. That path is still resolved, for
good, and nothing needs moving.

If your machine already keeps keys somewhere else, put it there instead and
set `AIRLOCK_KEY_FILE` in `install/config.env` to that path. The installer
records the path (never the value) so the hook can find it.

**Type the key into the file with an editor** (`nano ~/.config/jev-kit/env`,
or `$EDITOR`) as a line reading `TYPESAFE_API_KEY=...`. Do not paste it into
a Claude session, an issue, a chat message or a command line -- anything a
transcript or shell history retains is a place a key can leak from. Never
`cat` the file afterwards to check it; if you need to confirm it is there,
check for the line without printing its value:

```bash
grep -q '^TYPESAFE_API_KEY=' ~/.config/jev-kit/env && echo "line present"
```

## 4. Configure and install

```bash
cp install/config.env.example install/config.env
$EDITOR install/config.env      # set AIRLOCK_KEY_FILE if you put it
                                 # somewhere other than the default above,
                                 # and which Claude account the tuning judge
                                 # and bench sessions spend
```

Recommended flags for a fresh machine:

```bash
install/install.sh --guard --daemon --monitoring --filesearch \
                   --claude-update --belay
install/doctor.sh
```

`--claude-update` installs the hourly idle-only Claude Code updater
(`claude-update/README.md`): it checks once an hour and updates only when no
headless run is alive and no session transcript has changed in the last 30
minutes, so it never lands mid-thought. There is no reason to run a stale CLI
on this machine, so it is part of the recommended set rather than an extra.

`--belay` installs the Stop hook. In plain words: when an agent says it has
finished, belay looks at whether that turn actually changed files and whether
any check has passed since. If files changed with no passing check behind the
claim, it asks Jev a few yes/no questions about what the transcript actually
shows, and if the claim looks unsupported it sends the agent back to verify
instead of letting the turn end. It does that at most three times in a
session, and it fails open: no key, a slow answer or any error means the turn
ends normally. What leaves the machine is small and redacted -- the task text,
the final message and the check command lines, through a 13-rule secret
redactor, capped at a few thousand characters. No diffs, no file contents.
`belay/install.sh` prints the Stop hook block to add; nothing is written to a
`settings.json` for you.

Add `--tuning` once you are comfortable with what the tuning loop does
(`tuning/README.md`); leave `--browser`, `--review` and `--shim` for later,
deliberate decisions. Compaction is its own numbered step below, because
`install.sh` deliberately refuses to install it for you.

`install.sh` never edits a `settings.json` unless you pass `--wire`; without
it, the hook edit is only printed. Do not pass `--wire` yet -- wire each
config directory by hand in the next step, because there is more than one.

## 5. Finding every Claude config directory

A workstation often has several Claude config directories, not one, because
it runs per-repository git hooks alongside interactive sessions on more than
one account.

A bare `ls -d ~/.claude*` is **not** the list. On the machine this guide was
written against it returned 11 entries, most of which were files (`.claude.json`, `.claude.json.bak*`,
`.claude-remote-control.log`, `.claude-usage-notify.env`) or directories with
no `settings.json` in them. Only a directory that actually has a
`settings.json` can be wired. Filter for that:

```bash
for d in ~/.claude*/; do [ -f "$d/settings.json" ] && echo "$d"; done
```

On that machine it yielded exactly two of the eleven. The others existed but
carried no `settings.json`, so there was nothing to wire in them -- do not
create one just to have somewhere to put the hook.

Also check any repo-level `.envrc`, wrapper script, or shell alias that sets
`CLAUDE_CONFIG_DIR` itself before assuming the `~/.claude*` glob is the whole
list.

## 6. Wire the hook into each one

For every directory the previous step found:

```bash
install/wire.sh --print ~/.claude/settings.json ~/.claude-<account>/settings.json ...
```

**`wire.sh --apply` both ADDS a missing airlock hook and repoints an
existing one.** Earlier releases of this repository only repointed: against
a settings.json that had never had the guard in it -- which was the case for
both files on that machine -- `wire.sh` printed `no airlock.py
hook command found, skipping` and changed nothing, so a first install needed
a hand-edit before `wire.sh` had anything to do. That gap is fixed: `--apply`
now adds the missing `PreToolUse` entry itself (matcher `"*"`, timeout 5,
command `<absolute python3> $AIRLOCK_HOME/current/hooks/airlock.py`),
idempotently, backing the file up first and leaving every other key and hook
untouched -- or creates the file outright if it does not exist yet. `--print`
shows exactly what `--apply` would add or repoint.

So on a first install the order is simply:

```bash
install/install.sh --guard --wire ~/.claude/settings.json ~/.claude-<account>/settings.json ...
```

which adds the hook to every file named (each backed up first, or created if
missing), and on every later run just repoints them at the newest release
after a `deploy.sh`.

Pass `--belay` (`install/wire.sh --apply --belay ...`, or `install.sh --wire`
when `--belay` is one of the components selected -- it is in the installer's
default set) to also add the `Stop` hook (matcher `"*"`, absolute path to
`<home>/bin/airlock-belay-run`, timeout 25) to the same files, in the same
pass. It is only added when that wrapper file actually exists on this
machine; otherwise `wire.sh` says so and leaves `Stop` alone. Pass
`--function-hooks` to also set `"CLAUDE_CODE_ENABLE_FUNCTION_HOOKS": "1"` in
each file's `env` block, if compaction is going in.

Repeat for any further config directory the glob or your check turned up.
Missing one is not catastrophic -- that account's sessions simply run
without the guard -- but it means "installed" is not the same as "wired
everywhere yet".

## 6a. Per-machine rule overrides

The rules table is written against a headless server. A workstation has a
desktop, so at least one rule is simply wrong there.
Overrides live in `~/.config/airlock/rules.json`, one entry per rule id from
`airlock/rules.py`, with values `deny` | `ask` | `warn` | `log` | `off`:

```bash
mkdir -p ~/.config/airlock
cat > ~/.config/airlock/rules.json <<'JSON'
{"R6-gui-or-browser": "off", "R5-sudo": "warn"}
JSON
```

`R6-gui-or-browser` denies `xdg-open`, `wslview` and `explorer.exe` because
a headless server has no desktop; on a workstation those are ordinary. It is
**`off` by default on every platform** now, and the installer never turns it on
inside WSL (WSL reaches a Windows desktop), so the `"off"` entry above is
belt-and-braces rather than a change. `R5-sudo` is a
workstation's own machine, so it warns rather than blocks. `R3` and `R7`
already default to `warn` and need no entry. `R4-long-work-bare-shell` also
defaults to `warn`, so it needs no entry either, but note that its advice
text still talks about a box "reached only over SSH" -- accurate for a
server, not for a workstation.

**Watch out:** once `rules.json` exists, re-running `install/install.sh
--guard` fails. Its pre-deploy `python3 -m unittest discover -s tests` reads
the real `$HOME`, so the machine's own overrides leak into
`tests/test_user_requested.py` (which expects `R5-sudo` to deny) and 5 tests
fail with `refusing to deploy it`. Verified 2026-09-19: the
same suite passes with `env HOME=$(mktemp -d)`. Until the tests are isolated
from `$HOME`, either set `rules.json` after the guard install, or re-run the
install with a throwaway `HOME`.

## 7. Start in shadow mode

The default mode is already `shadow` on a fresh install: it logs what
enforce *would* have done and blocks nothing. Leave it there for a day and
read the log before arming it:

```bash
cat "$(python3 -c 'from airlock import paths; print(paths.state_dir())')/shadow.jsonl" | tail -50
```

Then arm it:

```bash
echo enforce > ~/.config/airlock/mode
```

and go back to shadow at any time the same way:

```bash
echo shadow > ~/.config/airlock/mode
```

## 8. The kill switch

Either of these makes the hook a complete no-op, immediately, on every
account it is wired into:

```bash
export AIRLOCK_DISABLE=1                  # this shell only
touch ~/.config/airlock/disabled          # this machine, until removed
```

Removing the `disabled` file, or unsetting the variable, restores whatever
mode was set before.

## 9. Prove it actually runs

```bash
install/doctor.sh
```

`doctor.sh` runs real hook processes against a throwaway `HOME`, and pins R6
on inside that throwaway `HOME` -- which means its deny case (an `xdg-open`
blocked by R6) passes on any machine, whatever this one's `rules.json` says
about R6. It reports this machine's actual R6 setting separately. It proves the hook works, not what this
machine's rules do; check those by piping an event through the deployed hook
with the real `HOME`. `doctor.sh` (one real
deny, one real allow, a fail-open check, a kill-switch check), checks the
daemon over its Unix socket, runs a real health check, queries the real
plocate index, and reports every installed systemd timer's actual state --
it does not just check that files exist.

## 10. The health timer

Installed by `--monitoring`. It runs every five minutes and writes
`health.jsonl`; nothing is pushed to Uptime Kuma until `GS_KUMA_AIRLOCK_PUSH_URL`
is set in the key file (it is a capability token, so it is treated like a
secret, not put in `install/config.env`). Create the push monitor first, then
add the URL, then the health check will start pushing on its next run.

## 11. The plocate index, under WSL

`--filesearch` indexes `$HOME` inside the WSL distribution -- the Linux
side only. It does not, and cannot usefully, see the Windows filesystem:
Windows files under `/mnt/c/...` are visible from WSL but plocate's Ubuntu
package does not index NTFS efficiently, and nothing here tries. For
searching Windows files, **Everything's `es.exe`** command-line tool is the
better fit -- it is a separate Windows-native tool, not something this
repository builds or installs, so if the Windows-side files need
searching, install Everything and use `es.exe` there. This section indexes
the Linux home only.

## 12. The compaction plugin

Recommended, and a separate step on purpose: `install/install.sh` never
installs it, even with `--compaction` -- that flag only prints the warning and
stops. Run the component's own installer yourself, after reading its README:

```bash
less compaction/README.md
compaction/install.sh
```

**What it sends off the machine, in one sentence:** up to roughly 25,000
tokens of raw tool inputs and tool-result text per request -- file contents,
command output, fetched pages -- truncated only for size, with no redaction
pass anywhere in the plugin's source.

That is a much larger exposure than anything else installed here, and it is
why this step is a deliberate act rather than a flag. What you get for it is
real: a long session stays inside its context window instead of degrading, and
a measured manual compaction on this box took a 49,288-token session down to
23,111 tokens in 906 ms. It needs Claude Code 2.1.274 or later with function
hooks enabled. If the exposure is not acceptable for the work this machine
does, skip this step and say so in the machine notes -- everything else above
still works without it.

## 13. Updating later

```bash
cd ~/code/airlock
git pull
install/deploy.sh
```

`deploy.sh` finds the main checkout, runs the full unit test suite against a
clean export of its current `main` commit (never the working tree), deploys
it to a new immutable release under `$AIRLOCK_HOME/releases/<sha>/`, and
flips `current` at it atomically. Nothing observes `current` missing or
half-written, and `settings.json` and the systemd units already point at
`current`, so an update takes effect on the next tool call (or daemon
restart) with no further edits.

**Rollback:**

```bash
install/rollback.sh
```

points `current` at the release immediately before the one it currently
points at.

## Troubleshooting

| Symptom | Cause |
|---|---|
| Guard judgements take ~0.9 s instead of ~0.3 s | The daemon is down, or was never installed (no systemd). The client falls back to a direct HTTPS call per judgement automatically -- it still works, just slower. |
| A hook does not seem to run at all, in any mode | Not usually the `~`. Measured under WSL with Claude Code 2.1.278, 2026-09-19: hook commands are run through a shell, so a leading `~/` in `bash ~/.claude/hooks/...` **is** expanded and the hook does run -- another hook on that machine was written that way and wrote its audit log from a real session the same day. Write absolute paths anyway (a hook invoked without a shell, or with a different `HOME`, has nothing to expand against), but look elsewhere first: the wrong config directory, a non-executable script, or a `matcher` that does not match. `$HOME` and `$AIRLOCK_HOME` in a hook command are a different matter -- do not rely on those. |
| An `ask` rule never seems to ask, just blocks | Correct, in a headless run. `ask` is honoured in an attended session, but a headless one has nobody to answer it, so it degrades to an honest deny (verified against Claude Code 2.1.272). No rule ships with `ask` as its default action for exactly this reason. |
| Guard installs fine but "fails open" on everything | This is the intended behaviour whenever something is wrong: no key, no daemon, a malformed payload, an internal error. A broken guard must never block or slow a tool call. Check `install/doctor.sh`'s output for which specific thing is missing. |
| A session on one account is guarded, another is not | Step 6 was not repeated for every directory `ls -d ~/.claude*` found. Re-run `install/wire.sh --print` against the full list. |
