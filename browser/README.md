# browser: the Jev-decided browser agent, vendored

The agent itself lives at [`vendor/jev-ultrafast`](../vendor/jev-ultrafast),
part of this repository. This directory is what sets it up: one script that
syncs its Python environment. Nothing is cloned and no patch is applied any
more, so a fresh checkout already has the agent.

| | |
|---|---|
| Upstream | [browser-use/jev-ultrafast](https://github.com/browser-use/jev-ultrafast), MIT |
| Vendored at | `vendor/jev-ultrafast`, added with `git subtree` |
| Pinned commit | `1231850` |
| Our work | seven commits on top of that pin, in this repository's history |
| Upstream licence | `vendor/jev-ultrafast/LICENSE`, and [`NOTICE`](../NOTICE) |

It used to be a clone plus six `git format-patch` files, kept that way to make
future upstream merges easy. `git subtree` keeps that and drops the rest: a new
user gets the agent with the repository, our changes are ordinary commits
rather than patch files to regenerate, and `git subtree pull` still merges a
newer upstream.

A session drives it through the `browse` MCP tool in
[browse/](../browse/README.md), and the guard routes browsing there. Airlock's
`R11-browse-via-jev` rule matches a Playwright MCP browsing call by its tool
name and denies it with the usage of `browse`. It is on by default and code
only, so Jev is never asked.
[docs/rules.md](../docs/rules.md#r11-browse-via-jev-a-playwright-mcp-call-is-pointed-at-browse)
has the detail and the off switch.

## Install

```bash
browser/install.sh                    # sync the agent's dependencies
browser/install.sh --venv /some/dir   # or into an environment of your choosing
```

Needs `uv`. It syncs the dependencies into
`$AIRLOCK_HOME/jev-ultrafast-venv`, not into a `.venv` beside the source. A
deployed release is an immutable export that `install/deploy.sh` prunes, so a
venv inside one would be rebuilt on every deploy and thrown away again. One
environment outside the releases is shared by the checkout and by every
release, and `install/deploy.sh` re-syncs it only when `uv.lock` changes. The
project itself is not installed into it: `browse/server.py` points the child at
whichever source tree it resolved, through `PYTHONPATH`.

An older hand-made clone at `~/code/jev-ultrafast` is left exactly as it is.
Nothing here touches it, and `JEV_ULTRAFAST_DIR` is how you point `browse` at
one if you want to.

## Pulling a newer upstream

```bash
git subtree pull --prefix vendor/jev-ultrafast \
  https://github.com/browser-use/jev-ultrafast <commit> --squash
```

Our seven commits already sit on top of the pin, so a pull is a merge and
whatever conflicts is a conflict you resolve once rather than a patch to
rewrite. Afterwards: update the pin in this file and in [`NOTICE`](../NOTICE),
run the vendored project's own tests, and run the `browse` suite.

```bash
PYTHONPATH=vendor/jev-ultrafast \
  "$HOME/.local/share/airlock/jev-ultrafast-venv/bin/python" \
  -m pytest vendor/jev-ultrafast        # 33 tests
python3 -m unittest tests.test_browse   # the kit's own side
```

## What our commits add

1. **Headless CDP attach** (`BU_CDP_URL`), so the harness drives a Chromium
   we launched rather than launching its own.
2. **A Claude text-model adapter** for `TYPE_TEXT` fields, per-call
   (`claude-cli`) and as a standing child process (`claude-standing`), going
   through the local `claude` CLI, because this box has OAuth, not an API key.
3. **Thinking off and a trimmed context**, the two changes that made the
   standing child the fastest path measured.
4. **Claude as a pluggable decision-maker**, so Jev, Haiku and Sonnet can be
   benchmarked against each other with everything else held identical.
5. **The benchmark itself**, including the plain-Playwright-MCP arm.
6. **Off-screen links**, offered to the chooser and scrolled into view before
   a click, and the first scroll after a navigation no longer dropped. A long
   article stopped being a dead end: a two-hop Wikipedia link-navigation task
   went from blocked to done in 7.2 s.

## Measured results

From the vendored project's own `SPIKE-NOTES.md`, measured on this box on
**2026-09-19**. Three goals, three arms, three repetitions each: 27 runs,
strictly sequential, arms interleaved, one shared headless Chromium.
**n=3 per cell, so read every cell as directional rather than significant.**

### Decision-maker: Jev versus Claude

| Arm | Success | Decision latency, median |
|---|---|---|
| Jev (TypeSafe `systemone`) | **9/9** | **314-486 ms** across the three goals |
| Claude Sonnet | 9/9 | 1.1-1.5 s |
| Claude Haiku | **4/9** | 0.76-2.8 s |

Jev was faster on every goal, by roughly 2-9x. Haiku's failures were
instruction-following rather than speed. It returned unparseable non-JSON
against a large element table: 0/3 on the search goal, 1/3 on the multi-step
goal. It succeeded reliably only on the click-only goal, which has the smallest
element table. Sonnet was accurate but 2-3x slower than Jev.

### The text-model fix

Two levers, stacked, on the standing-child adapter:

- `MAX_THINKING_TOKENS=0`, which Claude Code honours even though no CLI flag
  exposes it. Latency on a realistic full context went from 2.3-6.2 s to
  **0.6-1.2 s**, with `thinking_tokens` reported as 0 on every call and the
  correct value returned every time.
- **A trimmed context** (`TEXT_MODEL_CONTEXT=trimmed`, now the default): the
  goal, the chosen element's own row from the element table, its five nearest
  rows, the page title and URL. No page body text.

End to end on the README Wikipedia goal with both live: **7.5 s wall** and a
**739 ms** `TYPE_TEXT` fill. The previous best was 9.3-9.8 s wall and 4761 ms
fill, and the standing-child regression they replaced ran 11.0-12.6 s wall with
a 6.55 s fill.

One caveat recorded rather than hidden: with thinking off, sending the *exact
same* ambiguous prompt repeatedly through one long-lived child degraded (the
answer flipped, then returned `NONE`). Four different cases run twice through
one shared child answered correctly 8/8, and a fresh child per request
answered 5/5. Real traffic does not produce byte-identical contexts, so this
was not treated as a correctness failure.

### Claude token cost, with Jev and without

Per-run medians, n=3, from the same sweep. "Claude cost" is the CLI's own
`total_cost_usd`, never tokens multiplied by a price.

**G1, find and open the Gödel's incompleteness theorems article**

| Arm | Success | Wall (s) | Claude in | Claude out | Claude cache-read | Claude cost USD | Jev in | Jev out |
|---|---|---|---|---|---|---|---|---|
| Jev | 3/3 | 4.87 | 687 | 13 | 0 | 0.0008 | 38,254 | 2,896 |
| Haiku decides | 1/3 | 7.30 | 3,821 | 90 | 5,458 | 0.0344 | 0 | 0 |
| Sonnet decides | 3/3 | 9.32 | 699 | 133 | 35,974 | 0.1868 | 0 | 0 |
| Plain Playwright MCP | 3/3 | 12.85 | 6 | 295 | 136,211 | 0.0376 | 0 | 0 |

**G2, open the 'Create account' page (click only)**

| Arm | Success | Wall (s) | Claude in | Claude out | Claude cache-read | Claude cost USD | Jev in | Jev out |
|---|---|---|---|---|---|---|---|---|
| Jev | 2/3 | 5.24 | 0 | 0 | 0 | no Claude call | 19,138 | 1,490 |
| Haiku decides | 3/3 | 6.26 | 5,901 | 87 | 11,817 | 0.0773 | 0 | 0 |
| Sonnet decides | 3/3 | 7.70 | 8 | 72 | 19,417 | 0.0364 | 0 | 0 |
| Plain Playwright MCP | 0/3 | 6.06 | 2 | 160 | 44,956 | 0.0276 | 0 | 0 |

**G3, search, open the article, open its Talk page (multi-step)**

| Arm | Success | Wall (s) | Claude in | Claude out | Claude cache-read | Claude cost USD | Jev in | Jev out |
|---|---|---|---|---|---|---|---|---|
| Jev | 3/3 | 8.27 | 686 | 5 | 0 | 0.0007 | 53,701 | 4,182 |
| Haiku decides | 1/3 | 9.49 | 6,624 | 127 | 23,048 | 0.1245 | 0 | 0 |
| Sonnet decides | 3/3 | 14.68 | 700 | 139 | 63,247 | 0.3727 | 0 | 0 |
| Plain Playwright MCP | 3/3 | 10.95 | 8 | 459 | 184,193 | 0.0556 | 0 | 0 |

Reading it: with Jev, the Claude bill for the decision loop is close to zero,
because the only Claude calls left are the cheap `TYPE_TEXT` fills. Without
Jev, every decision is itself a Claude call. Note also that the plain
Playwright-MCP arm is the most Claude-token-hungry of all by cache-read tokens,
at 136k-184k per run, while reporting a *lower* cost than Sonnet-as-decider.
Cache reads are cheap per token, and a token count is not a cost.

Jev's own usage is denominated in TypeSafe tokens, not Claude's. TypeSafe's
published price, quoted as-is from <https://docs.typesafe.ai/models.md>:
**"Price (per Btok / per Mtok) | $42 / $0.042"**, with "Charged per input
token. Output tokens are free." The arithmetic is left to the reader.

## Launching headless Chromium

There is no desktop on this kind of box: Chromium runs headless or not at all,
and only Chromium (Firefox and WebKit were removed on purpose). Run **one
instance at a time** and close it afterwards.

```bash
# If playwright is installed globally rather than beside the agent, node needs
# to be told where to find it:
export NODE_PATH=$HOME/.npm-global/lib/node_modules

node vendor/jev-ultrafast/scripts/launch_chromium.js &
CHROMIUM_PID=$!

export BU_CDP_URL=http://127.0.0.1:9333
PYTHONPATH=vendor/jev-ultrafast \
  "$HOME/.local/share/airlock/jev-ultrafast-venv/bin/python" \
  vendor/jev-ultrafast/examples/run.py

kill "$CHROMIUM_PID"
pgrep -a chrom    # should print nothing
```

`scripts/launch_chromium.js` starts Chromium with
`--remote-debugging-port=9333` and stays alive until killed. The harness
attaches over CDP and opens its own tab per run.

Check `free -h` before launching: a Chromium plus the harness wants a couple
of gigabytes, and this box swaps rather than fails.

## Adding more of our own work

Commit it to this repository like anything else. It lands on top of the pin by
construction, and `git subtree pull` merges upstream around it. Keep absolute
paths to a particular user's home directory or Claude account tree out of it,
the same rule the patch files used to carry.
