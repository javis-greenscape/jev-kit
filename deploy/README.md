# Installing the airlock daemon

`airlock/daemon.py` keeps a warm HTTPS connection to TypeSafe so a
judgement on this box costs ~0.3s instead of ~0.9s. This is not installed or
enabled automatically -- do it by hand:

```bash
mkdir -p ~/.config/systemd/user
cp "$PWD/deploy/airlock-daemon.service" ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now airlock-daemon.service
```

From a non-login shell (e.g. a fresh SSH session before any GUI/session
manager has set it), `XDG_RUNTIME_DIR` may not be exported, and `systemctl
--user` will fail to find the user bus. Set it first:

```bash
export XDG_RUNTIME_DIR=/run/user/$(id -u)
```

## Checking it's running

```bash
systemctl --user status airlock-daemon.service
pgrep -af airlock.daemon
```

The socket appears at `$XDG_RUNTIME_DIR/airlock/airlock.sock` (directory mode 700,
socket mode 600) once the daemon is up. `airlock/client.py`'s `ask()`
function uses it automatically when present and falls back to a direct HTTPS
call otherwise, so nothing else needs to change to benefit from it.

## Logs

The daemon logs one line per request to stderr, which journald captures:

```bash
journalctl --user -u airlock-daemon.service -f
```

Log lines never contain the API key, the request body, or Jev's answers --
just the request id, HTTP status, latency, whether the connection was
reused, and token counts.

## Stopping / restarting

```bash
systemctl --user restart airlock-daemon.service
systemctl --user stop airlock-daemon.service
systemctl --user disable airlock-daemon.service
```

## Tuning the pool

Pool size defaults to 2 persistent connections. Override with an
`Environment=` line in the unit (or a drop-in) before `daemon-reload`:

```
Environment=AIRLOCK_DAEMON_POOL=3
```

## The key

The daemon reads `TYPESAFE_API_KEY` the same way `airlock/keyfile.py`
does: the environment first, then `~/.config/airlock/env`. Nothing needs
to be added to the unit file for this -- the file is mode 600 and owned by
this user, and the daemon runs as this user under systemd `--user`.
