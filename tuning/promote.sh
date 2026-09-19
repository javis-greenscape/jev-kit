#!/usr/bin/env bash
# Promote the auto-tune branch to main. This is the ONLY script in this
# directory that touches the main working tree, and it does so deliberately:
# tune.sh (run by the timer) never merges auto-tune into main and never
# checks out anything in the main repo. Run this by hand (or have the
# director run it) after reviewing `git log auto-tune` in the tune worktree.
set -euo pipefail

STATE_DIR="${AIRLOCK_TUNE_STATE_DIR:-$HOME/.local/state/airlock}"
WORKTREE_DIR="${AIRLOCK_TUNE_WORKTREE_DIR:-$STATE_DIR/tune-worktree}"

if [ ! -d "$WORKTREE_DIR" ]; then
  echo "no tune worktree at $WORKTREE_DIR -- nothing to promote" >&2
  exit 1
fi

MAIN_REPO="$(git -C "$WORKTREE_DIR" worktree list --porcelain | awk '/^worktree /{print $2; exit}')"
if [ -z "$MAIN_REPO" ]; then
  echo "could not determine main repo from git worktree list" >&2
  exit 1
fi

echo "Running unit tests on auto-tune ($WORKTREE_DIR) before promoting..."
( cd "$WORKTREE_DIR" && python3 -m unittest discover -s tests )

CURRENT_BRANCH="$(git -C "$MAIN_REPO" rev-parse --abbrev-ref HEAD)"
if [ "$CURRENT_BRANCH" != "main" ]; then
  echo "main repo ($MAIN_REPO) is checked out on '$CURRENT_BRANCH', not main; refusing to promote." >&2
  exit 1
fi

if ! git -C "$MAIN_REPO" diff --quiet || ! git -C "$MAIN_REPO" diff --cached --quiet; then
  echo "main repo ($MAIN_REPO) has uncommitted changes; refusing to promote onto a dirty tree." >&2
  exit 1
fi

echo "Tests pass. Fast-forwarding main ($MAIN_REPO) to auto-tune..."
git -C "$MAIN_REPO" fetch "$WORKTREE_DIR" auto-tune:refs/heads/auto-tune-incoming
git -C "$MAIN_REPO" merge --ff-only auto-tune-incoming
git -C "$MAIN_REPO" branch -d auto-tune-incoming

echo "main is now at $(git -C "$MAIN_REPO" rev-parse --short HEAD)"
echo
echo "main's working tree just changed, but nothing live runs from it directly"
echo "any more -- settings.json hooks and the systemd units reference a"
echo "deployed release under \$AIRLOCK_HOME/current, not this checkout. Run"
echo "  $MAIN_REPO/install/deploy.sh"
echo "now to export this commit, run its unit tests, and flip current to it."
