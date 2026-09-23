# Measured numbers

Every number here was measured in this repository, on a stated date, by a
stated command. Nothing is extrapolated and nothing is a vendor claim. All
measured on a 4 vCPU cloud VM in Europe (Ubuntu, 8 GB RAM, Python 3.14) unless
stated.

## Hook latency

`python3 tests/latency_hook.py 200`, 2026-09-19. Real hook processes, `HOME`
pointed at a temp directory, no API key, so nothing reached the network:

| payload | median | p95 |
|---|---|---|
| Bash, no rule matches | 33.2 ms | 42.4 ms |
| Write, no rule matches | 26.9 ms | 38.1 ms |
| code-only deny (R6 `xdg-open`, pinned on) | 37.7 ms | 44.8 ms |
| code-only warn (R3 bare `pytest`) | 36.5 ms | 43.9 ms |

Bare `python3 -c pass` is about 23 ms here, so the hook is dominated by
interpreter start-up. `subprocess`/`tempfile` and `client`/`guards` are
imported lazily for exactly that reason.

## Judgement latency

Roughly 0.3 s through the warm daemon, against roughly 0.9 s for a fresh DNS +
TCP + TLS handshake per call. Both are round figures for a single call on an
idle daemon.

One figure here was measured end to end: the tier eval's **mean 572 ms per
judgement**, over 58 real Jev calls at four-way concurrency (`python3 -m
airlock.eval`, 2026-09-19, on the box described above). That is throughput
under load, not single-call latency. It is quoted because it is the one
judgement figure with a method attached.

### On native Windows, with no warm daemon

2026-09-19, Windows Python **3.11.9** on a Windows 11 workstation, a real key
in a throwaway profile, enforce mode, real `PreToolUse` events piped at the
installed launcher. **32 judged calls** (mixed `R8-tool-choice-guard` and
`R8-tier-guard`), latency as the guard itself recorded it in its log:

| | ms |
|---|---|
| min | 953 |
| median | 1030 |
| p95 | 1092 |
| max | 1359 |

**One of the 32 did not complete**: `_ssl.c:989: The handshake operation timed
out`. It blew the then-1500 ms enforce budget and **fail-opened**, a 3.1%
fail-open rate in this sample. Windows has no warm daemon, so every judgement
pays a fresh DNS + TCP + TLS handshake against a budget chosen for a Linux box
that has one.

The decision from these numbers: the default enforce budget on native Windows
is now **2000 ms** (`airlock/enforce.py:WINDOWS_DEFAULT_BUDGET_MS`), clearing
the measured 1359 ms max with room to spare. POSIX, WSL and macOS keep 1500 ms.
See [native-windows.md](native-windows.md).

The health check's own direct HTTPS probe on the same machine and the same
session: `direct_ask {ok: true, latency_ms: 1125}`, `status=healthy`.

## Rule accuracy

`python3 -m airlock.eval`, 2026-09-19. Every deny-capable rule scored 100%
with zero false denies on its labelled cases: R1 16 cases, R2 8, R3 10, R4 11,
R5 9, R6 9, R7 10, R9 9. So every rule ships at its intended action. **A rule
with any false deny on its eval cases ships as `warn`, not `deny`.**

`R10-general-risk` scored **95.2% on 21 labelled cases** (20/21), with zero
false denies. A false deny is structurally impossible there, because the rule
can only warn.

It scored 88.9% (16/18) before the quieting pass that narrowed its pre-filter
away from user-level `systemctl` and read-only `docker`. Both runs are in
`eval/README.md`. The one remaining miss is an over-warn on an `rsync` into a
local backup directory, and it flaps between runs.

Its code pre-filter fired on **2 of 284 Bash calls in the existing shadow
log**, 0.7%. It was *consulted* on none of them, because every row in that log
is a call some specific rule had already claimed. That is the intended shape:
the fallback is the residue, not a second opinion.

## Tier-guard accuracy

`python3 -m airlock.eval`, 2026-09-19. 58 labelled Agent dispatches, scored on
the guard's three real outcomes: block, warn, silent. The labels come from each
case's own chosen tier and task, read through the ladder.

**98.2%** overall, 56 of 57 scored, with **zero false denies and zero missed
denies**. One case is labelled `ambiguous` and excluded. Block 18/18, silent
29/29, warn 9/10.

The single miss is a `task_kind` boundary rather than a policy bug, and it
misses in the safe direction: a missed warn, never a false block.
`eval/README.md` names it, and says why the previous 16-false-deny figure was a
label artefact rather than anything the model got wrong.

## A/B bench: the guard against no guard

`bench/results/20260919-120344.md`, 2026-09-19: 30 sessions, enforce mode
against no guard at all, five tasks. **Zero denies**: agents already pick the
right tool almost every time.

Read it as a backstop, not a tax. The measured value of the guard so far is
that it is cheap and stays out of the way, not that it has saved anything. That
result is also why `airlock/policy.py` now skips the Jev call when the
code-computed facts make a deny unreachable, and samples 5% of the rest so the
tuning loop still sees ordinary traffic.

## The `subagent_type` ablation

`python3 eval/ablation.py`, 2026-09-19. Five groups holding an Agent prompt
exactly fixed and varying only the chosen agent type across the whole ladder,
30 cases: **`task_kind` did not move**. All five groups stable, 30/30 correct.
So the tier guard's state keeps `subagent_type`. Thirty cases on five prompts
is not a general result; re-run it whenever the tier question's wording
changes. See `eval/README.md`.

## Threshold calibration

`tuning/calibrate.sh`, 2026-09-19: `task_kind` 98.0% accuracy (ECE 1.9%) over
50 labelled rows, `search_intent` 84.4% (ECE 12.6%) over 32. Neither got a
threshold, because the corpus is too small, not because the guard is wrong.
See `tuning/README.md`.

## The browser component

From the upstream clone's own `SPIKE-NOTES.md`, measured on this box on
2026-09-19. Three goals, three arms, three repetitions each: 27 runs, strictly
sequential, arms interleaved, one shared headless Chromium. **n=3 per cell, so
read every cell as directional rather than significant.**

| Arm | Success | Decision latency, median |
|---|---|---|
| Jev (TypeSafe `systemone`) | **9/9** | **314-486 ms** across the three goals |
| Claude Sonnet | 9/9 | 1.1-1.5 s |
| Claude Haiku | **4/9** | 0.76-2.8 s |

On cost, "Claude cost" is the CLI's own `total_cost_usd`, never tokens
multiplied by a price. Per-run medians, n=3, from the same sweep:

| Goal | Jev arm | Sonnet-decides arm | Haiku-decides arm |
|---|---|---|---|
| G1, find and open an article | 3/3, 4.87 s, **0.0008 USD** | 3/3, 9.32 s, 0.1868 USD | 1/3, 7.30 s, 0.0344 USD |
| G2, click-only navigation | 2/3, 5.24 s, **no Claude call** | 3/3, 7.70 s, 0.0364 USD | 3/3, 6.26 s, 0.0773 USD |
| G3, multi-step search and Talk page | 3/3, 8.27 s, **0.0007 USD** | 3/3, 14.68 s, 0.3727 USD | 1/3, 9.49 s, 0.1245 USD |

On goal 1, the cleanest of the three, Jev's Claude spend is 233 times smaller
than Sonnet's (0.1868 against 0.0008, rounded down). That ratio is the headline
figure in the README.

With Jev, the Claude bill for the decision loop is close to zero, because the
only Claude calls left are the cheap `TYPE_TEXT` fills. Without Jev, every
decision is itself a Claude call. Jev's own usage is denominated in TypeSafe
tokens, not Claude's. The full tables, all four arms, the `MAX_THINKING_TOKENS`
finding and the caveats are in `browser/README.md`, with their own dates.

## The `browse` tool, warm

Measured 2026-09-23 through the installed MCP server over stdio, several calls
in one server session. The goal is the vendored README's own example: open the
Wikipedia article on Gödel's incompleteness theorems, starting from the main
page. Upstream reports 2.798 s for it.

| | before | after |
|---|---|---|
| first call in a server session | 14.4 s | 8.1 s |
| calls after the first (n=4) | 14.4 s | 5.0-6.1 s |
| writing one field value | 7.9 s | 0.72-0.98 s |
| one Jev decision, median | ~700 ms | 537 ms |

Before is a fresh agent process per call falling back to the per-call `claude`
CLI adapter. After is one long-lived worker holding a warm Haiku child, a warm
`browser_harness` daemon and a warm connection to the decision endpoint.

Roughly 410 ms of every decision is this box's own network floor: a bare HTTPS
GET to the decision endpoint takes that long from here, and TCP to the edge
takes 24 ms of it. Five decisions is therefore about 2.8 s that no amount of
warming removes.

Two other goals in the same session: a link far below the fold of a long
article, 3/3 at 3.5-5.9 s; `example.com`, 1 step, 1.4 s including starting
Chromium. The sweep behind the `JEV_OFFSCREEN_MAX` default, the daemon
comparison and the duplicate-decision trace are in the vendored
`SPIKE-NOTES.md`.

## The Wikipedia suite

Eight tasks, three runs of each per arm, on 23 September 2026. Release
`380aafa`, which carries the documentation fixes, is what the three fast arms
ran against. Their "before" figures come from release `9adf232` earlier that
day. The two Claude Code arms are reused from the sweep against
release `16c46df` and were not rerun: nothing since touches how they drive the
page.

Every arm had the same goal text and the same 180 second budget, and no two
arms ran at the same time.

- **jev** is Jev alone through this kit's own `browse` server, called over one
  MCP session held open for the whole sweep, the way a real client reuses it.
- **sonnet-low plans, `browse` executes** is `browse` with `plan: true`, its
  opt-in plan mode and the default planner. One long-lived `claude -p` child on
  Sonnet, effort low and thinking off, with no tools and no settings, sees the
  task, the current URL and title, and the element table Jev is choosing from.
  It answers with one line (`CLICK`, `TYPE`, `FIND` or `DONE`), `browse`
  executes that line, and the loop repeats up to twelve times. `FIND <text>`
  returns the links anywhere on the page that match the text.
- **haiku plans, `browse` executes** is the same plan mode with the planner
  model switched to Haiku (`JEV_PLANNER_MODEL=haiku`).
- **sonnet + Playwright MCP** (reused) is `claude -p --model sonnet` holding the
  Playwright MCP server and nothing else, driving the page a click at a time.
- **sonnet planning, `browse` executing** (reused) is the same isolated Sonnet
  session with `browse` as its only tool, handing `browse` one or two explicit
  steps at a time.

Tasks, checks and runner are in
[vendor/jev-ultrafast/bench](../vendor/jev-ultrafast/bench). The raw rows sit
beside them: `results-wiki-20260923T154632Z.jsonl` for the fast arms after the
fixes, `results-wiki-20260923T135921Z.jsonl` for them before, and
`results-wiki-20260923T113839Z.jsonl` for the two reused arms.

**Group A is navigation with every hop named**, ending "Stop when the X article
is open". Passing means the final URL is the target article. The scorer reads
the fact off that final page, the same way for every arm, and no arm is ever
asked for it in its own words.

| arm | pass rate | before the fixes | median s (passes) | before | p90 s | median cost USD |
|---|---|---|---|---|---|---|
| jev via `browse` | 15/18 (83%) | 11/18 (61%) | 7.1 | 6.3 | 11.3 | not reported by `browse` |
| haiku plans, `browse` executes | 15/18 (83%) | 12/18 (67%) | 17.4 | 12.0 | 22.4 | $0.044 |
| sonnet-low plans, `browse` executes (default planner) | 16/18 (89%) | 11/18 (61%) | 16.4 | 11.8 | 29.9 | $0.048 |
| sonnet + Playwright MCP (reused) | 17/18 (94%) | | 26.0 | | 42.8 | $0.099 |
| sonnet planning, `browse` executing (reused) | 12/18 (67%) | | 34.4 | | 150.7 | $0.080 |

| task | jev | haiku plans | sonnet-low plans | sonnet + Playwright (reused) | sonnet plans (reused) |
|---|---|---|---|---|---|
| A1 chlorophyll, two link hops to Chlorophyll a | 3/3 (3/3) | 3/3 (3/3) | 3/3 (3/3) | 3/3 | 3/3 |
| A2 chess loser, his birth city, its founding year (1703) | 1/3 (0/3) | 0/3 (0/3) | 1/3 (0/3) | 2/3 | 2/3 |
| A3 Python's creator, the company he joined, its founding year (1975) | 3/3 (3/3) | 3/3 (3/3) | 3/3 (3/3) | 3/3 | 2/3 |
| A4 Feynman's doctoral advisor, his birth year (1911) | 3/3 (0/3) | 3/3 (0/3) | 3/3 (0/3) | 3/3 | 0/3 |
| A5 search Kilimanjaro, first to the summit, his nationality (German) | 2/3 (2/3) | 3/3 (3/3) | 3/3 (2/3) | 3/3 | 2/3 |
| A6 bicycle wheel, two link hops to Axle | 3/3 (3/3) | 3/3 (3/3) | 3/3 (3/3) | 3/3 | 3/3 |

The figure in brackets is the same arm before the fixes.

**Group B is a start article and a goal article with no hops named**, which is
not what `browse`'s Jev chooser is built for on its own.

| arm | pass rate | before the fixes | median s (passes) | before | p90 s | median cost USD |
|---|---|---|---|---|---|---|
| jev via `browse` | 0/6 (0%) | 0/6 (0%) | n/a | n/a | n/a | not reported by `browse` |
| haiku plans, `browse` executes | 4/6 (67%) | 1/6 (17%) | 28.5 | 18.9 | 62.5 | $0.108 |
| sonnet-low plans, `browse` executes (default planner) | 6/6 (100%) | 5/6 (83%) | 33.5 | 25.8 | 35.6 | $0.113 |
| sonnet + Playwright MCP (reused) | 6/6 (100%) | | 47.1 | | 52.7 | $0.182 |
| sonnet planning, `browse` executing (reused) | 1/6 (17%) | | 65.5 | | 65.5 | $0.138 |

| task | jev | haiku plans | sonnet-low plans | sonnet + Playwright (reused) | sonnet plans (reused) |
|---|---|---|---|---|---|
| B1 open-ended link race to Ancient Rome | 0/3 (0/3) | 2/3 (0/3) | 3/3 (3/3) | 3/3 | 0/3 |
| B2 open-ended link race to Quantum mechanics | 0/3 (0/3) | 2/3 (1/3) | 3/3 (2/3) | 3/3 | 1/3 |

Jev's timing comes from `browse`'s own `timing` block. The sweep's first call
paid the one-time cold start at 7.0 s. The median Jev decision took 545 ms,
against 596 ms before the fixes: the two extra Noul judgments ride in the same
request, and Jev evaluates every question in a request in parallel.

**Where the two planner arms spent their wall time.** `planner s` is the time
inside the `claude -p` child, `browse s` the time the executing steps took.

| arm | group | runs | median turns | median planner s | median browse s |
|---|---|---|---|---|---|
| haiku plans | A | 18 | 4 | 3.5 | 13.6 |
| haiku plans | B | 6 | 6 | 5.7 | 22.4 |
| sonnet-low plans | A | 18 | 3 | 4.0 | 12.6 |
| sonnet-low plans | B | 6 | 6 | 7.2 | 23.7 |

Tokens come from the child's own usage block, not from a rate. Haiku's median
run sent 5,728 uncached input tokens and read 7,835 from cache, for 40 output.
Sonnet-low sent 7 uncached and read 17,691 from cache, for 47. The twenty-four
planner runs cost $2.55 on Haiku and $1.90 on Sonnet-low.

**Read the arms as the trade they are.**

Jev alone is still the fastest thing here and costs nothing in Claude tokens,
on tasks whose hops are named. It cannot do Group B at all.

Plan mode with Sonnet-low is the default planner. It now scores 16/18 on Group
A and 6/6 on Group B, within one run of Sonnet on Playwright, at about 60% of
its wall time and 50 to 60% of its cost.

Haiku stays available as the cheaper model per token. It does Group A as well
as Sonnet-low does and trails on Group B, 4/6 against 6/6. Because it reads
less from cache, its runs did not come out cheaper here.

**What the fixes changed.** A4 went from 0/3 to 3/3 on every fast arm. Its
target sits in a long table below the ranked candidates, and the planner can
now `FIND` it. The planner arms got slower per run, 12 to 17 s on Group A. A
low-confidence DONE or BLOCKED now looks at the page again before the run ends,
and `FIND` adds a call when it is used.

**What still fails.** A2 passes 1/3 at best on the fast arms. In five of its
eight failures the run left English Wikipedia through an interlanguage link and
ended on `fr.wikipedia.org`, at Boris Spassky or at Saint Petersburg.

## Known limits

- **The bench found no denies.** The guard's measured value so far is that it
  is cheap and does not get in the way, not that it has saved anything. It is
  worth running in shadow for a week on a new machine and reading the log
  before arming it.
- **Shadow mode is the default.** Nothing here blocks anything
  until someone writes `enforce` into the mode file.
- **`review/` is JavaScript and TypeScript only.** Against a Python repository
  it reports very little, which reads exactly like "nothing wrong".
- **`ask` exists but no rule uses it by default.** It was verified empirically
  against Claude Code 2.1.272: `permissionDecision: "ask"` is honoured, but in a
  headless session it is indistinguishable from a deny (the tool does not run
  and the call lands in `permission_denials`), so it degrades to a plain deny
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
