#!/usr/bin/env bash
# Install the per-user plocate index: a systemd user service that runs
# updatedb over $HOME into a private database, and an hourly timer.
#
# Why it exists: `find ~ -name ...` walks a 150 GB tree and takes minutes.
# The same question against this index answers in milliseconds. The index is
# per-user and lives under $HOME, so nothing here needs root and nothing
# touches the system-wide /var/lib/plocate database.
#
# Idempotent: re-running it overwrites the two unit files with identical
# content and re-enables an already-enabled timer.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UNIT_DIR="$HOME/.config/systemd/user"
DB="$HOME/.cache/plocate/home.db"

NO_SYSTEMD=0
RUN_NOW=1
for arg in "$@"; do
  case "$arg" in
    --no-systemd) NO_SYSTEMD=1 ;;
    --no-run) RUN_NOW=0 ;;
    *) echo "usage: $0 [--no-systemd] [--no-run]" >&2; exit 2 ;;
  esac
done

if ! command -v updatedb >/dev/null 2>&1 || ! command -v plocate >/dev/null 2>&1; then
  echo "filesearch: plocate is not installed." >&2
  echo "  Install it as a named system package, then re-run:" >&2
  echo "      sudo apt install plocate" >&2
  exit 1
fi

mkdir -p "$(dirname "$DB")"

build_index() {
  echo "filesearch: building the index (this takes tens of seconds on a large home directory)..."
  updatedb -l 0 -U "$HOME" -o "$DB" \
    --prunenames ".git node_modules .pnpm-store __pycache__ .venv" \
    --prunepaths ""
  echo "filesearch: index at $DB ($(du -h "$DB" 2>/dev/null | cut -f1))"
}

if [ "$NO_SYSTEMD" = "1" ]; then
  echo "filesearch: --no-systemd, so no timer is installed."
  echo "  The index will not refresh by itself. Rebuild it by hand, or from"
  echo "  cron, with:"
  echo "      $SCRIPT_DIR/install.sh --no-systemd"
  [ "$RUN_NOW" = "1" ] && build_index
  exit 0
fi

mkdir -p "$UNIT_DIR"
install -m 644 "$SCRIPT_DIR/airlock-filesearch.service" "$UNIT_DIR/airlock-filesearch.service"
install -m 644 "$SCRIPT_DIR/airlock-filesearch.timer" "$UNIT_DIR/airlock-filesearch.timer"
echo "filesearch: installed units into $UNIT_DIR"

: "${XDG_RUNTIME_DIR:=/run/user/$(id -u)}"
export XDG_RUNTIME_DIR

if ! systemctl --user show-environment >/dev/null 2>&1; then
  echo "filesearch: no systemd user session reachable." >&2
  echo "  The unit files are installed but nothing is scheduled. On WSL this" >&2
  echo "  usually means systemd is off -- see install/install.sh --help." >&2
  [ "$RUN_NOW" = "1" ] && build_index
  exit 1
fi

systemctl --user daemon-reload
systemctl --user enable --now airlock-filesearch.timer
echo "filesearch: timer enabled:"
systemctl --user list-timers airlock-filesearch.timer --no-pager 2>/dev/null | head -3

if [ "$RUN_NOW" = "1" ]; then
  echo "filesearch: running the first index build now..."
  systemctl --user start airlock-filesearch.service
  echo "filesearch: index at $DB ($(du -h "$DB" 2>/dev/null | cut -f1))"
fi

echo
echo "Query it with:"
echo "    plocate -d \"$DB\" -i '<pattern>'"
echo
echo "Tell the agent about it: paste filesearch/CLAUDE.md.snippet into the"
echo "machine's CLAUDE.md. An index nothing is told about gets used by nothing."
