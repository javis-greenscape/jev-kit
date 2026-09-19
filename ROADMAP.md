# Roadmap

What is known to be missing or unfinished. Nothing here is a commitment to a
date. The [Native Windows gap](docs/native-windows.md) is the largest single item
and is itemised there rather than repeated.

## Known gaps

- **Native Windows support.** Not built, not run, itemised in
  [docs/native-windows.md](docs/native-windows.md).
  Everything (`es.exe`) file search on Windows is part of that work.
- **macOS is untested.** The guard is stdlib Python and should run with
  `--no-systemd`, but nobody has tried it, and the `launchd` equivalents of
  the five systemd user units are not written.
- **The installer and doctor are bash.** Porting both to Python would remove
  the Git Bash dependency on Windows and would suit a project whose guard is
  already stdlib Python.
- **The unit tests read the real `$HOME`.** A machine with its own
  `~/.config/airlock/rules.json` can fail the suite, which blocks a deploy
  that is otherwise fine. `install/deploy.sh` works around it by running the
  tests with `HOME` pointed at a temporary directory; the suite should isolate
  itself instead.

## Evaluation and tuning

- **The A/B bench has found no denies yet.** The measured result so far is
  that the guard is cheap and stays out of the way, not that it has prevented
  anything. More adversarial bench tasks are needed before the guard's value
  can be claimed rather than assumed.
- **The eval corpus skews toward the rules that already exist.** Cases are
  labelled per rule, so a rule nobody wrote has no cases arguing for it.
- **Threshold calibration is run by hand.** `tuning/calibrate.sh` locks
  thresholds into the two `eval/decisions-*.lock.json` files; deciding when to
  re-run it is currently a judgement call, not a trigger.

## Components

- **Rewrite mode stays off by default.** See the README section for why: a
  rewritten command is a command the user did not type.
- **`docclass/` ships no real taxonomy**, only a generic example. Writing one
  is the adopter's job, and the README says to key off document kind rather
  than a folder tree.
- **`logtriage/` local rules are a small set.** They cover the common shapes;
  anything else falls through to a model call.

## Possible, not planned

- A named-pipe transport for the daemon on Windows, as its own piece of work.
- Structured judgements for row-level data sources, which would need a
  client-side tool and an explicit decision about what may leave the machine.
