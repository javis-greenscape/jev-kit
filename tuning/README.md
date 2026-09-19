# airlock tuning loop

An unattended loop that reads new shadow-mode log rows, has a headless Claude
judge them against the current guard rubrics, and -- only when a hard gate
passes -- commits a criteria fix and new eval cases to a dedicated `auto-tune`
branch. It never touches the main working tree and never merges into `main`
on its own.

## How one run works (`tuning/tune.py`, invoked by `tuning/tune.sh`)

1. Exit immediately, without touching any state, if `~/.config/airlock/disabled`
   or `~/.config/airlock/tuning-disabled` exists, or if fewer than
   `--min-rows` (default 10) new shadow rows exist since the last run.
2. Otherwise check the backoff: `~/.local/state/airlock/tune_state.json`
   holds `interval_min` (starts at 30, doubles after a no-op run, capped at
   1440, resets to 30 after a commit or when the judged error rate exceeds
   10%). `--force` bypasses this check only (used for manual/verification
   runs); it never bypasses the min-rows check.
3. Ensure `~/.local/state/airlock/tune-worktree` exists as a git worktree on
   branch `auto-tune`, created from `main` if the branch doesn't exist yet,
   otherwise rebased onto `main` (aborting and logging on a conflict). The
   main repo path is never hard-coded -- it's read from `git worktree list`,
   which always lists the original checkout first.
4. Take up to the newest 20 new shadow rows and send them to a headless judge:
   `CLAUDE_CONFIG_DIR=$AIRLOCK_TUNE_CLAUDE_CONFIG_DIR claude -p <prompt> --model
   $AIRLOCK_TUNE_JUDGE_MODEL --effort $AIRLOCK_TUNE_JUDGE_EFFORT --safe-mode --tools
   ""` with stdin closed (default model `opus`, effort `high`). Each
   tool_choice_guard row already carries its own code-computed `scope` field;
   the prompt tells the judge to treat that as ground truth and judge
   `search_intent` only, never re-deriving scope. The prompt carries the
   rubric and the rows and demands a strict JSON array back. On anything that
   doesn't parse as expected, the run stops and logs why -- nothing
   downstream happens. The row cap was halved from 40 when the judge moved to
   Opus at high effort, a materially more expensive call per row, to keep a
   single run's cost in check.
5. Rows the judge marks wrong become new `source: "shadow"` cases, deduped by
   a hash of the command (or subagent_type+prompt) and appended to
   `eval/cases.jsonl`, capped at 400 total (oldest shadow cases drop first;
   seed cases are never dropped).
6. A **baseline eval runs before any code changes**, on the full case set
   (existing + new), using the *old* `questions.py`. This is the number
   everything else is measured against.
7. A second headless Claude call (same flags, same `$AIRLOCK_TUNE_JUDGE_MODEL` /
   `$AIRLOCK_TUNE_JUDGE_EFFORT` defaults) is given the current `questions.py`
   criteria text, the wrong cases, and a short summary of TypeSafe's own
   guidance, and must return ONLY a JSON object mapping
   `{question_id: {option_name: "new criteria text"}}`. This is applied with
   an AST-based rewrite (`apply_criteria_replacement`) that only ever swaps
   the criteria **value** for a named option inside `tier_questions()` /
   `bash_questions()` -- it cannot touch option names, question ids, or
   anything in `policy.py`.
8. **Gate**, enforced in code:
   - `git diff --name-only` in the worktree may show only
     `airlock/questions.py` and `eval/cases.jsonl`. Anything else and the
     whole change is discarded.
   - The unit test suite must pass.
   - A post-change eval must show: accuracy on the previously-existing cases
     is no worse than before, overall accuracy (existing + new cases) is
     strictly better, and the false-deny count is no higher.
   - Any failure discards the change (`git checkout --`) and logs why.
9. On success: commit on `auto-tune` with the before/after numbers in the
   message. `auto-tune` is **never merged into `main`** by this script.
10. Every run appends one line to `~/.local/state/airlock/tune_log.jsonl`
    regardless of outcome. `python3 -m airlock.report --tuning` summarizes
    the log.

## Design choices worth knowing about

- The "strictly better overall" gate compares the SAME case set (existing +
  newly-added cases) evaluated with the old questions.py vs. the new one, both
  via real Jev calls, so the comparison is apples-to-apples. This costs two
  full eval runs (plus a third, cheaper one restricted to the pre-existing
  case ids) every time there's something to gate on -- that's the loop's real
  ongoing cost, not the judge calls.
- Portability: nothing here hard-codes a user's home directory. Paths derive from
  `$HOME`, the script's own location, and `git worktree list`. The systemd
  unit uses the `%h` specifier (expands to the invoking user's home
  directory in a `--user` unit) for the one absolute path it needs.
- `AIRLOCK_TUNE_CLAUDE_CONFIG_DIR` (default: `$CLAUDE_CONFIG_DIR` if set,
  else `~/.claude`) and
  `AIRLOCK_TUNE_CLAUDE_BIN` (default `claude`) control which account and binary
  the judge calls use. `AIRLOCK_TUNE_STATE_DIR` and `AIRLOCK_TUNE_WORKTREE_DIR`
  override the state/worktree locations, mainly for testing.
- `AIRLOCK_TUNE_JUDGE_MODEL` (default `opus`) and `AIRLOCK_TUNE_JUDGE_EFFORT` (default
  `high`) control both the judge call and the criteria-rewrite call -- they
  share a model/effort because a cheaper judge is exactly the thing a more
  expensive judge exists to catch, so the rewrite it drives should reason at
  the same level.

## Install (NOT done by this change -- run these yourself)

```bash
mkdir -p ~/.config/systemd/user
cp tuning/airlock-tune.service tuning/airlock-tune.timer ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now airlock-tune.timer
systemctl --user list-timers airlock-tune.timer
```

To verify the unit files are well-formed without installing them:

```bash
export XDG_RUNTIME_DIR=/run/user/$(id -u)
systemd-analyze --user verify tuning/*.service tuning/*.timer
```

To pause the loop without uninstalling anything:

```bash
touch ~/.config/airlock/tuning-disabled   # tuning only
touch ~/.config/airlock/disabled          # tuning AND the shadow hook itself
```

## Promotion is separate and manual

`tuning/tune.sh` (and the timer that runs it) **never merges `auto-tune` into
`main`, and never checks out or edits anything in the main working tree** --
that tree is imported directly by live Claude Code sessions on this box, and
switching branches or editing it out from under them would be actively
dangerous.

When `auto-tune` has commits worth promoting (check with
`git -C ~/.local/state/airlock/tune-worktree log main..auto-tune`), a human
(or the director, deliberately) runs:

```bash
tuning/promote.sh
```

This re-runs the unit tests in the tune worktree, refuses to run if the main
repo isn't on `main` or has uncommitted changes, and only then fast-forwards
`main` to `auto-tune`. It is the only script here that touches the main
working tree, and it only ever fast-forwards -- if `main` has diverged (e.g. a
human commit landed there), it fails rather than merging or rebasing anything
automatically.


## Calibrating the thresholds (jevcal)

`airlock/policy.py` ships `CONFIDENCE_THRESHOLD = 0.8` and
`MARGIN_THRESHOLD = 0.4`. Those were chosen by judgement. `tuning/calibrate.sh`
replaces that with a measurement, using
[jevcal](https://github.com/abhixhek/jevcal) as a developer tool in this repo.

```bash
tuning/calibrate.sh --lint-only        # no key, no API calls
# load the key into this shell first, then:
tuning/calibrate.sh                    # measure + compile, writes the locks
tuning/calibrate.sh --no-measure       # recompile from existing predictions
```

`tuning/jevcal_export.py` writes jevcal's three inputs **from what we already
have** -- the live question definitions in `airlock/questions.py` and the
labelled cases in `eval/cases.jsonl` -- so there is no second copy of either to
drift. The exported files land in `eval/jevcal/` and are gitignored; the lock
files (`eval/decisions-tier.lock.json`, `eval/decisions-search.lock.json`) are
not, because they are the artefact worth keeping.

Each guard gets its own questions/data pair. jevcal sends every question in a
file for every row of its data, so combining them would ask `task_kind` about a
Bash command's state -- a question the guard never asks, calibrating a threshold
against noise.

**`jevcal label` and `jevcal optimize` are never run.** Both call a third-party
LLM (OpenAI, OpenRouter or Anthropic) with our dataset. `calibrate.sh` uses only
`lint`, `measure` and `compile`.

**Tune against observed correctness, never against reported confidence.** jevcal
scores against the gold labels in `eval/cases.jsonl`, which is the right way
round. The behaviour study measured a case where the mean reported confidence of
an entirely wrong answer was 0.9744 (RINNECODER/jev-behavior-study, see
[`docs/CREDITS.md`](../docs/CREDITS.md)).

### The drift gate

`jevcal check` re-measures against a lock file and exits 1 on drift. It is
deliberately not wired into the timer yet: the locks currently record "escalate
everything" (see below), so there is nothing meaningful to drift away from.

### What jevcal lint flagged, 2026-09-19

Run over `eval/jevcal/questions.yaml`, which is generated from
`airlock/questions.py`, so it is our real wording:

| id | severity | question | finding |
|---|---|---|---|
| J002 | error | `task_kind` | multiple negations (`not`, `without`) |
| J002 | error | `search_intent` | multiple negations (`doesn't`, `isn't`, `not`) |
| J010 | warn | `states_prior_failed_attempts` | compound yes/no question (and / or) |
| J010 | warn | `brief_is_self_contained` | compound yes/no question (and / or) |
| J006 | info | `task_kind` | looks like a multi-hop question |
| J006 | info | `search_intent` | looks like a multi-hop question |

Read honestly, in two parts.

**One caveat about the export.** jevcal lints the instructions *and* the
criteria text together, and our criteria are `{what, not_for, examples}` objects
that the exporter flattens into one string per option. That flattening folds
every `not_for` into the linted text, so it inflates the negation count.
Checking the instructions alone: `task_kind` still has two negations and would
still be a J002 error; `search_intent` has one, so on its own it would be a J001
warning rather than an error. The J002 on `search_intent` is partly an artefact
of the export.

**What is worth acting on.** J010 on the two nouls is a fair hit and cheap to
fix: `states_prior_failed_attempts` asks about a prior attempt *and* what went
wrong, and `brief_is_self_contained` asks about paths *and* acceptance criteria
*and* verification. Splitting each into separate nouls costs almost nothing per
jevcal's own advice, and the behaviour study's finding (2) says the same thing
from the other direction. J006 is inherent to what the tier guard asks and is
not a defect to chase. Not changed here: the wording is what the thresholds were
just measured against, and moving both at once would leave neither measured.

### What compile found, 2026-09-19

| set | rows | accuracy | ECE | threshold |
|---|---|---|---|---|
| `task_kind` | 50 | 98.0% | 1.9% | none: escalate everything |
| `search_intent` | 32 | 84.4% | 12.6% | none: escalate everything |

Both came back `no_threshold`, for a reason about the dataset rather than the
model: **no threshold reaches the 99% target with at least 30 accepted rows,
because there are only 50 and 32 labelled rows in total.** jevcal says so itself
("thresholds from small samples do not hold up"). The honest reading is that
`eval/cases.jsonl` is too small to calibrate on, not that the guard should stop
deciding. The existing hand-picked 0.8/0.4 bar stays in place.

Two things it surfaced that were worth having:

- **One gold label in `eval/cases.jsonl` was wrong.** `tier-hard-2` was labelled
  `hard_problem`, but its prompt only asserts that a bug is "really nasty and
  hard to find" and names no prior attempt, which our own criteria put under
  `judgement` explicitly. Correcting it took `task_kind` from 96.0% to 98.0% and
  cleared jevcal's "confident and wrong" warning. Its `would_deny` is unchanged:
  that comes from the separate fable-without-a-stated-prior-failure rule.

- **`search_intent` is the weaker of the two**, at 84.4% with an ECE of 12.6%.
  Its five misses are mostly genuinely arguable labels rather than model errors:

  | command | gold | answered | confidence |
  |---|---|---|---|
  | `ls -R /home/user/code` | `filename_search` | `not_a_search` | 0.99 |
  | `grep -c ERROR app.log` | `not_a_search` | `literal_text_search` | 0.91 |
  | `find /tmp -name '*.tmp' -delete` | `not_a_search` | `filename_search` | 0.57 |
  | `fdfind pattern <dir>` | `filename_search` | `unclear` | 0.91 |
  | `grep -rn 'default_timeout' .` | `literal_text_search` | `code_structure_search` | 0.41 |

  Those labels are worth arguing about before any of them is treated as a fault.

The next step is more labelled rows, not a different threshold.
