#!/usr/bin/env bash
# Invoked by airlock-health.service (a timer, every 5 minutes, NOT
# installed by this change -- see monitoring/README.md). Runs the health
# check, appends its one JSON line to ~/.local/state/airlock/health.jsonl
# (mode 600), and -- only if AIRLOCK_KUMA_PUSH_URL is configured -- pushes
# the result to Uptime Kuma. Exits with airlock.health's own exit code
# (0 healthy, 1 degraded, 2 down) so `systemctl status` reflects it.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
STATE_DIR="${AIRLOCK_STATE_DIR:-$HOME/.local/state/airlock}"
LOG_FILE="$STATE_DIR/health.jsonl"

mkdir -p "$STATE_DIR"
chmod 700 "$STATE_DIR" 2>/dev/null || true

LINE="$(cd "$REPO_ROOT" && python3 -m airlock.health)"
STATUS=$?

umask 077
echo "$LINE" >> "$LOG_FILE"
chmod 600 "$LOG_FILE" 2>/dev/null || true

# AIRLOCK_KUMA_PUSH_URL (or either older GS_KUMA_* name) lives in the
# key file (AIRLOCK_KEY_FILE, default ~/.config/airlock/env), loaded the exact
# same redacted way the rest of airlock loads TYPESAFE_API_KEY -- never
# printed, never put on a command line.
# Key-file resolution: the ONE shell implementation, shared with every other
# component here. The order and the pointer-file trust rules are documented in
# airlock/keyfile.py's module docstring. Fails open if the helper is missing.
if [ -r "$SCRIPT_DIR/../install/keyfile.sh" ]; then
  . "$SCRIPT_DIR/../install/keyfile.sh"
else
  airlock_key_file() { printf '%s\n' "${AIRLOCK_KEY_FILE:-$HOME/.config/airlock/env}"; }
fi
set -a
. "$(airlock_key_file)" 2>/dev/null || true
set +a

if [ -n "${AIRLOCK_KUMA_PUSH_URL:-}" ] || [ -n "${GS_KUMA_AIRLOCK_PUSH_URL:-}" ] \
   || [ -n "${GS_KUMA_JEV_PUSH_URL:-}" ]; then
  echo "$LINE" | python3 "$SCRIPT_DIR/kuma_push.py"
fi

exit "$STATUS"
