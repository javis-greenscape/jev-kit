#!/usr/bin/env bash
# Stop-hook wrapper for jev-belay. Loads only the TypeSafe key, never prints
# it, and fails open (exits 0) whenever the key is missing or the kill switch
# is set -- exactly like the guard itself.
#
# The key-file path is not hard-coded here: it comes from the shared config
# (install/config.env's AIRLOCK_KEY_FILE), the same variable every other
# component in this repository reads its key path from.
set -u
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
[ -e "$HOME/.config/airlock/disabled" ] && exit 0
[ -e "$HOME/.config/jev-guard/disabled" ] && exit 0
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
envfile="$(airlock_key_file)"
if [ -z "${TYPESAFE_API_KEY:-}" ] && [ -r "$envfile" ]; then
  TYPESAFE_API_KEY="$(sed -n 's/^ *\(export \)\?TYPESAFE_API_KEY=//p' "$envfile" | head -1)"
fi
[ -n "${TYPESAFE_API_KEY:-}" ] || exit 0
export TYPESAFE_API_KEY JEV_BELAY_LOG=1
belay_home="${AIRLOCK_BELAY_DIR:-$HOME/.local/share/jev-belay}"
[ -e "$belay_home/current/belay.mjs" ] || belay_home="$here"
exec node "$belay_home/current/belay.mjs"
