#!/usr/bin/env bash
# Export the current main-branch commit to a deployed, immutable release
# directory and flip `current` to point at it. This is what settings.json
# hooks and the systemd units should reference -- NOT any development
# checkout -- so a merge to main never changes what a live session is
# running mid-session.
#
# No path here is hard-coded to a particular user: everything derives from
# $HOME (via AIRLOCK_HOME) or from git itself (the main worktree, found the same
# way tuning/tune.py does).
#
# This script only ever touches $AIRLOCK_HOME. It never edits settings.json or
# any systemd unit -- that is install/wire.sh's job, and the director's call
# on when to run it.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git -C "$SCRIPT_DIR" rev-parse --show-toplevel)"

# The MAIN checkout is always the first entry `git worktree list` reports,
# regardless of which worktree this script is invoked from -- same trick
# tuning/tune.py's find_main_repo() uses, so it's never hard-coded here.
MAIN_REPO="$(git -C "$REPO_ROOT" worktree list --porcelain | awk '/^worktree /{print $2; exit}')"
if [ -z "$MAIN_REPO" ]; then
  echo "deploy.sh: could not determine main repo from 'git worktree list'" >&2
  exit 1
fi

BASE_BRANCH="${AIRLOCK_DEPLOY_BASE_BRANCH:-main}"
AIRLOCK_HOME="${AIRLOCK_HOME:-$HOME/.local/share/airlock}"
RELEASES_DIR="$AIRLOCK_HOME/releases"
KEEP_RELEASES=5

SHA="$(git -C "$MAIN_REPO" rev-parse "$BASE_BRANCH")"
SHORT_SHA="$(git -C "$MAIN_REPO" rev-parse --short "$SHA")"

echo "deploy.sh: exporting $BASE_BRANCH @ $SHORT_SHA ($MAIN_REPO)" >&2

# --- 1. run the unit tests against a clean, throwaway export of exactly
# this commit -- never against MAIN_REPO's working tree, which could be
# dirty or mid-edit, and never against a stale prior export. ------------
TEST_TMP="$(mktemp -d)"
trap 'rm -rf "$TEST_TMP"' EXIT
git -C "$MAIN_REPO" archive "$SHA" | tar -x -C "$TEST_TMP"
echo "deploy.sh: running unit tests against the clean export..." >&2
# Belt-and-braces on top of the suite's own tests/__init__.py isolation
# fixture: run it with $HOME pointed at a throwaway directory too, and with
# every AIRLOCK_*/legacy override var this shell might have exported
# stripped, so THIS machine's own ~/.config/airlock/rules.json or mode
# cannot change the test outcome and refuse a deploy that is actually fine.
TEST_HOME="$(mktemp -d)"
( cd "$TEST_TMP" && env -u AIRLOCK_CONFIG_DIR -u PLUMBLINE_CONFIG_DIR -u JEV_GUARD_CONFIG_DIR \
    -u AIRLOCK_STATE_DIR -u PLUMBLINE_STATE_DIR -u JEV_GUARD_STATE_DIR \
    -u AIRLOCK_HOME -u PLUMBLINE_HOME -u JEV_HOME \
    -u AIRLOCK_KEY_FILE -u PLUMBLINE_KEY_FILE -u JEV_GUARD_KEY_FILE \
    -u AIRLOCK_MODE -u PLUMBLINE_MODE -u JEV_GUARD_MODE \
    HOME="$TEST_HOME" python3 -m unittest discover -s tests )
rm -rf "$TEST_HOME"
echo "deploy.sh: unit tests passed" >&2

# --- 2. export the commit into its own release directory ---------------
mkdir -p "$RELEASES_DIR"
RELEASE_DIR="$RELEASES_DIR/$SHORT_SHA"

if [ -d "$RELEASE_DIR" ]; then
  echo "deploy.sh: release $SHORT_SHA already exported at $RELEASE_DIR, re-using it" >&2
else
  EXPORT_TMP="$(mktemp -d "$RELEASES_DIR/.export-XXXXXX")"
  git -C "$MAIN_REPO" archive "$SHA" | tar -x -C "$EXPORT_TMP"
  mv "$EXPORT_TMP" "$RELEASE_DIR"
fi

# --- 3. flip `current` atomically: build the new symlink under a temp
# name in the same directory, then rename over the old one -- rename is
# atomic on the same filesystem, so `current` is never observed missing
# or half-written by a concurrently running hook/daemon. -----------------
TMP_LINK="$AIRLOCK_HOME/.current.tmp.$$"
ln -sfn "$RELEASE_DIR" "$TMP_LINK"
mv -T "$TMP_LINK" "$AIRLOCK_HOME/current"

echo "deploy.sh: current -> $RELEASE_DIR" >&2

# --- 4. prune old releases, keeping the newest $KEEP_RELEASES; never
# delete the one `current` points at even if clock skew put it out of
# the newest-N window. ---------------------------------------------------
CURRENT_REAL="$(readlink -f "$AIRLOCK_HOME/current")"
mapfile -t ALL_RELEASES < <(ls -1t "$RELEASES_DIR" 2>/dev/null | grep -v '^\.export-')
if [ "${#ALL_RELEASES[@]}" -gt "$KEEP_RELEASES" ]; then
  for old in "${ALL_RELEASES[@]:$KEEP_RELEASES}"; do
    old_path="$RELEASES_DIR/$old"
    if [ "$old_path" != "$CURRENT_REAL" ]; then
      rm -rf "$old_path"
      echo "deploy.sh: pruned old release $old" >&2
    fi
  done
fi

echo
echo "Deployed $SHORT_SHA. Reference these paths in settings.json hooks and systemd units:"
echo "  hook:              $AIRLOCK_HOME/current/hooks/airlock.py"
echo "  WorkingDirectory=   $AIRLOCK_HOME/current"
echo
echo "Run install/wire.sh --print <settings.json...> to preview the settings.json edit."
