# The rules

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

Every deny keeps its safety net: the `[airlock-ok: <reason>]` override stamp
in a call's description, loop protection (the same call is never denied twice
in ten minutes), a hard budget, fail-open on any error, and -- where Jev is
involved -- a confidence of at least 0.8 with a margin of at least 0.4 over the
runner-up.

A deny is also softened to a warn when the user's own recent words asked for the
thing. That check reads only what the person typed, never a tool result or a
fetched page, and it can only ever soften: no answer to it can make the guard
stricter. See `airlock/context.py`.

Before anything leaves the machine, `airlock/redact.py` replaces token-shaped
strings with `[REDACTED]`, and truncation happens after redaction so a secret
straddling the boundary is not half-leaked.

## Turning rules off, or down

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

## `R8-tier-guard`: what happens when the agent is too expensive

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

## `R10-general-risk`: the fallback

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
