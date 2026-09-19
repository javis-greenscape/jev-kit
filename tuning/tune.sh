#!/usr/bin/env bash
# Thin entrypoint for systemd (and manual runs): all logic lives in tune.py
# so it can be tested and reasoned about as ordinary Python. This script's
# only job is to find its own directory portably and hand off.
#
# tune.py resolves its OWN repo (env -> repo.path pointer -> its own parent if
# a git checkout -> give up) and exits 0 with one log line if none is found --
# tuning is optional, and a fresh or deployed-only install must never fail the
# timer over it. The auto-promote check below needs the same answer for a
# different purpose (finding auto-tune's ahead-count against main), so it
# resolves it the same way rather than assuming its own location is a checkout.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_DIR="${AIRLOCK_CONFIG_DIR:-$HOME/.config/airlock}"

# Machine-specific tuning settings, written by install/install.sh --tuning:
# the absolute path of the `claude` binary (a systemd user unit's PATH does
# not include an npm global prefix under $HOME, which is why every unattended
# run died with "No such file or directory: 'claude'"), and optionally which
# account tree the judge bills and which model/effort it uses. Paths and
# names only -- never a key. tune.py can resolve the binary on its own too;
# this just makes the answer explicit and auditable per machine.
if [ -f "$CONFIG_DIR/tune.env" ]; then
  set -a
  # shellcheck disable=SC1091
  . "$CONFIG_DIR/tune.env"
  set +a
fi

# shellcheck disable=SC1091
. "$SCRIPT_DIR/../install/repo-path.sh"
python3 "$SCRIPT_DIR/tune.py" "$@"

# Optional auto-promotion. Off unless the flag file exists, so promotion stays
# a deliberate choice per machine. Safe while the guards are shadow-only and
# fail-open; remove the flag before any guard is allowed to block.
STATE_DIR="${AIRLOCK_TUNE_STATE_DIR:-$HOME/.local/state/airlock}"
WORKTREE_DIR="${AIRLOCK_TUNE_WORKTREE_DIR:-$STATE_DIR/tune-worktree}"
if [ -e "$CONFIG_DIR/auto-promote" ] && [ -d "$WORKTREE_DIR" ]; then
  if MAIN_REPO="$(airlock_tune_repo "$SCRIPT_DIR")"; then
    AHEAD="$(git -C "$WORKTREE_DIR" rev-list --count "$(git -C "$MAIN_REPO" rev-parse main)..auto-tune" 2>/dev/null || echo 0)"
    if [ "$AHEAD" -gt 0 ]; then
      echo "auto-promote: auto-tune is $AHEAD commit(s) ahead of main, promoting"
      "$SCRIPT_DIR/promote.sh" || echo "auto-promote: promote.sh refused or failed; main unchanged" >&2
    fi
  else
    echo "[tune] no repository resolved (no AIRLOCK_TUNE_REPO, no repo.path, not a checkout); skipping auto-promote" >&2
  fi
fi
