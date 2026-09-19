# What is in the kit

One row per component, with what it does, whether it is installed by default,
what it sends off the machine, and how much use it has actually had.

Every component is optional except the guard. Two columns are worth reading
before you install anything: **Exercised**, which says how much use and testing
each piece has actually had, and **What leaves the machine**.

`Exercised` means: unit tests, a labelled eval, a measured bench, and real daily
use on at least one machine. `Experimental` means the logic has unit tests and
the component has been run by hand, but it has no labelled corpus, no measured
numbers, and no sustained real use -- keep it, read its README's banner, and do
not build anything load-bearing on it yet.

## Guards

| Component | What it does | Default | Exercised | What leaves the machine |
|---|---|---|---|---|
| **Airlock, the tool-call guard** (`airlock/` + `hooks/`) | The rules table, policy, redaction, client and health. The `PreToolUse` entry point. | **yes** (`--guard`) | exercised | Redacted summaries of the ambiguous fraction of tool calls, to TypeSafe. Never a raw tool result; every string passes through `airlock/redact.py` first. |
| **Tier guard** (`airlock/tiers.py`, rule `R8-tier-guard`) | Compares the agent rung a dispatch chose against the kind of task Jev judges it to be, and warns, blocks or (opt-in) rewrites. | part of the guard | exercised | The `Agent` dispatch's description and prompt, redacted. |
| **Belay** (`belay/`) | Wrapper for the community `jev-belay` Stop hook: when an agent claims it is finished with no passing check behind it, sends it back to verify. | **yes** (`--belay`) -- clones a pinned third-party repo | exercised | Task text, final assistant message and check command lines, through a 13-rule secret redactor, capped at a few thousand characters. No diffs, no file contents. |

## Speed and cost

| Component | What it does | Default | Exercised | What leaves the machine |
|---|---|---|---|---|
| **Warm daemon** (`deploy/`) | Keeps a warm connection so a judgement costs about 0.3 s instead of about 0.9 s. | **yes** (`--daemon`) | exercised | Nothing of its own. It is the transport the guard already uses. |
| **Compaction** (`compaction/`) | Installer for the community `fast-jev-compaction` plugin. **Read `compaction/README.md` first.** | no (`--compaction`) | never enabled here | **Up to roughly 25,000 tokens of raw, unredacted tool inputs and tool-result text per request.** By far the largest exposure here, which is why it is never installed for you. |
| **File search** (`filesearch/`) | Per-user `plocate` index of `$HOME` and its hourly timer, so R8 can suggest an indexed search. | **yes** (`--filesearch`) | exercised | Nothing. Entirely local. |

## Uses

| Component | What it does | Default | Exercised | What leaves the machine |
|---|---|---|---|---|
| **Browser agent** (`browser/`) | Clones and patches a Jev-decided browser agent at a pin. | no (`--browser`) | own numbers, see `browser/README.md` | Page state and goals to the decision model you configure. |
| **Review** (`review/`) | Clones a Jev code reviewer at a pin, with a fail-open wrapper. | no (`--review`) | no numbers here | Diffs, to whatever review gate you configure. |
| **Document classifier** (`docclass/`) | Two-stage document classifier with an escape hatch and a confidence gate. | no | **experimental** | Page text, to TypeSafe, when you call it. |
| **Log triage** (`logtriage/`) | Redact-first, local-rules-first log triage on stdin. | no | **experimental** | Nothing until local rules are exhausted; redacted lines after that. |
| **Shim** (`shim/`) | An OpenAI-shaped HTTP shim over the `claude` CLI. | no (`--shim`) | **experimental** | Whatever you send through it. |

## Operations

| Component | What it does | Default | Exercised | What leaves the machine |
|---|---|---|---|---|
| **Installer** (`install/`) | One installer, a doctor, deploy/rollback/wire, the migration script. | n/a | exercised | Nothing. |
| **Monitoring** (`monitoring/`) | Five-minute health check, its timer, and an optional push to a monitor you host. | **yes** (`--monitoring`) | exercised | Nothing, unless you set `AIRLOCK_KUMA_PUSH_URL`, in which case a bare liveness ping to that URL. |
| **Tuning loop** (`tuning/`) | Unattended tuning loop, its timer, promotion and threshold calibration. | no (`--tuning`) | exercised | Real Claude sessions on the account you nominate, for the judge. |
| **Auto-updater** (`claude-update/`) | Idle-only Claude Code auto-updater and its timer. Updates only when no run is alive. | **yes** (`--claude-update`) | exercised | Nothing. An idle check and `npm install -g`. |

## Not components, but in the tree

| Directory | What it holds | Exercised | What leaves the machine |
|---|---|---|---|
| `eval/` | Labelled cases for every rule, and the ablations. | exercised | Nothing. |
| `bench/` | A/B benchmark: enforce mode against no guard at all. | exercised | Nothing until you run it. |
| `docs/` | Credits for the community projects the ported patterns came from, and the install guides. | n/a | Nothing. |

## Why the guard is called Airlock

`airlock` is the name of the guard component, its Python package, its systemd
units and its config directory -- formerly `plumbline`, formerly `jev-guard`.
The first rename was forced: a public project,
[leepokai/jev-guard](https://github.com/leepokai/jev-guard), already uses that
name. Both older names still work through the cutover; see
[MIGRATING-TO-AIRLOCK.md](MIGRATING-TO-AIRLOCK.md).

**Airlock is not a sandbox.** Several projects called "airlock" isolate agents
or untrusted code in a VM or container. This does none of that. It is a policy
hook inside your own session that judges individual tool calls and, at most,
returns a deny to Claude Code.
