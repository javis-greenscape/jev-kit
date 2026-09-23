# Credits

Every community project whose code, patterns or ideas are used in jev-kit, what
was taken from each, and its licence. Licence identifiers were verified against
the GitHub API on 2026-09-19 (`gh api repos/<owner>/<repo>/license`), not
written from memory.

`NOTICE` carries the copyright notices themselves, in full, where a licence
requires it. This file is the readable version: who to thank, and for what.

## Ported into this repository

### [valentynkit/jev-commit](https://github.com/valentynkit/jev-commit), MIT

The local credential belt. Roughly 50 lines of secret-shaped patterns, and the
placeholder-suppression logic that stops `API_KEY=xxx` in an example from being
treated as a real finding, adapted from `jev_commit/belt.py` into
`airlock/belt.py`.

Also the idea `airlock/belt.py` is built on: check locally first, and only spend
a model call on what local rules could not settle.

### [leepokai/jev-guard](https://github.com/leepokai/jev-guard), MIT

Question wording. The `user_requested` question in `airlock/questions.py` and
the transcript reading behind it in `airlock/context.py` come from that
project's `src/context.js`. R10's `risk` score in `airlock/rules.py` is ported
in spirit from its `ACTION_QUESTIONS`: a four-level score from read-only and
reversible up to destructive and irreversible, rather than a yes/no block
question.

That project also had the name `jev-guard` first, which is why the guard
component here is called `airlock`.

### [kyotofin/tax-doc-classifier](https://github.com/kyotofin/tax-doc-classifier), Apache-2.0

The shape of `docclass/`: a first `choice` over broad families with an explicit
`not_in_this_list` escape hatch, a second narrowing pass inside the family the
first pass chose, and a confidence gate on the result. No source file was
copied; `docclass/` is an independent Python reimplementation targeting a
different document domain, with its own generic example taxonomy. Upstream's
data is under a separate DATA-LICENSE and none of it is here.

### [reachjalil/jevlogs](https://github.com/reachjalil/jevlogs), MIT

The privacy architecture of `logtriage/`: redact first, then let local rules
answer everything they can, and send a model only the lines that are left. No
code copied. Its treatment of a log line as untrusted data rather than as
instructions is also carried across into the question wording here.

### [RINNECODER/jev-behavior-study](https://github.com/RINNECODER/jev-behavior-study), MIT

Published measurements, not code. Four of its findings shape the design here,
and they are cited by name where they do:

* **Narrow checks transfer; direct action-selection does not.** On its
  twelve-scenario panel, asking directly which action to take scored 65/120
  (54.2%), while two narrow prerequisite checks scored 110/120 (91.7%). This is
  the reason the guard asks Jev only what KIND of task or search a call is, and
  decides the action in code (`airlock/policy.py`), instead of asking "should
  this be blocked?".
* **A surface cue in the state can dominate the judgement.** Holding a scenario
  fixed and calling a journey "a five-minute walk" moved the same question from
  20/20 correct to 0/20. The tier guard puts `subagent_type` in the state
  alongside the prompt, which is the same shape of exposure, so
  `eval/ablation.py` measures it rather than assuming it is fine.
* **Confidence is not accuracy.** In one of its conditions the mean confidence
  in an answer that was wrong every single time was 0.9744. Everything
  `tuning/` calibrates is calibrated against observed correctness, never
  against a reported confidence.
* **Describe what each option does; a generic instruction to be careful does
  nothing.** Defining options concretely scored 25/25 where prefixing a "choose
  carefully" preamble scored 0/25. The questions here use structured
  `{what, not_for, examples}` criteria per option for that reason, and the
  answer to a miss is another example in the criteria rather than a sterner
  preamble.

## Cloned or installed at a pin, never vendored

None of these are copied into this repository. Each installer fetches upstream
at install time, at the pin shown, and upstream's licence governs upstream's
code.

| Project | Licence | Pin | Used by |
|---|---|---|---|
| [valentynkit/jev-belay](https://github.com/valentynkit/jev-belay) | MIT | `98f39e0` | `belay/install.sh` clones it; `belay/run.sh` is this project's own fail-open wrapper around it. |
| [tamaratran/fast-jev-compaction](https://github.com/tamaratran/fast-jev-compaction) | MIT | marketplace | `compaction/install.sh` installs it as a Claude Code plugin. Opt-in, never installed for you: read `compaction/README.md` for what it sends. |
| [browser-use/jev-ultrafast](https://github.com/browser-use/jev-ultrafast) | MIT | `1231850` | Vendored in full at `vendor/jev-ultrafast` with `git subtree`, its LICENSE alongside it. Seven of this project's commits sit on top of the pin. |
| [devagrawal09/jev-review](https://github.com/devagrawal09/jev-review) | MIT | `31f8960` | `review/install.sh` clones it; `review/jev-prefilter.sh` is this project's own wrapper. No code copied. |
| [abhixhek/jevcal](https://github.com/abhixhek/jevcal) | MIT | binary | `tuning/calibrate.sh` invokes it; `tuning/jevcal_export.py` writes this project's predictions into the format it reads. No code copied. |

## And TypeSafe

Jev itself, and the System One API every component here talks to, are
TypeSafe's: <https://typesafe.ai>. Nothing in this repository is affiliated with
or endorsed by them.
