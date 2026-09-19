# compaction: fast-jev-compaction (optional, off by default, read this first)

[tamaratran/fast-jev-compaction](https://github.com/tamaratran/fast-jev-compaction)
is a Claude Code plugin that compacts context automatically once a session
passes a token threshold (default 60% of the context window), asking
TypeSafe's Jev model per tool call whether the call and its result still
need to stay in history.

## What it sends off the machine

**This is the largest default-on data egress of anything used by this
repository.** Read from its own source: the plugin builds a
conversation state of up to `maxStateTokens` (default **25,000 tokens**) made
of tool inputs and tool-result text, truncated only for size, with **no
redaction pass anywhere in the plugin's source** -- there is no `redact()`
call, no secret scrubber, nothing. In practice that means document text,
email bodies, and the contents of `Read`/`Bash` output going to TypeSafe on
most long turns, as-is.

Compare that with `belay/`, which sends only the task text, the final
message and check command lines, through a 13-rule redactor, capped at a few
thousand characters. Compaction is a different order of exposure.

## Why it might still be worth it

Compaction is genuinely useful and, run manually, genuinely fast. Measured
once on this box, 2026-09-19: a manual `/compact` took a 49,288-token session
down to 23,111 tokens in 906 ms. **The decision taken here was to ship the
installer but never to turn it on**: the exposure above is why. This component
exists so a machine that has made its own decision about that trade-off can
install it deliberately, not so that it becomes a default.

It is **off by default** everywhere in this repository. `install/install.sh`
never turns it on without `--compaction`, and nothing here ever will.

## Requirements

- Claude Code **2.1.274 or later**, with function hooks enabled
  (`CLAUDE_CODE_ENABLE_FUNCTION_HOOKS=1` in the account's `settings.json`
  `env` block).
- A loadable `TYPESAFE_API_KEY` (environment, or the key file).

## Install

```bash
set -a; . ~/.config/airlock/env 2>/dev/null; set +a   # loads the key into this shell only
compaction/install.sh
```

`install.sh`:

1. checks the Claude Code version;
2. checks whether `CLAUDE_CODE_ENABLE_FUNCTION_HOOKS=1` is already set in the
   account's `settings.json`, and if not, **prints** the edit and stops --
   it does not write settings.json itself;
3. reads the key into a shell variable (never echoed, never logged);
4. runs `claude plugin marketplace add tamaratran/fast-jev-compaction`;
5. runs `claude plugin install fast-jev-compaction@fast-jev-compaction --config apiKey=$TYPESAFE_API_KEY`.

## If you want it, but redacted

What that would take is a fork: lift `jev-belay`'s `SECRET_RULES` redactor and
apply it in `historyEntries()` before the state is built, and drop
`maxStateTokens` hard. That is not what this `install.sh` does -- it installs
upstream as published. Forking it is a separate, deliberate piece of work.

## Uninstall

```bash
claude plugin uninstall fast-jev-compaction@fast-jev-compaction
claude plugin marketplace remove tamaratran/fast-jev-compaction
```

Then remove `CLAUDE_CODE_ENABLE_FUNCTION_HOOKS` from `settings.json` if
nothing else in that account needs it.
