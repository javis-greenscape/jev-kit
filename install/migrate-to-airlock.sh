#!/usr/bin/env bash
# Move the old jev-guard / plumbline directories to their airlock names.
#
# The project has been renamed twice: jev-guard -> plumbline -> airlock. A
# machine that has been running either earlier name has real data under the
# old names -- the mode file (which may say `enforce`), rules.json, the shadow
# log, loop-protection state and the tuning loop's state. airlock/paths.py
# already falls back to those directories when the airlock one does not
# exist, so nothing breaks before this script runs; this script is what makes
# the new names the real ones.
#
# Idempotent. Running it twice is a no-op. It NEVER merges or overwrites: if
# both a real source directory and a real destination directory exist it says
# what it found, leaves both alone, and exits non-zero for a person to look
# at. Nothing is ever deleted.
#
# The live layout it is written for is a real `plumbline` directory with a
# `jev-guard` symlink pointing at it. After migration both old names are
# symlinks to the airlock directory, so a systemd unit, a script or a stale
# shell that has not been repointed yet still reads exactly the same files.
#
# It touches only directories under $HOME. It does not edit any settings.json,
# does not touch a systemd unit, and does not stop or start anything.
set -uo pipefail

DRY_RUN=0
case "${1:-}" in
  --dry-run) DRY_RUN=1 ;;
  "") ;;
  *) echo "usage: $0 [--dry-run]" >&2; exit 2 ;;
esac

status=0

say() { echo "$@"; }

# A real directory: exists, is a directory, and is NOT a symlink.
is_real_dir() { [ -d "$1" ] && [ ! -L "$1" ]; }

# Point $1 at $2 as a symlink, unless $1 is a real directory (which would mean
# throwing away whatever is in it -- never do that silently). $4, when given,
# is the directory this run is moving FROM: in a dry run that directory has
# not moved yet, so it still looks real and must not be reported as leftover
# data.
link_back() {
  local old="$1" new="$2" label="$3" source="${4:-}"
  if [ "$DRY_RUN" = "1" ] && [ -n "$source" ] && [ "$old" = "$source" ]; then
    say "    WOULD link $old -> $new"
    return 0
  fi
  if is_real_dir "$old"; then
    say "  $label: $old is a REAL directory, not a symlink."
    say "    It is NOT the data airlock now uses, and it has been left exactly"
    say "    as it is. Compare it with $new yourself and remove it when you"
    say "    are satisfied nothing in it is wanted."
    return 0
  fi
  # Compare the link's IMMEDIATE target, not its fully resolved one. A
  # jev-guard symlink pointing at the plumbline symlink resolves to the same
  # place, but leaves a two-hop chain that breaks the moment somebody tidies
  # the middle link away; repoint it straight at the airlock directory.
  if [ -L "$old" ] && [ "$(readlink "$old")" = "$new" ]; then
    say "    $old -> $new (already)"
    return 0
  fi
  if [ "$DRY_RUN" = "1" ]; then
    say "    WOULD link $old -> $new"
    return 0
  fi
  ln -sfn "$new" "$old"
  say "    left a symlink at $old -> $new (safe to delete after cutover)"
}

# One slot (config / state / releases): new name, then the two older names,
# newest first.
migrate_slot() {
  local label="$1" new="$2" mid="$3" old="$4"
  local src=""

  if is_real_dir "$new"; then
    say "  $label: already migrated ($new)"
    link_back "$mid" "$new" "$label"
    link_back "$old" "$new" "$label"
    return 0
  fi

  if is_real_dir "$mid"; then
    src="$mid"
  elif is_real_dir "$old"; then
    src="$old"
  fi

  if [ -z "$src" ]; then
    say "  $label: no real directory under any old name -- nothing to migrate"
    return 0
  fi

  # $new is not a real directory here, but it could still be a stray symlink
  # (say, someone linked airlock -> plumbline by hand). Refuse rather than
  # move a directory on top of a link into itself.
  if [ -e "$new" ] || [ -L "$new" ]; then
    say "  $label: $new already exists and is not a real directory:"
    say "      $new -> $(readlink -f "$new" 2>/dev/null || echo '?')"
    say "    refusing to move $src on top of it. Remove the link and re-run."
    status=1
    return 0
  fi

  if [ "$DRY_RUN" = "1" ]; then
    say "  $label: WOULD move $src -> $new"
    link_back "$mid" "$new" "$label" "$src"
    link_back "$old" "$new" "$label" "$src"
    return 0
  fi

  mkdir -p "$(dirname "$new")"
  if ! mv -T "$src" "$new"; then
    say "  $label: mv failed for $src" >&2
    status=1
    return 0
  fi
  say "  $label: moved $src -> $new"

  # Leave BOTH old names behind as symlinks. The jev-guard one is usually
  # already a symlink pointing at the plumbline directory that has just moved,
  # so it is dangling at this point; ln -sfn repoints it at airlock.
  link_back "$mid" "$new" "$label"
  link_back "$old" "$new" "$label"
}

say "airlock: migrating jev-guard / plumbline directories under \$HOME ($HOME)"
if [ "$DRY_RUN" = "1" ]; then
  say "(dry run -- nothing will be moved)"
fi

migrate_slot "config" \
  "$HOME/.config/airlock" "$HOME/.config/plumbline" "$HOME/.config/jev-guard"
migrate_slot "state" \
  "$HOME/.local/state/airlock" "$HOME/.local/state/plumbline" "$HOME/.local/state/jev-guard"
migrate_slot "releases (AIRLOCK_HOME)" \
  "$HOME/.local/share/airlock" "$HOME/.local/share/plumbline" "$HOME/.local/share/jev-guard"

# The auto-tune loop keeps a git worktree under the state directory. Moving
# the state directory moves that worktree, and git records absolute paths on
# BOTH sides of the link (the worktree's own .git file, and the main
# checkout's .git/worktrees/<name>/gitdir). Neither is rewritten by mv, so the
# worktree is broken until `git worktree repair` is run. This script does not
# run git on a repository it does not own; it tells the operator instead.
TUNE_WORKTREE="$HOME/.local/state/airlock/tune-worktree"
if [ -e "$TUNE_WORKTREE" ]; then
  say
  say "The auto-tune git worktree moved with the state directory:"
  say "    $TUNE_WORKTREE"
  say "  git records absolute paths on both sides of a worktree link, so repair it:"
  say "      git -C \"$TUNE_WORKTREE\" worktree repair"
  say "      git -C <the main checkout> worktree repair \"$TUNE_WORKTREE\""
  say "  then confirm with:  git -C \"$TUNE_WORKTREE\" status"
fi

say
if [ "$DRY_RUN" = "1" ]; then
  say "Dry run complete."
elif [ "$status" = "0" ]; then
  say "Done. Check the mode survived the move:"
  say "    cat \$HOME/.config/airlock/mode"
  say
  say "Still to do, deliberately NOT done here:"
  say "  - repoint settings.json:  install/wire.sh --print <settings.json>"
  say "  - repoint the systemd units (deploy/, monitoring/, tuning/, filesearch/)"
  say "    and restart them; the old unit names still exist until you do."
else
  say "Finished with something needing a human -- see above."
fi

exit "$status"
