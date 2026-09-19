#!/usr/bin/env bash
# The shell half of the tuning-repo resolution order. Source it; do not run it.
#
#     . "$(dirname "${BASH_SOURCE[0]}")/../install/repo-path.sh"
#     REPO="$(airlock_tune_repo "$SCRIPT_DIR")"
#
# There are exactly two implementations of this order and this is one of them:
# `airlock/repo_path.py` is the other, and its module docstring is where the
# order and the pointer-file trust rules are WRITTEN DOWN. Read that first.
# `tuning/tune.sh` and `tuning/promote.sh` both source this rather than
# keeping their own copy.
#
# Prints an absolute PATH on stdout, or nothing (with a message on stderr) if
# no repo could be resolved -- callers must treat "nothing printed" as "tuning
# is optional here", log one line, and exit 0. Never partial output.

airlock_repo_path_trusted_file() {
  local p="$1" info uid mode
  [ -f "$p" ] || return 1
  [ -L "$p" ] && return 1
  info="$(stat -c '%u %a' "$p" 2>/dev/null)" || return 1
  uid="${info%% *}"
  mode="${info##* }"
  [ "$uid" = "$(id -u)" ] || return 1
  case "$mode" in
    *[2367][0-7]|*[0-7][2367]) return 1 ;;
  esac
  return 0
}

airlock_repo_path_trusted_dir() {
  local p="$1" info uid mode
  [ -d "$p" ] || return 1
  info="$(stat -c '%u %a' "$p" 2>/dev/null)" || return 1
  uid="${info%% *}"
  mode="${info##* }"
  [ "$uid" = "$(id -u)" ] || return 1
  case "$mode" in
    *[2367][0-7]|*[0-7][2367]) return 1 ;;
  esac
  return 0
}

# The pointer file's path, whether or not it exists. Mirrors
# airlock/paths.py config_dir(): the AIRLOCK_/PLUMBLINE_/JEV_GUARD_ override
# first, then whichever of the three directory names exists.
airlock_repo_path_pointer_path() {
  local dir=""
  for candidate in "${AIRLOCK_CONFIG_DIR:-}" "${PLUMBLINE_CONFIG_DIR:-}" "${JEV_GUARD_CONFIG_DIR:-}"; do
    if [ -n "$candidate" ]; then dir="${candidate/#\~/$HOME}"; break; fi
  done
  if [ -z "$dir" ]; then
    for name in airlock plumbline jev-guard; do
      if [ -d "$HOME/.config/$name" ]; then dir="$HOME/.config/$name"; break; fi
    done
  fi
  [ -n "$dir" ] || dir="$HOME/.config/airlock"
  printf '%s\n' "$dir/repo.path"
}

# The path the pointer records, if the pointer is trustworthy and names an
# absolute, existing directory containing a .git. Otherwise nothing, exit 1.
airlock_repo_path_pointer_target() {
  local pointer recorded
  pointer="$(airlock_repo_path_pointer_path)"
  airlock_repo_path_trusted_file "$pointer" || return 1
  airlock_repo_path_trusted_dir "$(dirname "$pointer")" || return 1
  recorded="$(head -n1 "$pointer" 2>/dev/null)" || return 1
  recorded="${recorded#"${recorded%%[![:space:]]*}"}"
  recorded="${recorded%"${recorded##*[![:space:]]}"}"
  case "$recorded" in ""|"#"*) return 1 ;; esac
  recorded="${recorded/#\~/$HOME}"
  case "$recorded" in /*) ;; *) return 1 ;; esac
  [ -d "$recorded" ] || return 1
  [ -e "$recorded/.git" ] || return 1
  printf '%s\n' "$recorded"
}

# Resolution order: AIRLOCK_TUNE_REPO env (honoured as given) -> the repo.path
# pointer (checked) -> $1's own parent, if that is a git checkout -> nothing.
# $1 is the directory the calling script itself lives in (tuning/).
airlock_tune_repo() {
  local script_dir="$1" self_repo recorded
  if [ -n "${AIRLOCK_TUNE_REPO:-}" ]; then
    printf '%s\n' "${AIRLOCK_TUNE_REPO/#\~/$HOME}"
    return 0
  fi
  if recorded="$(airlock_repo_path_pointer_target)"; then
    printf '%s\n' "$recorded"
    return 0
  fi
  if [ -n "$script_dir" ]; then
    self_repo="$(cd "$script_dir/.." 2>/dev/null && pwd)"
    if [ -n "$self_repo" ] && [ -e "$self_repo/.git" ]; then
      printf '%s\n' "$self_repo"
      return 0
    fi
  fi
  return 1
}
