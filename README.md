# jev-kit

**Everything you need to run TypeSafe's Jev with Claude Code.** It is a
cost-discipline guard that sits on `PreToolUse`, decides in pure code whether
any rule could apply, and asks Jev only the genuinely fuzzy half of a rule
that might fire. Around that guard it ships the components that make Jev
useful day to day -- a browser agent, code review, file search, a Stop-hook
verifier, transcript compaction, an auto-updater, a document classifier and
log triage -- each independently installable, each fail-open.

It blocks the small number of tool calls that are plainly the wrong move on a
working machine: a lookup dispatched to the most expensive agent, a disk-wide
`find` where an index exists, printing a secret into a transcript. It stays
silent for everything else, and it fails open everywhere -- a broken guard can
never block or slow a tool call. The default mode is **shadow**: it runs the
real rules table, logs what it *would* have done, and blocks nothing.

**This is not a sandbox.** Several projects called "airlock" isolate agents or
untrusted code in a VM or container. This does none of that. It is a policy
hook inside your own session that judges individual tool calls and, at most,
returns a deny to Claude Code.

`airlock` is the name of the guard component, its Python package, its systemd
units and its config directory -- formerly `plumbline`, formerly `jev-guard`.
The first rename was forced: a public project,
[leepokai/jev-guard](https://github.com/leepokai/jev-guard), already uses that
name. Both older names still work through the cutover; see
[Migrating to airlock](#migrating-to-airlock).

## What is in the box

Every component is optional except the guard. Two columns are worth reading
before you install anything: **Exercised**, which says how much use and testing
each piece has actually had, and **What leaves the machine**.

`Exercised` means: unit tests, a labelled eval, a measured bench, and real daily
use on at least one machine. `Experimental` means the logic has unit tests and
the component has been run by hand, but it has no labelled corpus, no measured
numbers, and no sustained real use -- keep it, read its README's banner, and do
not build anything load-bearing on it yet.

| Component | What it does | Default | Exercised | What leaves the machine |
|---|---|---|---|---|
| `airlock/` + `hooks/` (the guard) | The rules table, policy, redaction, client and health. The `PreToolUse` entry point. | **yes** (`--guard`) | exercised | Redacted summaries of the ambiguous fraction of tool calls, to TypeSafe. Never a raw tool result; every string passes through `airlock/redact.py` first. |
| `deploy/` (warm daemon) | Keeps a warm connection so a judgement costs ~0.3 s instead of ~0.9 s (measured; see [Measured numbers](#measured-numbers)). | **yes** (`--daemon`) | exercised | Nothing of its own. It is the transport the guard already uses. |
| `monitoring/` | Five-minute health check, its timer, and an optional push to a monitor you host. | **yes** (`--monitoring`) | exercised | Nothing, unless you set `AIRLOCK_KUMA_PUSH_URL`, in which case a bare liveness ping to that URL. |
| `filesearch/` | Per-user `plocate` index of `$HOME` and its hourly timer, so R8 can suggest an indexed search. | **yes** (`--filesearch`) | exercised | Nothing. Entirely local. |
| `claude-update/` | Idle-only Claude Code auto-updater and its timer. Updates only when no run is alive. | **yes** (`--claude-update`) | exercised | Nothing. An idle check and `npm install -g`. |
| `belay/` | Wrapper for the community `jev-belay` Stop hook: when an agent claims it is finished with no passing check behind it, sends it back to verify. | **yes** (`--belay`) -- clones a pinned third-party repo | exercised | Task text, final assistant message and check command lines, through a 13-rule secret redactor, capped at a few thousand characters. No diffs, no file contents. |
| `browser/` | Clones and patches a Jev-decided browser agent at a pin. | no (`--browser`) | own numbers, see `browser/README.md` | Page state and goals to the decision model you configure. |
| `review/` | Clones a Jev code reviewer at a pin, with a fail-open wrapper. | no (`--review`) | no numbers here | Diffs, to whatever review gate you configure. |
| `shim/` | An OpenAI-shaped HTTP shim over the `claude` CLI. | no (`--shim`) | **experimental** | Whatever you send through it. |
| `compaction/` | Installer for the community `fast-jev-compaction` plugin. **Read `compaction/README.md` first.** | no (`--compaction`) | never enabled here | **Up to ~25,000 tokens of raw, unredacted tool inputs and tool-result text per request.** By far the largest exposure here, which is why it is never installed for you. |
| `tuning/` | Unattended tuning loop, its timer, promotion and threshold calibration. | no (`--tuning`) | exercised | Real Claude sessions on the account you nominate, for the judge. |
| `docclass/` | Two-stage document classifier with an escape hatch and a confidence gate. | no | **experimental** | Page text, to TypeSafe, when you call it. |
| `logtriage/` | Redact-first, local-rules-first log triage on stdin. | no | **experimental** | Nothing until local rules are exhausted; redacted lines after that. |
| `eval/` | Labelled cases for every rule, and the ablations. | n/a | exercised | Nothing. |
| `bench/` | A/B benchmark: enforce mode against no guard at all. | n/a | exercised | Nothing until you run it. |
| `install/` | One installer, a doctor, deploy/rollback/wire, the migration script. | n/a | exercised | Nothing. |
| `docs/` | Credits for the community projects the ported patterns came from, and the install guides. | n/a | n/a | Nothing. |

## Install (for a person or an agent)

Everything needed to go from the repository URL to a working install. The
default is **shadow mode**: the guard runs the real rules table, logs what it
*would* have done, and blocks nothing.

```bash
git clone https://github.com/jonathanavis96/jev-kit.git ~/code/jev-kit
cd ~/code/jev-kit
install/install.sh          # guard + daemon + monitoring + filesearch
                            # + claude-update + belay, all under $HOME
install/doctor.sh           # prove each piece actually runs
```

`install.sh` never runs `sudo`, never writes outside `$HOME`, and never edits
a `settings.json` unless you pass `--wire`. Without `--wire` it prints the
exact JSON to add and stops. `--check-only` prints the plan and installs
nothing.

### The one thing it needs from a human

A **TypeSafe API key**, for the Jev judgements. Nothing else on this list is
a secret.

1. Ask the human for a `TYPESAFE_API_KEY`.
2. They put it in a file, one `TYPESAFE_API_KEY=...` line, mode 600. The
   default location is `~/.config/airlock/env`:

   ```bash
   mkdir -p ~/.config/airlock && touch ~/.config/airlock/env
   chmod 600 ~/.config/airlock/env
   $EDITOR ~/.config/airlock/env        # TYPESAFE_API_KEY=...
   ```

   A different path is fine: set `AIRLOCK_KEY_FILE` in `install/config.env`
   (`cp install/config.env.example install/config.env` first).
3. **Never print the key.** Not into a terminal you are reading, not into a
   file you then display, not into a commit message, not to confirm it. The
   installer checks only that the file has a `TYPESAFE_API_KEY=` line;
   `airlock/keyfile.py` reads the value with plain Python file I/O and never
   shells out, so the key never reaches a command line or a log.

Without a key the guard still installs and still fails open. It simply judges
nothing, so only the code-only rules (which need no key and no network) fire.

### Verify

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

### Switch modes

Run in `shadow` for a while first, read the log, and only then arm it.

```bash
echo shadow  > ~/.config/airlock/mode     # the default: log only, block nothing
echo enforce > ~/.config/airlock/mode     # a deny now blocks
python3 -m airlock.report                 # what shadow mode would have done
```

### Kill switch and rollback

```bash
export AIRLOCK_DISABLE=1                  # this shell, immediately
touch ~/.config/airlock/disabled          # this machine, until removed
echo off > ~/.config/airlock/mode         # complete no-op
```

Any of the three makes the hook do nothing. To remove it altogether, delete
the `PreToolUse` entry from `settings.json` (every file `--wire` touched has a
timestamped backup beside it).

```bash
install/rollback.sh                       # previous release, no other edits
```

`rollback.sh` repoints `$AIRLOCK_HOME/current` at the release before the
current one. `settings.json` and the systemd units already reference
`current`, so it takes effect on the next tool call.

### If you are an AI agent

You were probably pointed at this repository and told to install it. Do these
in order:

1. Read this section and `install/README.md`. Do not start by reading the
   whole repository.
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

### Platforms

| Platform | State today |
|---|---|
| **Linux** (systemd user session) | Fully supported, and what every measured number in this README came from. Guard, warm daemon, all timers, `plocate` file search. |
| **WSL2** (Ubuntu, systemd on) | Supported. Identical to Linux once `systemd=true` is in `/etc/wsl.conf` and the distribution has been restarted. See [Install on WSL](#install-on-wsl). |
| **WSL2** (systemd off) | Works, degraded, and the installer detects it. No daemon (a direct HTTPS call per judgement, roughly 0.9 s instead of 0.3 s), no timers, no hourly index refresh. The guard itself is unaffected. |
| **macOS** | Plausible but **untested**. The guard is stdlib Python and should run. There is no systemd, so `--no-systemd` is required, and the daemon, all timers and `plocate` are out. `launchd` equivalents are not written. Nobody has run it. |
| **Native Windows** (no WSL) | **Not supported yet.** The hook command form, the Unix-socket daemon, the systemd units and the `plocate` file-search rule all assume POSIX. See [Native Windows: the gap](#native-windows-the-gap) below. |

## Native Windows: the gap

Native Windows is **not supported yet**, and nothing in this repository has
ever been run there. The intent is Windows and Linux both, with
[Everything](https://www.voidtools.com/) (`es.exe`) providing file search on
Windows where Linux uses `plocate`. That is planned, not built. What "make it
work" actually means, smallest first:

| Item | What native Windows needs | Size |
|---|---|---|
| **Shadow mode's detached worker** | `start_new_session=True` is POSIX-only and raises on Windows. Needs `creationflags=DETACHED_PROCESS \| CREATE_NEW_PROCESS_GROUP`. Shadow is the default mode, so this is on the first-run path. | small |
| **Python launcher** | Windows has no `python3` on PATH. Probe the `py` launcher, then `python.exe`, and resolve to an absolute interpreter path, since a hook runs with a minimal environment. | small |
| **Hook command form** | The same command string with a Windows interpreter and an absolute Windows path, backslashes escaped in JSON. `install/wire.sh` is bash, so the writer needs a Python or PowerShell equivalent. | small |
| **Path handling** | `$HOME` is usually unset (`%USERPROFILE%`); a drive root is `C:\`, not `/`, and there may be several; separators may be either slash; comparisons must be case-insensitive. | medium |
| **File search** | The `plocate` suggestion string becomes `es.exe -i "<pattern>"`; `es`/`es.exe` must join the already-indexed tool family so R8 does not suggest replacing the indexed tool with itself; `filesearch/` becomes a *detection* check rather than an installer, because Everything maintains its own index and is not installable from here. | medium |
| **No systemd** | Task Scheduler equivalents for the timers (`schtasks /sc HOURLY` covers them); the daemon would need a Task or a Windows service. Each unit's `Nice`/`CPUQuota` intent maps only coarsely. | medium |
| **The daemon's transport** | It listens on a Unix domain socket, mode 700, with no TCP listener at all. The recommended first Windows release simply **skips it** and uses the direct-HTTPS fallback the client already has (~0.9 s against ~0.3 s warm), exactly as WSL-without-systemd does today. A named pipe is the correct analogue but needs a non-stdlib dependency; a localhost TCP listener is stdlib-only but is precisely the design this project refuses. | large, or small if skipped |
| **The shell scripts** | The installer, doctor, wire, deploy and rollback are all bash. Either require Git Bash, or port the installer and doctor to Python, which they arguably should be anyway given the guard is stdlib Python. | large |

The smallest credible native-Windows release, in dependency order: the
detached worker, the Python launcher, the hook command form, path handling,
then file search -- with the daemon skipped and the installer and doctor
ported to Python. That is the guard itself, in shadow mode, with the
file-search rule pointing at `es.exe`, no daemon and nothing scheduled.

## Install guides

Two guides live in `docs/`:
[INSTALL-WSL.md](docs/INSTALL-WSL.md) (a Windows PC running WSL2 Ubuntu, and
the closest thing to a step-by-step for a Linux server too) and
[INSTALL-SECOND-MACHINE.md](docs/INSTALL-SECOND-MACHINE.md) (installing on a
machine that belongs to somebody else, kept in shadow mode for a week before
arming anything).

## Install: the rest of the detail

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
```

**Kill switch**, which beats the mode entirely:

```bash
export AIRLOCK_DISABLE=1                  # this shell
touch ~/.config/airlock/disabled          # this machine, until removed
```

Either makes the hook a complete no-op. To remove it altogether, delete the
`PreToolUse` entry from `settings.json`.

Individual rules can be switched off or downgraded in
`~/.config/airlock/rules.json` without touching code:

```json
{"R3-whole-suite-or-uncapped-build": "off", "R7-destructive": "log"}
```

Valid values are `deny`, `ask`, `warn`, `log`, `off`. An `off` rule is skipped
in the pre-filter, so it costs nothing at all.

`ask` hands the decision to the human instead of blocking. It is
configuration-only: no rule ships with it. In a session nobody is attending it
degrades to a plain `deny`, because a headless session has no way to answer an
ask -- verified against Claude Code 2.1.272, not assumed.

Every deny keeps its safety net: the `[airlock-ok: <reason>]` override stamp
in a call's description, loop protection (the same call is never denied twice
in ten minutes), a hard budget, fail-open on any error, and -- where Jev is
involved -- a confidence of at least 0.8 with a margin of at least 0.4 over the
runner-up.

A deny is also softened to a warn when the user's own recent words asked for the
thing. That check reads only what the person typed, never a tool result or a
fetched page, and it can only ever soften: no answer to it can make the guard
stricter. See `airlock/context.py`.

## The rules

The hook is registered for **every** tool. What filters a call is not the
matcher but the table in `airlock/rules.py`: one entry per kind of
genuinely-wrong tool use, each with a code pre-filter that runs in
microseconds. **A call no rule covers costs nothing and logs nothing** -- no
Jev request, no log row, no subprocess.

| id | tools | default | decided by | what it is for |
|---|---|---|---|---|
| `R1-secret-exposure` | Bash, Read | deny | code, Jev for an ambiguous path | reading or echoing a secret into the transcript |
| `R2-claude-api-skill` | Skill | deny (warn with no stated purpose) | Jev | loading a 324,006-token reference for a price lookup |
| `R3-whole-suite-or-uncapped-build` | Bash | warn | code only | the whole test suite, or a build with uncapped parallelism |
| `R4-long-work-bare-shell` | Bash | warn | code, Jev for the ambiguous cases | long work on a shell a dropped connection would kill |
| `R5-sudo` | Bash | deny | code only | `sudo` outside a named package install, or anywhere under `$HOME` |
| `R6-gui-or-browser` | Bash | deny | code only | opening a GUI or browser on a headless box |
| `R7-destructive` | Bash | warn | code only | force pushes, hard resets, wholesale deletion |
| `R8-tier-guard` | Agent | deny two rungs over, **warn one rung over**, rewrite only if asked | Jev | a task dispatched to an agent more expensive than it needs |
| `R8-tool-choice-guard` | Bash | deny | Jev | a disk-wide filename crawl, or a raw grep where a code graph exists |
| `R9-commit-secret` | Bash | deny | code + a local credential belt | staging or committing a secret |
| `R10-general-risk` | Bash | **warn only, never deny** | code pre-filter, then a Jev Score + `user_requested` | the catch-all: a call no other rule covers that plainly reaches outside the working tree |

### `R8-tier-guard`: what happens when the agent is too expensive

The guard asks Jev one question about an `Agent` dispatch: what kind of task
is this, really? It compares the answer to the rung that was chosen, and the
gap decides what happens.

| gap | what happens |
|---|---|
| two or more rungs too expensive | **blocked**, with the cheaper `subagent_type` named in the reason |
| `fable` with no prior failed attempt stated | **blocked** |
| one rung too expensive, confidence >= 0.8 and margin >= 0.4 | **allowed, with a two-line note** saying what was chosen, what Jev judged adequate, and the exact `subagent_type` to use instead |
| below those confidence bars | nothing at all |
| cheaper than the task needs | logged as under-tiered, never blocked, never "corrected" upward |

The one-rung note is new, and it exists because the old behaviour was
useless. On the live log, 20 of 27 judged `Agent` calls were flagged
over-tiered and **not one of them was surfaced**: the row said
`action: deny, enforced: false` and the session never heard a word, to the
point that a later session concluded nothing intercepted the `Agent` tool at
all. A guard nobody can see teaches nobody anything. It reads:

```
airlock tier advice: dispatched 'workerO', but Jev judged this task
'scoped_implementation', which 'workerS' covers. Next time use
subagent_type=workerS.
Advice only -- nothing was blocked and this call ran as you wrote it.
```

Nothing is blocked, nothing is changed, and the dispatch runs exactly as it
was written.

### Rewrite mode (off by default, and here is the catch)

Rewrite mode goes one step further: instead of advising, the hook **edits the
dispatch**, changing `subagent_type` to the adequate rung and leaving every
other field byte-identical. Claude Code supports this -- a `PreToolUse` hook
may return `hookSpecificOutput.updatedInput` alongside
`permissionDecision: "allow"`, which the CLI's own shipped hooks reference
documents as "`updatedInput` - Modified tool input (PreToolUse only)", and
which a live run on CLI 2.1.278 confirmed by executing the rewritten call
rather than the original.

Turn it on with either:

```bash
touch ~/.config/airlock/tier-rewrite     # or: export AIRLOCK_TIER_REWRITE=1
rm ~/.config/airlock/tier-rewrite        # off again
```

It is deliberately stricter than the warn: confidence >= 0.9 and margin >=
0.5, rather than 0.8 and 0.4. It never rewrites upward, never rewrites to an
agent type that is not in the ladder, never rewrites past an
`[airlock-ok: <reason>]` stamp in the Agent description or prompt, and never
rewrites a `fable` dispatch that did state a prior failed attempt. The model
is told what happened and how to override it.

**The honest downside: a wrong downgrade is silent apart from the context
note.** A block is loud -- the work stops and somebody looks. A rewrite is
quiet: if Jev misreads a genuinely hard task as routine, the job runs on a
cheaper agent, the only trace is one line of context the model may not act
on, and what you get back is a worse answer that nothing flagged as worse.
That is the whole reason this is opt-in and the warn is the default. Turn it
on where the cost of one bad downgrade is smaller than the cost of the
over-tiering it prevents, and read the `surfaced: "rewrite"` rows in the log
for a while before trusting it.

### The ladder lives in one file

Agent type names differ per machine. `airlock/tiers.py` holds the built-in
ladder, and `~/.config/airlock/tiers.json` overrides it: an ordered list of
lists of equivalent names, cheapest rung first.

```json
[["scout-find"],
 ["scout"],
 ["workerS", "worker"],
 ["workerO"],
 ["claude", "general-purpose", "Plan", "Explore"],
 ["fable"]]
```

The first name in each list is both the rung's name and the type the guard
suggests or rewrites to, so put the one you actually want dispatched first.
A type that appears nowhere in the ladder is treated as Opus-level. Anything
malformed -- not a list of non-empty lists of strings, fewer than two rungs,
a name in two rungs -- is ignored whole and the built-in ladder is used, so a
broken config can never make the guard misjudge a rung.

`R10-general-risk` is the only fallback rule, and it behaves differently on
purpose. It is consulted **only when no other rule matched the call at all**,
and only when a pure code pre-filter recognises one of six shapes: a write
landing outside the cwd and outside temp, an upload to a non-localhost host
(`curl`/`scp`/`rsync`), a package or release publish, a database CLI carrying
a write verb, service or container control, or a mass file operation globbed
high in the tree. Anything else costs nothing at all. When it does ask, it
asks one Score question about risk (four ordered levels, from read-only and
fully reversible to reaching off the machine) and the same `user_requested`
noul used elsewhere, which can only ever soften. It is `warn` and never
`deny`: a catch-all heuristic blocking things it cannot name is how a guard
becomes something people turn off. Ported in spirit from
[leepokai/jev-guard](https://github.com/leepokai/jev-guard)'s
`ACTION_QUESTIONS`, which asks the same risk score on *every* non-read-only
tool call -- exactly the cost this project exists to avoid, hence the
pre-filter and the fallback-only placement.

Before anything leaves the machine, `airlock/redact.py` replaces token-shaped
strings with `[REDACTED]`, and truncation happens after redaction so a secret
straddling the boundary is not half-leaked.

## Measured numbers

All measured on a 4 vCPU cloud VM in Europe (Ubuntu, 8 GB RAM, Python 3.14)
unless stated.

**Hook latency**, `python3 tests/latency_hook.py 200`, 2026-09-19. Real hook
processes, `HOME` pointed at a temp directory, no API key, so nothing reached
the network:

| payload | median | p95 |
|---|---|---|
| Bash, no rule matches | 33.2 ms | 42.4 ms |
| Write, no rule matches | 26.9 ms | 38.1 ms |
| code-only deny (R6 `xdg-open`) | 37.7 ms | 44.8 ms |
| code-only warn (R3 bare `pytest`) | 36.5 ms | 43.9 ms |

Bare `python3 -c pass` is about 23 ms here, so the hook is dominated by
interpreter start-up. `subprocess`/`tempfile` and `client`/`guards` are
imported lazily for exactly that reason.

**Judgement latency.** Roughly 0.3 s through the warm daemon, against roughly
0.9 s for a fresh DNS + TCP + TLS handshake per call. Both are round figures for
a single call on an idle daemon. The one figure measured end to end here is the
tier eval's: **mean 572 ms per judgement** over 58 real Jev calls at four-way
concurrency, `python3 -m airlock.eval`, 2026-09-19, on the box described above --
which is a throughput number under load, not a single-call latency, and is
quoted only so there is one figure with a method attached to it.

**Rule accuracy**, `python3 -m airlock.eval`, 2026-09-19. Every deny-capable
rule scored 100% with zero false denies on its labelled cases (R1 16 cases,
R2 8, R3 10, R4 11, R5 9, R6 9, R7 10, R9 9), so every rule ships at its
intended action. **A rule with any false deny on its eval cases ships as
`warn`, not `deny`.**

`R10-general-risk` scored **95.2% on 21 labelled cases** (20/21), with zero
false denies -- structurally impossible, since it can only warn. It was 88.9%
(16/18) before the quieting pass that narrowed its pre-filter away from
user-level `systemctl` and read-only `docker`; `eval/README.md` has both runs.
The one remaining miss is an over-warn on an `rsync` into a local backup
directory, which also flaps between runs. Its code pre-filter
fired on **2 of 284 (0.7%)** of the Bash calls in the existing shadow log; it
was actually *consulted* on 0 of them, because every row in that log is a call
some specific rule had already claimed. That is the intended shape -- the
fallback is the residue, not a second opinion.

**Tier-guard accuracy**, `python3 -m airlock.eval`, 2026-09-19. 58 labelled
Agent dispatches, scored on the three outcomes the guard actually has -- block,
warn, silent -- against labels derived from each case's own chosen tier and task
by the ladder: **98.2%** overall (56 of 57 scored; one case is labelled
`ambiguous` and excluded), with **zero false denies and zero missed denies**.
Block 18/18, silent 29/29, warn 9/10. The single miss is a `task_kind` boundary,
not a policy bug, and the direction is safe (a missed warn, never a false
block). `eval/README.md` names it and says why the previous 16-false-deny figure
was a label artefact rather than anything the model got wrong.

**A/B bench**, `bench/results/20260919-120344.md`, 2026-09-19: 30 sessions,
enforce mode against no guard at all, five tasks. Zero denies -- agents
already pick the right tool almost every time. That result is why
`airlock/policy.py` now skips the Jev call entirely when the code-computed
facts make a deny unreachable, and samples 5% of the rest so the tuning loop
still sees ordinary traffic.

**The `subagent_type` ablation**, `python3 eval/ablation.py`, 2026-09-19. Five
groups holding an Agent prompt exactly fixed and varying only the chosen agent
type across the whole ladder, 30 cases: **`task_kind` did not move**. All five
groups stable, 30/30 correct. So the tier guard's state keeps `subagent_type`.
Thirty cases on five prompts is not a general result; re-run it whenever the
tier question's wording changes. See `eval/README.md`.

**Threshold calibration**, `tuning/calibrate.sh`, 2026-09-19: `task_kind` 98.0%
accuracy (ECE 1.9%) over 50 labelled rows, `search_intent` 84.4% (ECE 12.6%)
over 32. Neither got a threshold, because the corpus is too small, not because
the guard is wrong. See `tuning/README.md`.

**The browser component's numbers** are in `browser/README.md`, with their own
dates and their own caveats.

## Known limits

- **The bench found no denies.** The guard's measured value so far is that it
  is cheap and does not get in the way, not that it has saved anything. It is
  worth running in shadow for a week on a new machine and reading the log
  before arming it.
- **Shadow mode is the default, deliberately.** Nothing here blocks anything
  until someone writes `enforce` into the mode file.
- **`review/` is JavaScript and TypeScript only.** Against a Python repository
  it reports very little, which reads exactly like "nothing wrong".
- **`ask` exists but no rule uses it by default.** It was verified empirically
  against Claude Code 2.1.272: `permissionDecision: "ask"` is honoured, but in a
  headless session it is indistinguishable from a deny (the tool does not run
  and the call lands in `permission_denials`), so it degrades to an honest deny
  when nobody is attending. Switch it on per rule in `rules.json` if you want
  it.
- **The eval corpus is too small to calibrate on.** `jevcal` could not pick a
  threshold for either Jev-decided guard: 50 and 32 labelled rows are not
  enough to clear a 99% target with 30 accepted rows. The hand-picked 0.8/0.4
  bar stays. See `tuning/README.md`.
- **`search_intent` is the weaker of the two guards**, at 84.4% on its labelled
  rows with an ECE of 12.6%, against 98.0% and 1.9% for `task_kind`.
- **`shim/`: PageIndex local indexing works through it, chat does not.**
- **Nothing here is a security control.** It is a cost and hygiene guard that
  fails open by design. A control that depends on an agent choosing to obey it
  is not a control; if something must not happen, restrict it at the platform.

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
[`docs/CREDITS.md`](docs/CREDITS.md) shows how far apart they can get.

## Migrating to airlock

Nothing breaks before you migrate. `airlock/paths.py` resolves each directory
newest-name-first and then through every older name in turn -- `airlock`, else
`plumbline`, else `jev-guard` -- so a machine whose `~/.config/plumbline/mode`
(or `~/.config/jev-guard/mode`) says `enforce` keeps enforcing. All three hook
paths work, all three package names import the same objects, every
`PLUMBLINE_*`/`JEV_GUARD_*`/`JEV_TUNE_*`/`JEV_HOME` variable is still accepted,
all three override stamps (`[airlock-ok: ...]`, `[plumbline-ok: ...]`,
`[jev-ok: ...]`) are honoured, and the kill-switch file is honoured under any
of the three config directories.

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
python3 -m airlock.eval --json             # the labelled cases, real calls
python3 -m airlock.report                  # summarise the shadow log
```

## The API key

Read by `airlock/keyfile.py` with plain Python file I/O -- never shelled out,
never put on a command line. It is never logged, printed, or written anywhere.
Nothing in this repository contains a key, and nothing here should ever be made
to print one.

**The resolution order lives in exactly one place: the module docstring of
`airlock/keyfile.py`.** In outline: the environment, then `AIRLOCK_KEY_FILE`,
then the default `~/.config/airlock/env`, then the path recorded in the pointer
file `~/.config/airlock/keyfile.path`, then `AIRLOCK_LEGACY_KEY_FILES`. Do not
restate the order anywhere else; read it there.

There are two implementations of that order and no third: `airlock/keyfile.py`
for Python (the guard, the daemon, the health check, the eval) and
`install/keyfile.sh` for the shell, which `install/install.sh`, `belay/run.sh`,
`belay/install.sh`, `monitoring/run_health_check.sh` and
`compaction/install.sh` all source rather than each keeping a copy.

`install/install.sh` writes the pointer file because a Claude Code hook runs
with a bare environment and never sources `install/config.env`, so on a machine
whose key is not at the default path there is otherwise no way to tell the hook
where it is. The pointer holds a path and never a value. Because whoever can
write it chooses which file the hook parses for a secret, it is followed only
when it and its directory are owned by you and are not group- or
world-writable, and when it names an absolute path to an existing regular file;
otherwise it is ignored, resolution carries on, and `install/doctor.sh` says
why. R1 protects both the pointer and whatever it names, resolved at hook time
so a pointer written after a release was deployed is still covered.
