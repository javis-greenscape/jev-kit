#!/usr/bin/env bash
# Promote the auto-tune branch to main. This is the ONLY script in this
# directory that touches the main working tree, and it does so deliberately:
# tune.sh (run by the timer) never merges auto-tune into main and never
# checks out anything in the main repo. Run this by hand (or have the
# director run it) after reviewing `git log auto-tune` in the tune worktree.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
. "$SCRIPT_DIR/../install/repo-path.sh"

STATE_DIR="${AIRLOCK_TUNE_STATE_DIR:-$HOME/.local/state/airlock}"
WORKTREE_DIR="${AIRLOCK_TUNE_WORKTREE_DIR:-$STATE_DIR/tune-worktree}"

if [ ! -d "$WORKTREE_DIR" ]; then
  echo "no tune worktree at $WORKTREE_DIR -- nothing to promote" >&2
  exit 1
fi

# Resolve the repo the same way tune.py does (env -> repo.path pointer -> our
# own parent if a checkout), rather than trusting `git worktree list` on
# $WORKTREE_DIR -- that would just report whatever repo the worktree happens
# to already belong to, which is exactly wrong when it is a foreign leftover.
if ! MAIN_REPO="$(airlock_tune_repo "$SCRIPT_DIR")"; then
  echo "could not resolve a repository (no AIRLOCK_TUNE_REPO, no repo.path, not a checkout); refusing to promote" >&2
  exit 1
fi

WORKTREE_REPO="$(git -C "$WORKTREE_DIR" worktree list --porcelain | awk '/^worktree /{print $2; exit}')"
if [ -z "$WORKTREE_REPO" ] || [ "$(cd "$WORKTREE_REPO" && pwd)" != "$(cd "$MAIN_REPO" && pwd)" ]; then
  echo "tune worktree at $WORKTREE_DIR belongs to $WORKTREE_REPO, not the resolved repo $MAIN_REPO; refusing to promote" >&2
  echo "  (the tuning loop retires a foreign worktree on its next run; run tune.sh once, then retry)" >&2
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
