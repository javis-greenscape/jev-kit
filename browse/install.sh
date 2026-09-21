#!/usr/bin/env bash
# Check that the `browse` MCP server can run here, and PRINT the block that
# registers it. Nothing is applied: ~/.claude.json is yours, and this script
# never opens it.
#
# The server drives the pinned jev-ultrafast clone that browser/install.sh
# makes, so that clone has to exist first.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
SERVER="$SCRIPT_DIR/server.py"
CLONE="${JEV_ULTRAFAST_DIR:-${AIRLOCK_BROWSER_DIR:-$HOME/code/jev-ultrafast}}"
CLONE="${CLONE/#\~/$HOME}"
PY="${PYTHON:-python3}"

usage() {
  cat >&2 <<USAGE
usage: $0

Verifies the jev-ultrafast clone (\$JEV_ULTRAFAST_DIR, else
\$AIRLOCK_BROWSER_DIR, else \$HOME/code/jev-ultrafast), runs the server through
a real MCP handshake, and prints the mcpServers block to add to ~/.claude.json.
It never edits that file.
USAGE
  exit 2
}
[ "$#" -eq 0 ] || usage

command -v "$PY" >/dev/null 2>&1 || { echo "browse: '$PY' not found" >&2; exit 1; }
[ -f "$SERVER" ] || { echo "browse: no server at $SERVER" >&2; exit 1; }

if [ ! -f "$CLONE/jev_ultrafast/agent.py" ]; then
  echo "browse: the jev-ultrafast clone was not found at $CLONE" >&2
  echo "  Run this first, then run me again:" >&2
  echo "      $(dirname "$SCRIPT_DIR")/browser/install.sh" >&2
  echo "  A clone somewhere else? Set JEV_ULTRAFAST_DIR to it." >&2
  exit 1
fi
echo "browse: clone at $CLONE ($(git -C "$CLONE" rev-parse --short HEAD 2>/dev/null || echo 'no git'))"

if [ ! -x "$CLONE/.venv/bin/python" ] && ! command -v uv >/dev/null 2>&1; then
  echo "browse: $CLONE has no .venv and 'uv' is not on PATH." >&2
  echo "  Install uv, then run 'uv sync' in the clone." >&2
  exit 1
fi

chmod +x "$SERVER" 2>/dev/null || true

# A real handshake over a real pipe: initialize, then tools/list.
HANDSHAKE="$(printf '%s\n' \
  '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"browse-install","version":"0"}}}' \
  '{"jsonrpc":"2.0","method":"notifications/initialized"}' \
  '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' \
  | "$PY" "$SERVER" 2>/dev/null)"
if printf '%s' "$HANDSHAKE" | grep -q '"name": "browse"'; then
  echo "browse: the server answered initialize and tools/list"
else
  echo "browse: the server did not answer the MCP handshake. Try it by hand:" >&2
  echo "      $PY $SERVER" >&2
  exit 1
fi

PY_ABS="$(command -v "$PY")"
cat <<NOTE

Add this under "mcpServers" in ~/.claude.json (nothing here edits that file).
Your Playwright MCP entries stay exactly as they are.

NOTE
"$PY_ABS" - "$SERVER" <<'PYEOF'
import json, sys
print(json.dumps({"mcpServers": {"browse": {
    "type": "stdio", "command": sys.argv[1], "args": [], "env": {}}}}, indent=2))
PYEOF
cat <<NOTE

If Chromium refuses to start with "no usable sandbox" (Ubuntu 23.10+ and an
unprofiled binary), browse/README.md says what that means and what the
JEV_BROWSE_NO_SANDBOX=1 opt-in costs. It is never set for you.
NOTE
