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
idle daemon. The one figure measured end to end here is the tier eval's:
**mean 572 ms per judgement** over 58 real Jev calls at four-way concurrency,
`python3 -m airlock.eval`, 2026-09-19, on the box described above -- which is
a throughput number under load, not a single-call latency, and is quoted only
so there is one figure with a method attached to it.

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
out`, which blew the then-1500 ms enforce budget and **fail-opened** -- a
3.1% fail-open rate in this sample. Windows has no warm daemon, so every
judgement pays a fresh DNS + TCP + TLS handshake against a budget chosen for
a Linux box that has one. The owner's decision from these numbers: the
default enforce budget on native Windows is now **2000 ms**
(`airlock/enforce.py:WINDOWS_DEFAULT_BUDGET_MS`), clearing the measured
1359 ms max with room to spare; POSIX, WSL and macOS keep 1500 ms. See
[native-windows.md](native-windows.md).

The health check's own direct HTTPS probe on the same machine and the same
session: `direct_ask {ok: true, latency_ms: 1125}`, `status=healthy`.

## Rule accuracy

`python3 -m airlock.eval`, 2026-09-19. Every deny-capable rule scored 100%
with zero false denies on its labelled cases (R1 16 cases, R2 8, R3 10, R4 11,
R5 9, R6 9, R7 10, R9 9), so every rule ships at its intended action. **A rule
with any false deny on its eval cases ships as `warn`, not `deny`.**

`R10-general-risk` scored **95.2% on 21 labelled cases** (20/21), with zero
false denies -- structurally impossible, since it can only warn. It was 88.9%
(16/18) before the quieting pass that narrowed its pre-filter away from
user-level `systemctl` and read-only `docker`; `eval/README.md` has both runs.
The one remaining miss is an over-warn on an `rsync` into a local backup
directory, which also flaps between runs. Its code pre-filter fired on **2 of
284 (0.7%)** of the Bash calls in the existing shadow log; it was actually
*consulted* on 0 of them, because every row in that log is a call some specific
rule had already claimed. That is the intended shape -- the fallback is the
residue, not a second opinion.

## Tier-guard accuracy

`python3 -m airlock.eval`, 2026-09-19. 58 labelled Agent dispatches, scored on
the three outcomes the guard actually has -- block, warn, silent -- against
labels derived from each case's own chosen tier and task by the ladder:
**98.2%** overall (56 of 57 scored; one case is labelled `ambiguous` and
excluded), with **zero false denies and zero missed denies**. Block 18/18,
silent 29/29, warn 9/10. The single miss is a `task_kind` boundary, not a
policy bug, and the direction is safe (a missed warn, never a false block).
`eval/README.md` names it and says why the previous 16-false-deny figure was a
label artefact rather than anything the model got wrong.

## A/B bench: the guard against no guard

`bench/results/20260919-120344.md`, 2026-09-19: 30 sessions, enforce mode
against no guard at all, five tasks. **Zero denies** -- agents already pick the
right tool almost every time.

Read that honestly: it is a backstop, not a tax. The measured value of the
guard so far is that it is cheap and does not get in the way, not that it has
saved anything. That result is also why `airlock/policy.py` now skips the Jev
call entirely when the code-computed facts make a deny unreachable, and samples
5% of the rest so the tuning loop still sees ordinary traffic.

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
sequential, arms interleaved, one shared headless Chromium. **n=3 per cell --
directional, not statistically significant.**

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
