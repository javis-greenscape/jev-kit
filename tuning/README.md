# airlock tuning loop

An unattended loop that reads new shadow-mode log rows and has a headless
Claude judge them against the current guard rubrics. Only when a hard gate
passes does it commit a criteria fix and new eval cases to a dedicated
`auto-tune` branch. It never touches the main working tree and never merges into `main`
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
3. Resolve which repository this run operates on (see "Which repository does
   tuning use?" below), then ensure `~/.local/state/airlock/tune-worktree`
   exists as a git worktree of it on branch `auto-tune`, created from `main`
   if the branch doesn't exist yet, otherwise rebased onto `main` (aborting
   and logging on a conflict). If no repository resolves at all, the run
   exits 0 with one log line and touches no state. That is the common case
   for a fresh install that has not run `install/install.sh --tuning` yet, or
   a deployed release with no pointer recorded. Tuning is optional; it must never fail the
   timer noisily.
4. **Sample up to 20 rows to judge** (`tuning/sampling.py`), drop any that
   still carry secret-shaped text (see "The redaction sanity check" below; a
   dropped row is counted, never a reason to abandon the run), and send the
   rest to a headless judge whose binary was resolved before any of the above
   (see "Finding the judge binary"):
   `CLAUDE_CONFIG_DIR=$AIRLOCK_TUNE_CLAUDE_CONFIG_DIR claude -p <prompt> --model
   $AIRLOCK_TUNE_JUDGE_MODEL --effort $AIRLOCK_TUNE_JUDGE_EFFORT --safe-mode --tools
   ""` with stdin closed (default model `opus`, effort `high`). The row cap was
   halved from 40 when the judge moved to Opus at high effort. See
   "How rows are sampled" and "What the judge is told" below. Both sections
   exist because the first real unattended run got both wrong.
5. Rows whose **label** the judge marks wrong become new `source: "shadow"`
   cases, deduped by a hash of the command (or subagent_type+prompt) and
   appended to `eval/cases.jsonl`, capped at 400 total (oldest shadow cases
   drop first; seed cases are never dropped). A row whose label was right and
   whose *action* was wrong produces no case: there is no label expectation to
   record, and inventing one teaches the eval the opposite of the finding. The
   case's deny expectation is re-derived from the live policy given the
   corrected label, and omitted (`deny_expectation: "unverified"`) when the row
   does not carry what the policy needs. It is never copied off the row: a
   row the judge has just called wrong is the last place to read the right
   answer from.
6. A **baseline eval runs before any code changes**, on the full case set
   (existing + new), using the *old* `questions.py`. This is the number
   everything else is measured against.
7. A second headless Claude call (same flags, same `$AIRLOCK_TUNE_JUDGE_MODEL` /
   `$AIRLOCK_TUNE_JUDGE_EFFORT` defaults) is given the current `questions.py`
   criteria text, the wrong cases, and a short summary of TypeSafe's own
   guidance, and must return ONLY a JSON object mapping
   `{question_id: {option_name: "new criteria text"}}`. This is applied with
   an AST-based rewrite (`apply_criteria_replacement`) that only ever swaps
   the criteria **value** for a named option inside `tier_questions()` or
   `bash_questions()`. It cannot touch option names, question ids, or
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
    regardless of outcome, carrying a `category` that says which of the five
    possible outcomes it was (see "Every run records a category" below).
    `python3 -m airlock.report --tuning` summarizes the log.

## How rows are sampled

`tuning/sampling.py`. Two rules, both named in every log line that quotes a
rate, because a rate without its sampling rule is not a measurement.

**Only judgeable rows are sampled.** A row is judgeable when it carries a Jev
answer for a question the judge's rubric still has (`task_kind`,
`search_intent`) and an input summary to judge it against. Everything else is
skipped and counted by reason:

| reason | what it is |
|---|---|
| `no_jev_answer` | Jev was never called. Usually `skipped: "no_deny_possible"` -- the code already knew from scope/program/rung that no answer could reach a deny, so the call was skipped to save ~300ms. There is no answer to be right or wrong about. |
| `legacy_question` | written by an older release under a question id since renamed (`search_kind` predates `search_intent`). Not scorable against today's rubric. |
| `no_rubric_for_question` | a guard whose question the judge has no rubric for (`risk`, `prints_a_secret`, ...). |
| `no_input_summary` | an answer with nothing to judge it against, e.g. an override row. |

**The budget is split in half.** Half goes to the newest judgeable rows (the
recency slice: what the guard is doing right now, which is what a tuning loop
should react to). Half is a uniformly random draw over every judgeable row the
run can see. Each half reports its own rate, and **only the random half is an
estimate of overall accuracy.** The run log carries `sample`,
`rates_by_sample_rule` and a `rate_sentence` that never states a bare number:

```
40% wrong of the 5 rows sampled by the newest judgeable rows since the last run
(recency-biased: NOT an estimate of overall accuracy); 0% wrong of the 6 rows
sampled by a uniformly random draw ... ; skipped as not judgeable:
no_jev_answer=773, legacy_question=34, no_rubric_for_question=68
```

`AIRLOCK_TUNE_SAMPLE_SEED` pins the random half for a reproducible run; the
seed used is recorded either way.

### Why this section exists

The first real unattended run (2026-09-19T17:13:03Z, judge = Opus at high
effort) took "the newest 20 new rows" and reported `error_rate 0.85`. That is
17 of 20 wrong, against a guard that scores 95-98% on its labelled eval. Replaying
that exact window through `sampling.select` gives: **20 considered, 1
judgeable, 17 `no_jev_answer`, 2 `no_rubric_for_question`.** The 17 is not a
measurement of the guard. It is the count of rows in which Jev never spoke.

## What the judge is told

`tuning/policy_text.py` generates the policy block by **running the real
policy functions**. It runs `airlock/policy.py` over the real ladder in
`airlock/tiers.py` and the real option lists in `airlock/questions.py`, then
prints what they return.

It prints four things:

- the three live tier outcomes: block at two rungs or more, or fable without a
  stated prior failure; warn at exactly one rung; silent otherwise;
- the shared confidence/margin bar;
- the full task_kind x rung grid;
- the exact scope/intent combinations the search guard denies on. Nothing in it is
hand-kept, so it cannot drift from the code.

`tests/test_tune_judge_policy.py` pins a fingerprint over those constants.
Change a threshold, a rung, an option or an outcome and that test fails,
which forces whoever changed it to re-read the prompt before re-pinning:

```bash
python3 -c 'from tuning import policy_text as p; print(p.policy_fingerprint())'
```

The prompt asks for **two verdicts per row, never one**:

- `label_correct`: was the option Jev chose the right one?
- `action_correct`: given the policy, did the guard do the right thing?

They are independent. A below-bar answer that the guard stayed silent about is
a *wrong label with a correct action*, and the old single `jev_correct` flag
had no way to say so.

The prompt also states four things: fail-open is the design, below-bar silence
is correct, under-tiering is never a deny, and the row's `action` field is the
*rule's configured action* rather than what happened to this call. On that last
point, 18 of the 20 rows in the bad run read `action: "deny"` with
`would_deny: false`, meaning they were allowed.

The judge may answer `cannot_tell`. A `cannot_tell` is counted, reported, and
put in **neither** half of the ratio.

## Per-run verdicts: `tune_verdicts/`

Every run writes one JSONL file per run to
`~/.local/state/airlock/tune_verdicts/<ts>.jsonl`, mode 600, newest 20 runs
kept. The first line is a `_meta` record: judge model and effort, the sampling
report, the rate table, the policy fingerprint.

One line per row follows, with the row id, guard, sample rule, the label Jev
chose, the label the judge chose, both verdicts, `cannot_tell`, and the judge's
one-line reason.

No command text, no cwd and no prompt is copied in: the shadow log already
holds those under the same protection, and a second copy is a second thing to
leak.

The run that produced `error_rate 0.85` kept none of this, so answering "why
17?" afterwards meant reconstructing the batch from timestamps. A run that
cannot be questioned after the fact cannot be trusted before it.

## Which repository does tuning use?

`tune.sh`, `tune.py` and `promote.sh` all resolve "the repository" the same
way, with one implementation per language: `airlock/repo_path.py` for Python,
`install/repo-path.sh` for the shell. The module docstring in the former is
where the order is written down.

1. `AIRLOCK_TUNE_REPO` in the environment, honoured as given, no checks.
2. the path recorded in `$AIRLOCK_CONFIG_DIR/repo.path`, written by
   `install/install.sh --tuning`, which records the checkout it was run from.
   It counts only if the pointer passes the same trust checks as `keyfile.path`: owned by
   this user, not group- or world-writable (pointer file and its directory
   both), the recorded path absolute, and naming an existing directory that
   contains a `.git`.
3. the resolving script's own parent directory, if THAT is a git checkout.
4. otherwise nothing: the run exits 0 and logs one line.

Step 3 is why a plain checkout needs no pointer at all: this repo, cloned and
run in place for manual testing.

Step 2 is why the systemd timer works. It runs the DEPLOYED release under
`$AIRLOCK_HOME/current/tuning/`, which `install/deploy.sh` exports as a plain
directory tree with no `.git` of its own. Step 3 can never fire there, so the
pointer written at install time is the only way it resolves to anything.

`install/doctor.sh` reports which repository tuning currently resolves to and
whether `~/.local/state/airlock/tune-worktree` actually matches it.

### A worktree left over from a different repository

A `tune-worktree` that already exists but belongs to another repository is
never reused and never deleted. Its `.git` file points at a different main
checkout, which happens after `install/install.sh --tuning` is re-run at a new
or moved checkout, or after a clone is retired or replaced.

It is renamed to `tune-worktree.retired-<unix-timestamp>` next to itself, so
any auto-tune commits in it stay inspectable. A line is logged saying so, and a
fresh worktree is created from the newly-resolved repository.

The interval backoff in `tune_state.json` lives in the state directory rather
than inside the worktree, so a retirement never resets it.

### Moving tuning to a new checkout (e.g. after this fix is deployed)

```bash
install/install.sh --tuning --wire   # or just --tuning, if guard is already wired
cat ~/.config/airlock/repo.path      # confirm it names the checkout you expect
install/doctor.sh                    # "Tuning" section: resolved repo + worktree match
systemctl --user restart airlock-tune.timer   # picks up the new pointer on its next run
```

If a stale `tune-worktree` from the old checkout is present, the next tuning
run retires it automatically, as above. No manual cleanup needed.

## Every run records a category

Twelve unattended runs produced zero commits and every one of them exited 0,
because tuning is optional by design and a run that cannot proceed is not
an error. That is the right behaviour and the wrong report: "healthy, nothing
to do" and "has never once worked" looked identical in the log.

Each run now writes exactly one `category` to
`~/.local/state/airlock/tune_log.jsonl`, and `install/doctor.sh` prints the
newest one with its age:

| category | means | examples |
|---|---|---|
| `did_not_run` | the run was not attempted | interval not elapsed, kill switch present |
| `could_not_run` | attempted, blocked before any judging | no judge binary, no repository, worktree setup failed |
| `ran_nothing` | ran, found nothing worth changing | too few new rows, judge found no wrong rows, all wrong rows already have cases |
| `ran_rejected` | ran, produced a candidate, the gate discarded it | accuracy did not improve, unit tests failed, judge returned junk |
| `ran_committed` | ran, committed to `auto-tune` | -- |

`did_not_run`, `could_not_run` and the too-few-rows case append a log row and
**do not** write `tune_state.json`. Writing `last_run_epoch` on
every timer firing would keep pushing the backoff clock forward and the
interval would never elapse at all.

## Finding the judge binary

Seven of those twelve runs died on
`judge call invalid: [Errno 2] No such file or directory: 'claude'`. A
systemd user unit runs with a minimal `PATH` (typically `/usr/bin:/bin`) that
does not include the npm global prefix under `$HOME` where the `claude` CLI
is installed. Run by hand from a login shell it worked every time, which is
exactly why it looked fine.

Resolution now lives in one function, `tune.resolve_judge_bin()`, in this
order, first executable wins:

1. `$AIRLOCK_TUNE_CLAUDE_BIN` or `$AIRLOCK_CLAUDE_BIN` (this is how
   `$AIRLOCK_CONFIG_DIR/tune.env` reaches the run, since `tune.sh` sources
   it);
2. `shutil.which("claude")`, i.e. whatever `PATH` the run actually has;
3. the usual per-user install locations: `~/.npm-global/bin`, `~/.local/bin`,
   `~/.claude/local`, `~/bin`, `~/.bun/bin`, `~/.yarn/bin`, and nvm's
   `current/bin` plus each installed version's `bin`, read from nvm's
   directory layout without sourcing a shell.

If none resolves, the run logs one line naming every place it looked, records
`reason: "judge binary not found"` with category `could_not_run`, and exits 0.

`install/install.sh --tuning` resolves the binary at install time from the
installing shell's `PATH` and records the **absolute path** in
`$AIRLOCK_CONFIG_DIR/tune.env` (mode 600). That file may also carry
`AIRLOCK_TUNE_CLAUDE_CONFIG_DIR` (which account the judge bills),
`AIRLOCK_TUNE_JUDGE_MODEL` and `AIRLOCK_TUNE_JUDGE_EFFORT`. It holds paths and
names only, never a key, and no account directory is defaulted to any
particular person's. `install/doctor.sh` prints what resolved and **fails**
when tuning is installed and nothing does.

`python3 tuning/tune.py --print-judge-bin` prints the resolved path and exits
0, or exits 1 listing everywhere it looked. `doctor.sh` calls exactly that, so
doctor cannot disagree with the run.

## The redaction sanity check

Three runs died on `redaction sanity check failed`. The check was a substring
search for the literals `apikey_` and `sk-` over the row's JSON, and it was
wrong in both directions:

- `sk-` is a substring of ordinary text. The live shadow log contains
  `disk-wide` and `mask a disk-wide find`, which is this project's own scope
  vocabulary rather than any secret.
- Worse, it fired on *correctly redacted* material. Every `apikey_` hit in the
  live log was the literal source of a redaction command, e.g.
  `sed 's/apikey_[A-Za-z0-9_]*/[REDACTED]/g'`. `redact()`'s own
  `apikey_[A-Za-z0-9_]+` needs at least one following word character and `[`
  is not one, so the pattern text survives redaction. The check then tripped
  on the pattern text of the thing that does the redacting.

Both errors came from keeping a second, hand-maintained idea of what a secret
looks like. There is exactly one such list, `airlock/redact.py`, so the check
now asks *it*: a row is clean when running the real redactor over the row
changes nothing. Over the live log of 1140 rows that takes the trip rate from
23 rows (every one benign) to 2 (a long base64-shaped run inside a file path).

A row that does trip is now **skipped and counted** (`redaction_skipped` in
the log row), and the run carries on with the rest; only a batch where every
row trips ends the run, as `ran_nothing`.

## The criteria rewrite is validated against the real schema

One run died on
`criteria rewrite failed: unknown option 'not_for' for question 'search_intent'`.
That was a criteria key the judge invented, because nothing had told it which
keys exist. Two changes:

- `tune.allowed_criteria_options()` reads the option names straight out of
  `airlock/questions.py` by AST, and the criteria prompt lists them to the
  judge: *"The ONLY keys you may use are these, exactly as written."*
- `apply_criteria_replacement()` drops an unknown question id, an unknown
  option name or a non-string value with a log line and keeps the rest,
  instead of failing the run over one bad key.

## Design choices worth knowing about

- The "strictly better overall" gate compares the SAME case set (existing +
  newly-added cases) evaluated with the old questions.py vs. the new one, both
  via real Jev calls, so the comparison is apples-to-apples. This costs two
  full eval runs (plus a third, cheaper one restricted to the pre-existing
  case ids) every time there is something to gate on. That is the loop's real
  ongoing cost, not the judge calls.
- Portability: nothing here hard-codes a user's home directory. Paths derive
  from `$HOME`, the script's own location, and the repository resolution
  order above. The systemd unit uses the `%h` specifier (expands to the
  invoking user's home directory in a `--user` unit) for the one absolute
  path it needs.
- `AIRLOCK_TUNE_CLAUDE_CONFIG_DIR` (default: `$CLAUDE_CONFIG_DIR` if set,
  else `~/.claude`) and `AIRLOCK_TUNE_CLAUDE_BIN` (no default; see "Finding
  the judge binary" above) control which account and binary the judge calls
  use. `AIRLOCK_TUNE_STATE_DIR`, `AIRLOCK_TUNE_WORKTREE_DIR` and
  `AIRLOCK_TUNE_SHADOW_LOG` override the state, worktree and shadow-log
  locations, mainly for testing and for a hand run against throwaway
  directories.
- `AIRLOCK_TUNE_JUDGE_MODEL` (default `opus`) and `AIRLOCK_TUNE_JUDGE_EFFORT` (default
  `high`) control both the judge call and the criteria-rewrite call. They
  share a model and effort because a cheaper judge is exactly the thing a more
  expensive judge exists to catch, so the rewrite it drives should reason at
  the same level.

## Install: run these yourself, nothing here does it for you

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

`tuning/tune.sh`, and the timer that runs it, **never merges `auto-tune` into
`main`, and never checks out or edits anything in the main working tree**. That
tree is imported directly by live Claude Code sessions on this box, and
switching branches or editing it out from under them would be dangerous.

When `auto-tune` has commits worth promoting (check with
`git -C ~/.local/state/airlock/tune-worktree log main..auto-tune`), a human
(or the owner, as an explicit act) runs:

```bash
tuning/promote.sh
```

This re-runs the unit tests in the tune worktree, refuses to run if the main
repo isn't on `main` or has uncommitted changes, and only then fast-forwards
`main` to `auto-tune`. It is the only script here that touches the main
working tree, and it only ever fast-forwards. If `main` has diverged, say
because a human commit landed there, it fails rather than merging or rebasing
anything automatically.


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
have**: the live question definitions in `airlock/questions.py` and the
labelled cases in `eval/cases.jsonl`. So there is no second copy of either to
drift. The exported files land in `eval/jevcal/` and are gitignored; the lock
files (`eval/decisions-tier.lock.json`, `eval/decisions-search.lock.json`) are
not, because they are the artefact worth keeping.

Each guard gets its own questions/data pair. jevcal sends every question in a
file for every row of its data, so combining them would ask `task_kind` about a
Bash command's state. That is a question the guard never asks, calibrating a
threshold against noise.

**`jevcal label` and `jevcal optimize` are never run.** Both call a third-party
LLM (OpenAI, OpenRouter or Anthropic) with our dataset. `calibrate.sh` uses only
`lint`, `measure` and `compile`.

**Tune against observed correctness, never against reported confidence.** jevcal
scores against the gold labels in `eval/cases.jsonl`, which is the right way
round. The behaviour study measured a case where the mean reported confidence of
an entirely wrong answer was 0.9744 (RINNECODER/jev-behavior-study, see
[`docs/CREDITS.md`](../docs/CREDITS.md)).

### The drift gate

`jevcal check` re-measures against a lock file and exits 1 on drift. It is not
wired into the timer yet, because the locks currently record "escalate
everything" (see below) and there is nothing meaningful to drift away from.

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

Read it in two parts.

**One caveat about the export.** jevcal lints the instructions *and* the
criteria text together, and our criteria are `{what, not_for, examples}` objects
that the exporter flattens into one string per option. That flattening folds
every `not_for` into the linted text, so it inflates the negation count.
Checking the instructions alone: `task_kind` still has two negations and would
still be a J002 error; `search_intent` has one, so on its own it would be a J001
warning rather than an error. The J002 on `search_intent` is partly an artefact
of the export.

**What is worth acting on.** J010 on the two nouls is a fair hit and cheap to
fix. `states_prior_failed_attempts` asks about a prior attempt *and* what went
wrong, and `brief_is_self_contained` asks about paths *and* acceptance criteria
*and* verification.

Splitting each into separate nouls costs almost nothing per jevcal's own
advice, and the behaviour study's finding (2) says the same thing from the
other direction. J006 is inherent to what the tier guard asks and is not a
defect to chase.

It is not changed here. The wording is what the thresholds were just measured
against, and moving both at once would leave neither measured.

### What compile found, 2026-09-19

| set | rows | accuracy | ECE | threshold |
|---|---|---|---|---|
| `task_kind` | 50 | 98.0% | 1.9% | none: escalate everything |
| `search_intent` | 32 | 84.4% | 12.6% | none: escalate everything |

Both came back `no_threshold`, for a reason about the dataset rather than the
model. **No threshold reaches the 99% target with at least 30 accepted rows,
because there are only 50 and 32 labelled rows in total.** jevcal says so itself
("thresholds from small samples do not hold up"). The reading is that
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
