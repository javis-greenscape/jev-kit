# airlock health monitoring

`python3 -m airlock.health` is a one-shot check, budgeted to finish inside
5 seconds even if something hangs:

- does the daemon's Unix socket answer `{"op": "ping"}`
- does one real Jev call succeed **through the daemon specifically** (a tiny
  noul probe question -- not `client.ask()`'s daemon-then-fallback path,
  which would silently mask a dead daemon behind a working direct HTTPS call)
  and how long it took
- is the API key loadable (checked as a bool only -- never printed)
- the resolved mode (`shadow` / `enforce` / `off`)
- from the last hour of `~/.local/state/airlock/shadow.jsonl`: how many
  rows were actually judged (a real Jev call, not a no-deny-possible skip),
  how many fail-opened (an `error` field), the fail-open rate, how many
  looked like a deny, and p95 latency
- the auto-tune loop's configured backoff interval and how long since it
  last ran (`~/.local/state/airlock/tune_state.json`)

Exits 0 (healthy), 1 (degraded: the daemon answers but a real call or the key
failed, or the fail-open rate over the last hour exceeds 20%), or 2 (down:
the daemon socket didn't answer at all). Prints exactly one JSON line to
stdout, always -- nothing here ever prints the API key or the Kuma push URL.

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
   loads `TYPESAFE_API_KEY` (from `~/.config/airlock/env`, never shelled
   out, never printed) and, only if it's set, pushes the result to Uptime
   Kuma via `monitoring/kuma_push.py` -- a GET over IPv4 with a 5s timeout,
   `status=up|down`, `msg=<short>`, `ping=<daemon-ask latency ms>`. If the
   variable is unset, the push is skipped silently; Kuma is optional.
4. exits with `airlock.health`'s own exit code, so `systemctl status`
   reflects the last run's health.

`monitoring/airlock-health.timer` runs it every 5 minutes
(`OnBootSec=2min`, `OnUnitActiveSec=5min`, `Persistent=true`).

Both units reference `%h/.local/share/airlock/current` -- the deployed
release from `install/deploy.sh` (Job 3), not a development checkout, so a
merge to `main` never changes what the timer runs mid-cycle. Run
`install/deploy.sh` (and, once ready, `install/wire.sh --apply` for the hook
itself) before installing these units.

### Install (do this yourself -- not run automatically)

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

### The Uptime Kuma push monitor is a manual, one-time step

This repo can push to a Kuma push-type monitor once one exists, but it
cannot create one -- that's an admin action in the Kuma web UI: add a "Push"
monitor, copy its push URL, and put it in
`~/.config/airlock/env` as:

```
AIRLOCK_KUMA_PUSH_URL=<the push URL Kuma gives you>
```

Until a person does that, `run_health_check.sh` still logs to
`health.jsonl` and exits with the right code -- the Kuma push is additive,
not required for the health check itself to work.

### Reading the log

```bash
tail -f ~/.local/state/airlock/health.jsonl
```

One JSON line per run, oldest first, mode 600.
