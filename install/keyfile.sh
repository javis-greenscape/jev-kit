#!/usr/bin/env bash
# The shell half of the key-file resolution order. Source it; do not run it.
#
#     . "$(dirname "${BASH_SOURCE[0]}")/../install/keyfile.sh"
#     KEY_FILE="$(airlock_key_file)"
#
# There are exactly two implementations of this order and this is one of them:
# `airlock/keyfile.py` is the other, and its module docstring is where the
# order and the pointer-file trust rules are WRITTEN DOWN. Read that first.
# Everything in the shell -- install/install.sh, belay/install.sh,
# belay/run.sh, monitoring/run_health_check.sh, compaction/install.sh -- sources
# this file rather than keeping its own copy, because five copies is five
# chances for one of them to quietly resolve a different file than the hook
# does.
#
# Prints a PATH on stdout. Never the key, never the file's contents. Fails
# open: any error anywhere prints the generic default, which is what an
# un-configured machine uses anyway.

# Is $1 a regular file (not a symlink), owned by us, and not group- or
# world-writable? Silent; the caller decides what to say.
airlock_keyfile_trusted_file() {
  local p="$1" info uid mode
  [ -f "$p" ] || return 1
  [ -L "$p" ] && return 1
  info="$(stat -c '%u %a' "$p" 2>/dev/null)" || return 1
  uid="${info%% *}"
  mode="${info##* }"
  [ "$uid" = "$(id -u)" ] || return 1
  # Last two octal digits are group and other. Either carrying the write bit
  # (2, 3, 6, 7) means somebody else chooses what we parse.
  case "$mode" in
    *[2367][0-7]|*[0-7][2367]) return 1 ;;
  esac
  return 0
}

airlock_keyfile_trusted_dir() {
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
airlock_keyfile_pointer_path() {
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
  printf '%s\n' "$dir/keyfile.path"
}

# The path the pointer records, if the pointer is trustworthy and names an
# absolute path to an existing regular file. Otherwise nothing, exit 1.
airlock_keyfile_pointer_target() {
  local pointer recorded
  pointer="$(airlock_keyfile_pointer_path)"
  airlock_keyfile_trusted_file "$pointer" || return 1
  airlock_keyfile_trusted_dir "$(dirname "$pointer")" || return 1
  recorded="$(head -n1 "$pointer" 2>/dev/null)" || return 1
  # Trim surrounding whitespace without a subshell.
  recorded="${recorded#"${recorded%%[![:space:]]*}"}"
  recorded="${recorded%"${recorded##*[![:space:]]}"}"
  case "$recorded" in ""|"#"*) return 1 ;; esac
  recorded="${recorded/#\~/$HOME}"
  case "$recorded" in /*) ;; *) return 1 ;; esac
  [ -f "$recorded" ] || return 1
  [ -L "$recorded" ] && return 1
  printf '%s\n' "$recorded"
}

airlock_key_file() {
  local f="${AIRLOCK_KEY_FILE:-${PLUMBLINE_KEY_FILE:-${JEV_GUARD_KEY_FILE:-}}}"
  if [ -n "$f" ]; then
    printf '%s\n' "${f/#\~/$HOME}"
    return 0
  fi
  if [ -r "$HOME/.config/airlock/env" ]; then
    printf '%s\n' "$HOME/.config/airlock/env"
    return 0
  fi
  local recorded
  if recorded="$(airlock_keyfile_pointer_target)"; then
    printf '%s\n' "$recorded"
    return 0
  fi
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
}
