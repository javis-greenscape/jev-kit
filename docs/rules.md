# The rules

The hook is registered for **every** tool. What filters a call is not the
matcher but the table in `airlock/rules.py`: one entry per kind of
genuinely-wrong tool use, each with a code pre-filter that runs in
microseconds. **A call no rule covers costs nothing and logs nothing**: no Jev
request, no log row, no subprocess.

| id | tools | default | decided by | what it is for |
|---|---|---|---|---|
| `R1-secret-exposure` | Bash, Read | deny | code, Jev for an ambiguous path | reading or echoing a secret into the transcript |
| `R2-claude-api-skill` | Skill | deny (warn with no stated purpose) | Jev | loading a 324,006-token reference for a price lookup |
| `R3-whole-suite-or-uncapped-build` | Bash | warn | code only | the whole test suite, or a build with uncapped parallelism |
| `R4-long-work-bare-shell` | Bash | warn | code, Jev for the ambiguous cases | long work on a shell a dropped connection would kill |
| `R5-sudo` | Bash | deny | code only | `sudo` outside a named package install, or anywhere under `$HOME` |
| `R6-gui-or-browser` | Bash | **`off` by default on every platform** | code only | opening a GUI or browser on a headless box |
| `R7-destructive` | Bash | warn | code only | force pushes, hard resets, wholesale deletion |
| `R8-tier-guard` | Agent | deny two rungs over, **warn one rung over**, rewrite only if asked | Jev | a task dispatched to an agent more expensive than it needs |
| `R8-tool-choice-guard` | Bash | deny | Jev | a disk-wide filename crawl, or a raw grep where a code graph exists |
| `R9-commit-secret` | Bash | deny | code + a local credential belt | staging or committing a secret |
| `R10-general-risk` | Bash | **warn only, never deny** | code pre-filter, then a Jev Score + `user_requested` | the catch-all: a call no other rule covers that plainly reaches outside the working tree |
| `R11-browse-via-jev` | every tool (Bash and the Playwright MCP server) | deny | code pre-filter, then Jev on browse-or-test | a browse-and-report pass hand-driving Playwright, when the kit already ships a Jev-decided browser agent |

`R8-tool-choice-guard`'s graphify-suggestion branch only exists when the search
root carries a graph (`graphify-out/graph.json`). With no graph the branch is
inert: it never suggests `graphify query` and never denies. That is most
users. Most do not use graphify at all.

Every deny keeps its safety net. The `[airlock-ok: <reason>]` override stamp in
a call's description, loop protection so the same call is never denied twice in
ten minutes, a hard budget, and fail-open on any error. Where Jev is involved,
a deny also needs a confidence of at least 0.8 and a margin of at least 0.4
over the runner-up.

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
ask. That was verified against Claude Code 2.1.272, not assumed.

## `R8-tier-guard`: what happens when the agent is too expensive

The guard asks Jev one question about an `Agent` dispatch: what kind of task
this really is. It compares the answer to the rung that was chosen, and the gap
decides what happens.

| gap | what happens |
|---|---|
| two or more rungs too expensive | **blocked**, with the cheaper `subagent_type` named in the reason |
| `fable` with no prior failed attempt stated | **blocked** |
| one rung too expensive, confidence >= 0.8 and margin >= 0.4 | **allowed, with a two-line note** saying what was chosen, what Jev judged adequate, and the exact `subagent_type` to use instead |
| below those confidence bars | nothing at all |
| cheaper than the task needs | logged as under-tiered, never blocked, never "corrected" upward |

The one-rung note is new, and it exists because the old behaviour was useless.
On the live log, 20 of 27 judged `Agent` calls were flagged over-tiered and
**not one of them was surfaced**. The row said `action: deny, enforced: false`
and the session never heard a word. A later session went on to conclude that
nothing intercepted the `Agent` tool at all. A guard nobody can see teaches
nobody anything. It reads:

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
other field byte-identical. Claude Code supports this: a `PreToolUse` hook may
return `hookSpecificOutput.updatedInput` alongside
`permissionDecision: "allow"`. The CLI's own shipped hooks reference documents
it as "`updatedInput` - Modified tool input (PreToolUse only)", and a live run
on CLI 2.1.278 confirmed it by executing the rewritten call rather than the
original.

Turn it on with either:

```bash
touch ~/.config/airlock/tier-rewrite     # or: export AIRLOCK_TIER_REWRITE=1
rm ~/.config/airlock/tier-rewrite        # off again
```

The bars are stricter than the warn's: confidence >= 0.9 and margin >= 0.5,
rather than 0.8 and 0.4. Four things it never does:

- rewrite upward;
- rewrite to an agent type that is not in the ladder;
- rewrite past an `[airlock-ok: <reason>]` stamp in the Agent description or
  prompt;
- rewrite a `fable` dispatch that did state a prior failed attempt.

The model is told what happened and how to override it.

**The downside: a wrong downgrade is silent apart from the context note.** A
block is loud, because the work stops and somebody looks. A rewrite is quiet.

If Jev misreads a genuinely hard task as routine, the job runs on a cheaper
agent. The only trace is one line of context. You get back a worse answer, and
nothing flagged it as worse.
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
A type that appears nowhere in the ladder is treated as Opus-level. A
malformed ladder is ignored whole and the built-in one is used, so a broken
config can never make the guard misjudge a rung. Malformed means any of these:
not a list of non-empty lists of strings, fewer than two rungs, or one name in
two rungs.

## `R11-browse-via-jev`: browsing goes through the browser agent

The kit ships a Jev-decided browser agent (`browser/`, which clones
jev-ultrafast at a pin). On the same nine goals it reaches everything Sonnet
reaches for roughly 1/233rd of the Claude spend per run. Nothing steered
anybody to it, so sessions kept writing their own Playwright scripts at Sonnet
prices. This rule is that steer, and it is on by default.

The code pre-filter recognises three ways of driving a browser:

- a Playwright MCP tool call, under either name the server is registered with
  (`mcp__playwright__*`, `mcp__plugin_playwright_playwright__*`), and only for
  the navigate / click / type / snapshot family. `browser_install` and
  `browser_close` are not browsing and never match. The hook is wired with
  matcher `*`, so a non-Bash tool call reaches the table with its name and its
  `tool_input`, which is all this needs;
- `playwright <verb>` or `npx playwright <verb>` for any verb other than
  `test`, `install`, `install-deps`, `uninstall` and `show-report`;
- a shell command running a script that imports `playwright` or
  `playwright-core`, whether the script is a file (`node verify.cjs`,
  `python3 verify.py`, `uv run python3 verify.py`) or inline (`node -e`,
  `python3 -c`). `npm run <name>` is followed one step into `package.json`,
  because the script name alone says nothing, unless the name itself says
  tests (`test`, `test:e2e`, `e2e`, `spec`). Yarn and pnpm let the verb be
  left out, so `yarn scrape` is followed the same way and `yarn playwright
  open` is read as the binary it runs.

This is the one rule that reads a file the command names, because `node
verify.cjs` says nothing about Playwright from the command line alone. The
read is bounded: one `isfile`, one size check, at most 256KB, and only for a
segment that actually runs a file with a script extension.

Three things never match at all. A test run is e2e code, not browsing:
`playwright test`, `vitest`, `jest`, `pytest`, `npm test`, `pnpm run test:e2e`,
and a spec file run straight through an interpreter (`node e2e/login.spec.js`),
which is how somebody debugs one.
A script that does not import Playwright is nothing to do with this rule. And
a command that is already running the Jev agent is the thing the rule asks
for, so it is never the thing the rule catches: a script resolving inside the
agent's own checkout, or a segment that assigns or exports `BU_CDP_URL`, which
only the harness reads.

Those two exemptions are not the same strength, and the difference is
deliberate. A path into the checkout is evidence. `BU_CDP_URL` is a
declaration: setting it exempts the segments after it whatever they then run,
because the recipe's own runner script lives wherever the person put it rather
than inside the checkout, and requiring the checkout path there would flag the
very workflow this rule recommends. So it is an escape hatch somebody can type
on purpose, sitting beside the `[airlock-ok: <reason>]` stamp and the off
switch. This rule is a cost steer that fails open, not a lock, and the safety
model says the same of every rule here.

Both an assignment and a `cd` carry into the segments after them. Neither
blesses the command sharing its own segment, so `BU_CDP_URL=... node
hand-rolled.js` is still a hand-rolled script unless its path says otherwise,
and a bare mention of the agent or the variable in an `echo` or a heredoc
exempts nothing at all.

When the pre-filter fires, Jev is asked one question: is this a
browse-and-report pass, or is it writing or running test code? Only
`browse_and_report` denies, and only at the usual bar of confidence 0.8 and
margin 0.4. The deny prints the recipe measured on 2026-09-20: log in with a
small script that reads the credential inside the process, leave headless
Chromium on a CDP port, run each goal through jev-ultrafast with `BU_CDP_URL`,
and read the result with a small DOM extraction, because Jev decides
operations and does not narrate a page. Three LinkOne goals that way each
finished in under three seconds with no Claude decision calls at all.

**It never blocks when Jev cannot answer.** No key, no tokens, a timeout, an
error of any kind: the call is allowed, and the recipe is printed as advice
instead. That is the one place this rule differs from the others, which stay
silent on an error. Turn the whole thing off with
`{"R11-browse-via-jev": "off"}` in `~/.config/airlock/rules.json`, or get past
one call with an `[airlock-ok: <reason>]` stamp.

## `R10-general-risk`: the fallback

`R10-general-risk` is the only fallback rule, and it behaves differently on
purpose. It is consulted **only when no other rule matched the call at all**,
and only when a pure code pre-filter recognises one of six shapes:

- a write landing outside the cwd and outside temp;
- an upload to a non-localhost host (`curl`, `scp`, `rsync`);
- a package or release publish;
- a database CLI carrying a write verb;
- service or container control;
- a mass file operation globbed high in the tree.

Anything else costs nothing at all. When it does ask, it asks one Score
question about risk, over four ordered levels running from read-only and fully
reversible up to reaching off the machine. It also asks the same
`user_requested` noul used elsewhere, which can only ever soften.

It is `warn` and never `deny`. A catch-all heuristic blocking things it cannot
name is how a guard becomes something people turn off. It is ported in spirit
from [leepokai/jev-guard](https://github.com/leepokai/jev-guard)'s
`ACTION_QUESTIONS`, which asks the same risk score on *every* non-read-only
tool call. That is the cost this project exists to avoid, hence the pre-filter
and the fallback-only placement.
