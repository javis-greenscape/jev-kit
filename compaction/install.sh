#!/usr/bin/env bash
# Install tamaratran/fast-jev-compaction, a Claude Code plugin that compacts
# context automatically once a session passes a token threshold.
#
# READ compaction/README.md BEFORE running this. It sends far more off the
# machine than any other component in this repository: up to about 25,000
# tokens of tool inputs and tool-result text per request, UNREDACTED, to
# TypeSafe. There is no redaction pass anywhere in the plugin's source.
set -uo pipefail

MARKETPLACE="tamaratran/fast-jev-compaction"
PLUGIN_ID="fast-jev-compaction@fast-jev-compaction"
MIN_VERSION="2.1.274"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

usage() {
  cat >&2 <<EOF
usage: $0

Requires:
  - Claude Code >= $MIN_VERSION with function hooks enabled
    (env CLAUDE_CODE_ENABLE_FUNCTION_HOOKS=1 in the account's settings --
    this script checks and prints the edit; it does not write settings.json)
  - a loadable TYPESAFE_API_KEY (env, or the key file named by
    AIRLOCK_KEY_FILE)

This installs a marketplace and a plugin under whichever Claude account
CLAUDE_CONFIG_DIR (or the default ~/.claude) points at. Run it once per
account that should have compaction.
EOF
  exit 2
}
[ "${1:-}" = "-h" ] || [ "${1:-}" = "--help" ] && usage

command -v claude >/dev/null 2>&1 || { echo "compaction: 'claude' not found on PATH" >&2; exit 1; }

# --- version check -----------------------------------------------------------
CUR_VERSION="$(claude --version 2>/dev/null | awk '{print $1}')"
if [ -z "$CUR_VERSION" ]; then
  echo "compaction: could not read the Claude Code version" >&2
  exit 1
fi
ver_ge() { [ "$(printf '%s\n%s\n' "$1" "$2" | sort -V | tail -1)" = "$1" ]; }
if ! ver_ge "$CUR_VERSION" "$MIN_VERSION"; then
  echo "compaction: this Claude Code is $CUR_VERSION; fast-jev-compaction needs >= $MIN_VERSION." >&2
  echo "  Upgrade first (see claude-update/, or 'npm install -g @anthropic-ai/claude-code')." >&2
  exit 1
fi
echo "compaction: Claude Code $CUR_VERSION >= $MIN_VERSION, ok"

# --- function hooks: print the settings edit, do not make it ---------------
ACCOUNT_DIR="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
SETTINGS="$ACCOUNT_DIR/settings.json"
HOOKS_ENABLED=0
if [ -f "$SETTINGS" ] && grep -q '"CLAUDE_CODE_ENABLE_FUNCTION_HOOKS" *: *"1"' "$SETTINGS" 2>/dev/null; then
  HOOKS_ENABLED=1
fi
if [ "$HOOKS_ENABLED" = "1" ]; then
  echo "compaction: CLAUDE_CODE_ENABLE_FUNCTION_HOOKS=1 already set in $SETTINGS"
else
  cat <<EOF

compaction: function hooks are not (verifiably) enabled in $SETTINGS.
Add this to that file's "env" block yourself -- this script does not edit
settings.json:

    "env": {
      "CLAUDE_CODE_ENABLE_FUNCTION_HOOKS": "1"
    }

Then re-run this script.
EOF
  exit 1
fi

# --- the key: read into a shell variable, never echoed ---------------------
if [ -z "${TYPESAFE_API_KEY:-}" ]; then
  KEY_FILE="${AIRLOCK_KEY_FILE:-$HOME/.config/airlock/env}"
  if [ -r "$KEY_FILE" ]; then
    TYPESAFE_API_KEY="$(sed -n 's/^ *\(export \)\?TYPESAFE_API_KEY=//p' "$KEY_FILE" | head -1)"
  fi
fi
if [ -z "${TYPESAFE_API_KEY:-}" ]; then
  echo "compaction: no TYPESAFE_API_KEY in the environment or the key file." >&2
  echo "  Load it first:  set -a; . ~/.config/airlock/env 2>/dev/null; set +a" >&2
  exit 1
fi
echo "compaction: key loaded (not printed)"

# --- marketplace and plugin --------------------------------------------------
echo "compaction: claude plugin marketplace add $MARKETPLACE"
claude plugin marketplace add "$MARKETPLACE"

echo "compaction: claude plugin install $PLUGIN_ID"
claude plugin install "$PLUGIN_ID" --config "apiKey=$TYPESAFE_API_KEY"

cat <<'EOF'

Installed. Before you rely on this, re-read compaction/README.md: this
plugin sends up to ~25,000 tokens of raw tool inputs and tool-result text per
compaction request, unredacted, to TypeSafe. That was true at the time this
was vetted (docs/community-vetting.md) and nothing in this install changes
it.
EOF
