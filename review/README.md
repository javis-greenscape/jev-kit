# review: a cheap Jev pass before an expensive reviewer

[devagrawal09/jev-review](https://github.com/devagrawal09/jev-review) screens a
git diff with Jev: a noul risk matrix per file and dimension, then focused
`Choice`/`Score` calls to locate and rank the findings it followed. It costs a
fraction of an Opus review, so it is worth running first and handing the result
to the expensive reviewer as a hint.

| | |
|---|---|
| Upstream | `https://github.com/devagrawal09/jev-review` |
| Pinned commit | `31f8960` |
| Runtime | **Node 24**, via nvm, without changing this machine's default Node |
| Languages | **JavaScript and TypeScript only** |

## It is JS/TS only

This matters and is easy to forget. `jev-review` screens source files it can
parse as JS/TS. Point it at a Python, shell or mixed repository and it will
report little or nothing, which reads exactly like "nothing wrong". The
wrapper's note says "JS/TS only" on every run for that reason. Most of
airlock's own code is Python, so this component earns its keep on the
TypeScript repositories on the box, not on this one.

## Node 24 without moving the default

The default Node here is the system one (22), and things are built against it.
`nvm alias default 24` would quietly change every new shell. Both scripts
instead source `nvm.sh --no-use` and resolve Node 24's `bin` directory for the
duration of one command. **Do not run `nvm alias default 24`.**

## Install

```bash
review/install.sh                      # clones to $HOME/code/jev-review
review/install.sh --dest /path/to/dir
```

The upstream scripts run `node --env-file=.env`, so the key has to be in a
`.env` inside the clone. `install.sh` writes it, mode 600, from
`TYPESAFE_API_KEY` if that is already loaded; otherwise it tells you the two
lines to run. The key is never echoed.

## The wrapper

```bash
review/jev-prefilter.sh --repo /path/to/repo --gate 'my-review-command'
review/jev-prefilter.sh --repo /path/to/repo --print     # just show the note
```

It runs `review:changes` over the repository's current diff, sorts the findings
by severity, keeps the top few (`--max-findings`, default 5) and runs:

```
<gate> --note "<summary>" [extra args after --]
```

The summary names file, line, dimension, severity and the model's confidence in
that severity, under a header stating that these are hints rather than verdicts.

Set the gate once per machine with `AIRLOCK_REVIEW_GATE` instead of passing
`--gate` every time.

## It fails open

Every failure path produces a note saying the prefilter was unavailable, and
**still runs the gate**. No clone, no Node 24, no key, API down, a timeout
under `AIRLOCK_REVIEW_TIMEOUT_S` (default 300 s), no diff, unrecognised JSON. A prefilter that can block a review is worse than no prefilter. Failures
are reported on stderr so they are visible rather than silent.

The one thing it will not do is claim a clean bill of health: when there are no
findings the note says "Nothing flagged. Treat that as no signal, not as a
pass."

## Environment

| Variable | Default | What it does |
|---|---|---|
| `AIRLOCK_REVIEW_DIR` | `$HOME/code/jev-review` | Where the clone lives |
| `AIRLOCK_REVIEW_TARGET` | `$PWD` | Repository to review |
| `AIRLOCK_REVIEW_GATE` | unset | Default gate command |
| `AIRLOCK_REVIEW_MAX_FINDINGS` | `5` | Findings named in the note |
| `AIRLOCK_REVIEW_TIMEOUT_S` | `300` | Hard timeout on the review run |
