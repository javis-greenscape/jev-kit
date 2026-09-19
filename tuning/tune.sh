#!/usr/bin/env bash
# Thin entrypoint for systemd (and manual runs): all logic lives in tune.py
# so it can be tested and reasoned about as ordinary Python. This script's
# only job is to find its own directory portably and hand off.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
python3 "$SCRIPT_DIR/tune.py" "$@"

# Optional auto-promotion. Off unless the flag file exists, so promotion stays
# a deliberate choice per machine. Safe while the guards are shadow-only and
# fail-open; remove the flag before any guard is allowed to block.
CONFIG_DIR="${AIRLOCK_CONFIG_DIR:-$HOME/.config/airlock}"
STATE_DIR="${AIRLOCK_TUNE_STATE_DIR:-$HOME/.local/state/airlock}"
WORKTREE_DIR="${AIRLOCK_TUNE_WORKTREE_DIR:-$STATE_DIR/tune-worktree}"
if [ -e "$CONFIG_DIR/auto-promote" ] && [ -d "$WORKTREE_DIR" ]; then
  MAIN_REPO="$(cd "$SCRIPT_DIR/.." && pwd)"
  AHEAD="$(git -C "$WORKTREE_DIR" rev-list --count "$(git -C "$MAIN_REPO" rev-parse main)..auto-tune" 2>/dev/null || echo 0)"
  if [ "$AHEAD" -gt 0 ]; then
    echo "auto-promote: auto-tune is $AHEAD commit(s) ahead of main, promoting"
    "$SCRIPT_DIR/promote.sh" || echo "auto-promote: promote.sh refused or failed; main unchanged" >&2
  fi
fi
