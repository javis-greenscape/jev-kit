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
# The key file: AIRLOCK_KEY_FILE if set, else the generic ~/.config/airlock/env,
# else the path earlier installs of this project used. Nothing here reads the
# VALUE of anything in it onto a command line.
airlock_key_file() {
  local f="${AIRLOCK_KEY_FILE:-}"
  if [ -n "$f" ]; then
    printf '%s\n' "${f/#\~/$HOME}"
  elif [ -r "$HOME/.config/airlock/env" ]; then
    printf '%s\n' "$HOME/.config/airlock/env"
  else
    # Any extra path this machine's install/config.env names, in order.
    # Nothing is hard-coded here: on a fresh machine the loop is empty.
    local IFS=:
    local candidate
    for candidate in ${AIRLOCK_LEGACY_KEY_FILES:-}; do
      [ -n "$candidate" ] || continue
      candidate="${candidate/#\~/$HOME}"
      if [ -r "$candidate" ]; then
        printf '%s\n' "$candidate"
        return 0
      fi
    done
    printf '%s\n' "$HOME/.config/airlock/env"
  fi
}
set -a
. "$(airlock_key_file)" 2>/dev/null || true
set +a

if [ -n "${AIRLOCK_KUMA_PUSH_URL:-}" ] || [ -n "${GS_KUMA_AIRLOCK_PUSH_URL:-}" ] \
   || [ -n "${GS_KUMA_JEV_PUSH_URL:-}" ]; then
  echo "$LINE" | python3 "$SCRIPT_DIR/kuma_push.py"
fi

exit "$STATUS"
