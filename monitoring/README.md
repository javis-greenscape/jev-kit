# airlock health monitoring

## Server or workstation?

The guard fails open by design. That is right, and it has one consequence
worth saying out loud: **a dead guard is silent.** No key, TypeSafe
unreachable, the health timer stopped, the daemon down with a slow fallback.
Every one of those looks exactly like a quiet, well-behaved machine.

How you notice depends on what kind of machine it is, and the two answers are
genuinely different:

| | Server | Workstation |
|---|---|---|
| Is silence a fault? | **Yes.** It is meant to be up | **No.** It usually means the machine is off |
| What to install | the health timer, `heartbeat` push mode, and a monitor that alerts on silence | the **session check** (default, installed already) |
| Optional extra | -- | `explicit` push mode, if you also want a phone alert |
| Heartbeat interval on the monitor | minutes | **days**, long enough that a closed laptop never trips it |

A monitor that alerts on silence is the right design for a server and the
wrong one for a laptop. It would cry wolf every evening and be muted inside a
week.

So the workstation answer is not a push at all. It is
`hooks/airlock_session_check.py`, a `SessionStart` hook that tells the person
at the one moment they are certainly there and certainly care, which is when
they start using Claude Code. It prints **nothing at all** when everything is
healthy, and ships in the default install set on every platform, native
Windows included. See the README's component table.

**The limit of `explicit` mode:** it cannot report a health timer that has
itself died. Nothing pushes, and with a multi-day heartbeat interval nothing
notices. That is exactly the trade that stops a powered-off laptop
alerting. The session check covers that case instead. The two are
complementary and neither replaces the other.

## The health check

`python3 -m airlock.health` is a one-shot check, budgeted to finish inside
5 seconds even if something hangs:

- does the daemon's Unix socket answer `{"op": "ping"}`
- does one real Jev call succeed **through the daemon specifically** (a tiny
  noul probe question, not `client.ask()`'s daemon-then-fallback path, which
  would silently mask a dead daemon behind a working direct HTTPS call) and
  how long it took
- is the API key loadable (checked as a bool only, never printed)
- the resolved mode (`shadow` / `enforce` / `off`)
- from the last hour of `~/.local/state/airlock/shadow.jsonl`: how many
  rows were actually judged (a real Jev call, not a no-deny-possible skip),
  how many fail-opened (an `error` field), the fail-open rate, how many
  looked like a deny, and p95 latency
- the auto-tune loop's configured backoff interval and how long since it
  last ran (`~/.local/state/airlock/tune_state.json`)

Exit 0 is healthy. Exit 1 is degraded: the daemon answers but a real call or
the key failed, or the fail-open rate over the last hour exceeds 20%. A daemon
socket that did not answer at all gives exit 2, down.

It prints exactly one JSON line to stdout, always. Nothing here ever prints the
API key or the Kuma push URL.

```bash
python3 -m airlock.health
echo $?
```

## The systemd units (NOT installed by this change)

`monitoring/airlock-health.service` (oneshot) runs
`monitoring/run_health_check.sh`, which:

1. runs `python3 -m airlock.health` and captures its one JSON line and
   exit code,
2. appends that line to `~/.local/state/airlock/health.jsonl` (directory
   mode 700, file mode 600),
3. loads `AIRLOCK_KUMA_PUSH_URL` (falling back to the older
   `GS_KUMA_*` names) the same redacted way `airlock/keyfile.py`
   loads `TYPESAFE_API_KEY` (from `~/.config/jev-kit/env`, never shelled
   out, never printed) and, only if it's set, pushes the result to Uptime
   Kuma via `monitoring/kuma_push.py`: a GET over IPv4 with a 5s timeout,
   `status=up|down`, `msg=<short>`, `ping=<daemon-ask latency ms>`. If the
   variable is unset, the push is skipped silently; the push monitor is
   optional. `AIRLOCK_KUMA_PUSH_MODE` chooses `heartbeat` (the default) or
   `explicit`; see "The two push modes" below.
4. exits with `airlock.health`'s own exit code, so `systemctl status`
   reflects the last run's health.

### The two push modes

`AIRLOCK_KUMA_PUSH_MODE` in the key file chooses between them. It defaults to
`heartbeat`, so an existing server is unchanged by this and needs no action.

```
# ~/.config/jev-kit/env
AIRLOCK_KUMA_PUSH_URL=<the push URL your monitor gives you>
AIRLOCK_KUMA_PUSH_MODE=explicit        # omit entirely for a server
```

| Mode | Healthy | Faulty | Who it is for |
|---|---|---|---|
| `heartbeat` (default) | `status=up&msg=airlock: healthy` | `status=down&msg=airlock: <status>` | a server, watched by its monitor's own silence timeout |
| `explicit` | the same `status=up` | `status=down&msg=<which check failed>` | a workstation: the failure is stated, so silence never alerts |

In `explicit` mode the `msg` names the failing check from the health row
itself: `daemon ping failed: connection refused`, `no API key resolves`,
`fail-open rate 37% in the last hour`. It is put through `airlock/redact.py`,
has anything that looks like a home directory replaced with `<home>` (a home
directory carries a username, and a username is a person), and is capped at
200 characters. A key never reaches it.

`monitoring/airlock-health.timer` runs it every 5 minutes
(`OnBootSec=2min`, `OnUnitActiveSec=5min`, `Persistent=true`).

Both units reference `%h/.local/share/airlock/current`, the deployed release
from `install/deploy.sh` (Job 3) rather than a development checkout. So a merge
to `main` never changes what the timer runs mid-cycle. Run
`install/deploy.sh` (and, once ready, `install/wire.sh --apply` for the hook
itself) before installing these units.

### Install it yourself; nothing here runs automatically

```bash
mkdir -p ~/.config/systemd/user
cp monitoring/airlock-health.service monitoring/airlock-health.timer ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now airlock-health.timer
systemctl --user list-timers airlock-health.timer
```

From a non-login shell, `XDG_RUNTIME_DIR` may be unset:

```bash
export XDG_RUNTIME_DIR=/run/user/$(id -u)
```

Verify the unit files are well-formed without installing them:

```bash
export XDG_RUNTIME_DIR=/run/user/$(id -u)
systemd-analyze --user verify monitoring/*.service monitoring/*.timer
```

### The push monitor is a manual, one-time step

Any service that accepts a GET carrying `status` (`up`/`down`) and `msg` works
here; Uptime Kuma is simply the one this was built against, which is why the
variable keeps its name.

This repo can push to a push-type monitor once one exists, but it cannot
create one. That is an admin action in the Kuma web UI: add a "Push" monitor,
copy its push URL, and put it in
`~/.config/jev-kit/env` as:

```
AIRLOCK_KUMA_PUSH_URL=<the push URL Kuma gives you>
```

If the machine is a workstation, add the mode line too, and set that
monitor's heartbeat interval to **days** rather than minutes:

```
AIRLOCK_KUMA_PUSH_MODE=explicit
```

Until a person does that, `run_health_check.sh` still logs to
`health.jsonl` and exits with the right code. The push is additive, and the
health check works without it.

### Reading the log

```bash
tail -f ~/.local/state/airlock/health.jsonl
```

One JSON line per run, oldest first, mode 600.
