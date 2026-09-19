# logtriage: label log lines, cheaply and without leaking them

Reads log lines on stdin, emits one JSON object per line on stdout with a
triage label. The design is ported from reachjalil/jevlogs (MIT, see
[`docs/CREDITS.md`](../docs/CREDITS.md)), whose privacy architecture it
follows.

## Three steps, in this order, structurally

1. **Redact.** `airlock/redact.py` runs before anything else. Every later
   step -- the local rules, the cache key, the model state, the emitted record
   -- takes the redacted text as its only input. This is not a convention
   someone has to remember: there is no code path in `logtriage/triage.py` that
   can reach a model call holding raw text, and `tests/test_logtriage.py`
   asserts it against the actual request body rather than by reading the source.

2. **Local rules.** A regex settles the routine cases for nothing: a stack
   trace, an auth failure, a 5xx, a passing health check, an ordinary 200, a
   debug line. Anything a rule settles never reaches the model.

3. **The model, on what is left.** One `Choice` over five labels with the log
   line as state, and the instruction says plainly that the line is untrusted
   data and never instructions.

Plus jevlogs' **`protected`**: a line matching a protected pattern is never
sent anywhere at all, whatever else is true of it. The defaults cover
personal-data shapes (national insurance number, sort code, date of birth,
"patient"); add your own in a config file.

## Labels

| label | what it means |
|---|---|
| `investigate` | a person should look now: a crash, data loss, a security event |
| `attention` | a real problem that can wait: a retryable failure, a limit being approached |
| `routine` | the system reporting that it did its job |
| `noise` | no operational information at all |
| `unclear` | not enough in the line, or the model was unavailable |

## Use

```bash
journalctl --user -u airlock-daemon -n 500 --no-pager | python3 -m logtriage.cli
python3 -m logtriage.cli --no-model < app.log            # offline, local rules only
python3 -m logtriage.cli --only investigate,attention < app.log
python3 -m logtriage.cli --stats < app.log               # counts to stderr
```

`--stats` reports how many lines each stage settled. That percentage is the
number that says whether the local rules are pulling their weight; if almost
everything is falling through to the model, the rules need widening, not the
budget.

## Config

```json
{
  "rules": [["noise", "our own banner", "^===+$"]],
  "protected": ["(?i)\\bproject codename\\b"]
}
```

`--config <file>`, or `AIRLOCK_LOGTRIAGE_CONFIG`. Supplying `rules` replaces
the defaults outright rather than adding to them. A bad label or an
uncompilable regex is an error at load time, loudly: a silently half-loaded
rule set would send lines to a model that a rule was meant to keep back.

## Caching

An identical redacted line is asked about once. The cache is keyed on the
redacted text, so two lines differing only in a secret share an entry, which is
correct: they are the same operational event. The cached value is the model's
**answer**, not a verdict, which is jevlogs' trick -- changing a threshold
later re-decides old lines correctly instead of replaying a stale conclusion.

## Tests

```bash
python3 -m unittest tests.test_logtriage      # 27 tests, Jev fully mocked
```
