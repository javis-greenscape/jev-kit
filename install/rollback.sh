#!/usr/bin/env bash
# Point $AIRLOCK_HOME/current at the release immediately before the one it
# currently points at. Never touches settings.json or systemd units --
# those already reference $AIRLOCK_HOME/current, so a rollback here takes
# effect on the next tool call / daemon restart without any further edits.
set -euo pipefail

AIRLOCK_HOME="${AIRLOCK_HOME:-$HOME/.local/share/airlock}"
RELEASES_DIR="$AIRLOCK_HOME/releases"
CURRENT_LINK="$AIRLOCK_HOME/current"

if [ ! -L "$CURRENT_LINK" ] && [ ! -e "$CURRENT_LINK" ]; then
  echo "rollback.sh: no $CURRENT_LINK -- nothing deployed yet, nothing to roll back" >&2
  exit 1
fi

CURRENT_REAL="$(readlink -f "$CURRENT_LINK" || true)"
if [ -z "$CURRENT_REAL" ]; then
  echo "rollback.sh: $CURRENT_LINK exists but does not resolve -- refusing to guess" >&2
  exit 1
fi
CURRENT_NAME="$(basename "$CURRENT_REAL")"

mapfile -t ALL_RELEASES < <(ls -1t "$RELEASES_DIR" 2>/dev/null | grep -v '^\.export-')

PREVIOUS=""
for i in "${!ALL_RELEASES[@]}"; do
  if [ "${ALL_RELEASES[$i]}" = "$CURRENT_NAME" ]; then
    PREVIOUS="${ALL_RELEASES[$((i + 1))]:-}"
    break
  fi
done

if [ -z "$PREVIOUS" ]; then
  echo "rollback.sh: no release older than $CURRENT_NAME under $RELEASES_DIR -- cannot roll back" >&2
  exit 1
fi

TMP_LINK="$AIRLOCK_HOME/.current.tmp.$$"
ln -sfn "$RELEASES_DIR/$PREVIOUS" "$TMP_LINK"
mv -T "$TMP_LINK" "$CURRENT_LINK"

echo "rollback.sh: current $CURRENT_NAME -> $PREVIOUS"
echo "current -> $(readlink -f "$CURRENT_LINK")"
