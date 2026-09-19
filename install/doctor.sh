#!/usr/bin/env bash
# airlock: prove each installed piece actually runs.
#
# A download that succeeded is not a capability that works. This script does
# not check that files exist -- it runs things and reads the output:
#
#   - the health check, end to end
#   - ONE REAL DENY and ONE REAL ALLOW through the actual hook process, in
#     enforce mode, against a throwaway HOME, so nothing touches the real log
#     or the real loop-protection state
#   - a plocate query against the real index
#   - a daemon ping over the Unix socket
#
# Exit 0 if everything that is installed works, 1 otherwise. A component that
# is not installed is reported as "skipped", not as a failure.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

if [ -f "$SCRIPT_DIR/config.env" ]; then
  set -a
  # shellcheck disable=SC1091
  . "$SCRIPT_DIR/config.env"
  set +a
fi

AIRLOCK_HOME="${AIRLOCK_HOME:-${PLUMBLINE_HOME:-${JEV_HOME:-$HOME/.local/share/airlock}}}"
PY="${PYTHON:-python3}"
: "${XDG_RUNTIME_DIR:=/run/user/$(id -u)}"
export XDG_RUNTIME_DIR

FAILED=0
PASSED=0
SKIPPED=0

pass() { printf '  PASS  %s\n' "$*"; PASSED=$((PASSED + 1)); }
fail() { printf '  FAIL  %s\n' "$*"; FAILED=$((FAILED + 1)); }
skip() { printf '  skip  %s\n' "$*"; SKIPPED=$((SKIPPED + 1)); }
head_() { printf '\n== %s\n' "$*"; }

# Which copy of airlock is live? Prefer the deployed release, since that is
# what settings.json and the units actually run.
LIVE="$REPO_ROOT"
if [ -e "$AIRLOCK_HOME/current" ]; then
  LIVE="$(readlink -f "$AIRLOCK_HOME/current")"
fi
HOOK="$LIVE/hooks/airlock.py"

echo "airlock doctor"
echo "  live copy: $LIVE"
echo "  python:    $("$PY" -c 'import sys;print(sys.executable, "%d.%d.%d"%sys.version_info[:3])' 2>/dev/null || echo "NOT USABLE")"

# ---------------------------------------------------------------------------
head_ "The hook process"

if [ ! -f "$HOOK" ]; then
  fail "no hook at $HOOK -- run install/install.sh --guard"
else
  TMPHOME="$(mktemp -d)"
  trap 'rm -rf "$TMPHOME"' EXIT

  # --- one deny -----------------------------------------------------------
  # R6: opening a browser on a headless box. Code-only, so this needs no API
  # key and no network -- it proves the hook process, the rules table, the
  # mode resolution and the deny JSON, and nothing else.
  deny_out="$(printf '%s' '{"session_id":"doctor-deny","cwd":"/tmp","tool_name":"Bash","tool_input":{"command":"xdg-open https://example.com"}}' \
    | HOME="$TMPHOME" AIRLOCK_MODE=enforce "$PY" "$HOOK" 2>/dev/null)"
  if printf '%s' "$deny_out" | "$PY" -c '
import json,sys
d = json.load(sys.stdin)["hookSpecificOutput"]
sys.exit(0 if d.get("permissionDecision") == "deny" else 1)
' 2>/dev/null; then
    pass "deny: xdg-open blocked in enforce mode"
    printf '        %s\n' "$(printf '%s' "$deny_out" | "$PY" -c 'import json,sys;print(json.load(sys.stdin)["hookSpecificOutput"]["permissionDecisionReason"].splitlines()[0])' 2>/dev/null)"
  else
    fail "deny: expected a deny for 'xdg-open', got: ${deny_out:-<nothing>}"
  fi

  # --- one allow ----------------------------------------------------------
  # An ordinary command no rule covers must produce NOTHING at all: no JSON,
  # no log row, no subprocess. Empty stdout is the whole assertion.
  allow_out="$(printf '%s' '{"session_id":"doctor-allow","cwd":"/tmp","tool_name":"Bash","tool_input":{"command":"echo hello"}}' \
    | HOME="$TMPHOME" AIRLOCK_MODE=enforce "$PY" "$HOOK" 2>/dev/null)"
  if [ -z "$allow_out" ]; then
    pass "allow: 'echo hello' passes through silently, no output"
  else
    fail "allow: expected no output, got: $allow_out"
  fi

  # --- fail-open ----------------------------------------------------------
  broken_out="$(printf '%s' 'not json at all' \
    | HOME="$TMPHOME" AIRLOCK_MODE=enforce "$PY" "$HOOK" 2>/dev/null; echo "rc=$?")"
  if [ "$broken_out" = "rc=0" ]; then
    pass "fail-open: malformed payload exits 0 with no output"
  else
    fail "fail-open: expected 'rc=0' and no output, got: $broken_out"
  fi

  # --- kill switch --------------------------------------------------------
  killed_out="$(printf '%s' '{"session_id":"doctor-kill","cwd":"/tmp","tool_name":"Bash","tool_input":{"command":"xdg-open https://example.com"}}' \
    | HOME="$TMPHOME" AIRLOCK_MODE=enforce AIRLOCK_DISABLE=1 "$PY" "$HOOK" 2>/dev/null)"
  if [ -z "$killed_out" ]; then
    pass "kill switch: AIRLOCK_DISABLE=1 makes the hook a complete no-op"
  else
    fail "kill switch: expected no output, got: $killed_out"
  fi

  rm -rf "$TMPHOME"
  trap - EXIT
fi

# ---------------------------------------------------------------------------
head_ "Mode and config"

MODE="$(cd "$LIVE" && "$PY" -c 'from airlock import mode; print(mode.resolve_mode())' 2>/dev/null)"
if [ -n "$MODE" ]; then
  pass "resolved mode: $MODE"
  if [ "$MODE" = "shadow" ]; then
    MODE_FILE="$(cd "$LIVE" && "$PY" -c 'from airlock import paths; print(paths.config_file("mode"))' 2>/dev/null)"
    printf '        shadow logs and never blocks. To arm it:  echo enforce > %s\n' "${MODE_FILE:-<config dir>/mode}"
  fi
else
  fail "could not resolve the mode -- is $LIVE importable?"
fi

if (cd "$LIVE" && "$PY" -c '
from airlock import paths
c, s = paths.config_dir(), paths.state_dir()
print("config:", c)
print("state: ", s)
' 2>/dev/null | sed "s/^/        /"); then
  pass "config and state directories resolve"
else
  fail "config/state directories do not resolve"
fi

# ---------------------------------------------------------------------------
head_ "The API key"
# Presence and PATHS only. The value is never read into a variable, never
# printed. Which file is in use matters when the answer is surprising -- the
# resolution order (and why a pointer file can be refused) is documented in
# airlock/keyfile.py's module docstring, which is the only place it lives.
if (cd "$LIVE" && "$PY" -c '
import os
from airlock import keyfile
print("in use:  %s" % keyfile.key_file())
pointer = keyfile.pointer_file_path()
if pointer and os.path.exists(pointer):
    target = keyfile.pointer_target()
    print("pointer: %s -> %s" % (pointer, target or "(not honoured)"))
else:
    print("pointer: %s (absent; the default path is in use)" % pointer)
for note in keyfile.pointer_diagnostics():
    print("note:    %s" % note)
' 2>/dev/null | sed "s/^/        /"); then
  :
else
  fail "could not resolve the key-file path"
fi
if (cd "$LIVE" && "$PY" -c '
import sys
from airlock import keyfile
sys.exit(0 if keyfile.get_api_key() else 1)
' 2>/dev/null); then
  pass "an API key is loadable (its value was not printed, and never is)"
else
  skip "no API key found; every guard fails open and judges nothing"
fi

# ---------------------------------------------------------------------------
head_ "Daemon"
SOCK="$(cd "$LIVE" && "$PY" -c 'from airlock import paths; print(paths.runtime_socket())' 2>/dev/null)"
if [ -z "$SOCK" ]; then
  fail "could not resolve the daemon socket path"
elif [ ! -S "$SOCK" ]; then
  skip "no socket at $SOCK (daemon not installed, or systemd is off -- the client falls back to direct HTTPS)"
else
  if (cd "$LIVE" && "$PY" - <<'PY' 2>/dev/null
import json, socket, sys
from airlock import paths
s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
s.settimeout(3)
s.connect(paths.runtime_socket())
s.sendall(b'{"op": "ping"}\n')
line = s.makefile("rb").readline()
s.close()
sys.exit(0 if json.loads(line).get("pong") else 1)
PY
  ); then
    pass "daemon ping: socket $SOCK answered {\"pong\": true}"
  else
    fail "daemon socket $SOCK exists but did not answer a ping"
  fi
fi

# ---------------------------------------------------------------------------
head_ "Health check"
if [ ! -f "$LIVE/airlock/health.py" ]; then
  skip "no health module in the live copy"
else
  HEALTH_JSON="$(cd "$LIVE" && timeout 20 "$PY" -m airlock.health 2>/dev/null)"
  HEALTH_RC=$?
  if [ -z "$HEALTH_JSON" ]; then
    fail "health check produced no output (exit $HEALTH_RC)"
  else
    STATUS="$(printf '%s' "$HEALTH_JSON" | "$PY" -c 'import json,sys;print(json.load(sys.stdin).get("status","?"))' 2>/dev/null)"
    case "$STATUS" in
      healthy) pass "health: healthy (exit $HEALTH_RC)" ;;
      degraded) pass "health: degraded (exit $HEALTH_RC) -- it ran and reported honestly" ;;
      *) skip "health: $STATUS (exit $HEALTH_RC); usually means no daemon and/or no key" ;;
    esac
    printf '        %s\n' "$(printf '%s' "$HEALTH_JSON" | cut -c1-200)"
  fi
fi

# ---------------------------------------------------------------------------
head_ "Filesearch"
DB="$HOME/.cache/plocate/home.db"
if ! command -v plocate >/dev/null 2>&1; then
  skip "plocate not installed"
elif [ ! -f "$DB" ]; then
  skip "no index at $DB (run filesearch/install.sh)"
else
  HITS="$(plocate -d "$DB" -i -c 'airlock' 2>/dev/null)"
  if [ -n "$HITS" ]; then
    pass "plocate query answered from $DB ($HITS match(es) for 'airlock')"
  else
    # Zero matches is still a working index -- prove it with a pattern that
    # must exist, the index file's own directory.
    if plocate -d "$DB" -i -c 'plocate' >/dev/null 2>&1; then
      pass "plocate query answered from $DB (0 matches for 'airlock'; index may predate this install)"
    else
      fail "plocate could not query $DB"
    fi
  fi
fi

# ---------------------------------------------------------------------------
head_ "Timers"
if ! command -v systemctl >/dev/null 2>&1 || ! systemctl --user show-environment >/dev/null 2>&1; then
  skip "no systemd user session; nothing is scheduled (see install/install.sh --help)"
else
  for unit in airlock-daemon.service airlock-health.timer airlock-tune.timer airlock-filesearch.timer; do
    if systemctl --user list-unit-files "$unit" 2>/dev/null | grep -q "$unit"; then
      state="$(systemctl --user is-active "$unit" 2>/dev/null)"
      enabled="$(systemctl --user is-enabled "$unit" 2>/dev/null)"
      case "$state" in
        active|activating) pass "$unit: $state, $enabled" ;;
        *) fail "$unit installed but $state ($enabled)" ;;
      esac
    else
      skip "$unit not installed"
    fi
  done
fi

# ---------------------------------------------------------------------------
head_ "Optional components"
for pair in \
  "browser:${AIRLOCK_BROWSER_DIR:-$HOME/code/jev-ultrafast}" \
  "review:${AIRLOCK_REVIEW_DIR:-$HOME/code/jev-review}"
do
  name="${pair%%:*}"; dir="${pair#*:}"
  if [ -d "$dir/.git" ]; then
    pass "$name: clone at $dir ($(git -C "$dir" rev-parse --short HEAD 2>/dev/null))"
  else
    skip "$name: not installed"
  fi
done
if [ -f "$LIVE/shim/shim.py" ]; then
  if "$PY" -c "import ast,sys; ast.parse(open('$LIVE/shim/shim.py').read())" 2>/dev/null; then
    pass "shim: shim.py parses (stdlib-only; nothing to install)"
  else
    fail "shim: shim.py does not parse"
  fi
else
  skip "shim: not present in the live copy"
fi

if [ -x "$HOME/bin/claude-auto-update" ]; then
  if systemctl --user list-unit-files claude-auto-update.timer >/dev/null 2>&1 \
     && systemctl --user list-unit-files claude-auto-update.timer 2>/dev/null | grep -q claude-auto-update.timer; then
    state="$(systemctl --user is-active claude-auto-update.timer 2>/dev/null)"
    pass "claude-update: $HOME/bin/claude-auto-update, timer $state"
  else
    pass "claude-update: $HOME/bin/claude-auto-update (no systemd timer; check cron)"
  fi
else
  skip "claude-update: not installed"
fi

BELAY_HOME="${AIRLOCK_BELAY_DIR:-$HOME/.local/share/jev-belay}"
if [ -x "$HOME/bin/airlock-belay-run" ] && [ -e "$BELAY_HOME/current" ]; then
  pass "belay: wrapper at $HOME/bin/airlock-belay-run, current -> $(readlink -f "$BELAY_HOME/current" 2>/dev/null)"
else
  skip "belay: not installed"
fi

if command -v claude >/dev/null 2>&1 && claude plugin list 2>/dev/null | grep -q 'fast-jev-compaction'; then
  pass "compaction: fast-jev-compaction plugin installed (remember: unredacted, ~25k tokens/request)"
else
  skip "compaction: not installed"
fi

# ---------------------------------------------------------------------------
printf '\n%d passed, %d failed, %d skipped\n' "$PASSED" "$FAILED" "$SKIPPED"
[ "$FAILED" -eq 0 ] || exit 1
exit 0
