#!/usr/bin/env bash
# Install the idle-only Claude Code auto-updater: a script plus a systemd
# user timer that runs it hourly. Falls back to printing a cron line on a
# machine with no systemd user session.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN_DIR="$HOME/bin"
UNIT_DIR="$HOME/.config/systemd/user"

usage() {
  cat >&2 <<EOF
usage: $0 [--no-systemd]

Copies claude-auto-update to $BIN_DIR, and (unless --no-systemd, or no
systemd user session is reachable) installs and enables the hourly timer.

Assumes an npm-global install. The script itself auto-detects the prefix
(CLAUDE_UPDATE_PREFIX, else \$HOME/.npm-global, else 'npm config get prefix')
and refuses to touch a native (non-npm) Claude Code install -- it will say so
and exit 0 rather than doing anything.
EOF
  exit 2
}

NO_SYSTEMD=0
while [ "$#" -gt 0 ]; do
  case "$1" in
    --no-systemd) NO_SYSTEMD=1; shift ;;
    -h|--help) usage ;;
    *) echo "unknown argument: $1" >&2; usage ;;
  esac
done

: "${XDG_RUNTIME_DIR:=/run/user/$(id -u)}"
export XDG_RUNTIME_DIR

mkdir -p "$BIN_DIR"
install -m 755 "$SCRIPT_DIR/claude-auto-update" "$BIN_DIR/claude-auto-update"
echo "claude-update: installed $BIN_DIR/claude-auto-update"

SYSTEMD_OK=0
if [ "$NO_SYSTEMD" = "0" ] && command -v systemctl >/dev/null 2>&1 && systemctl --user show-environment >/dev/null 2>&1; then
  SYSTEMD_OK=1
fi

if [ "$SYSTEMD_OK" = "1" ]; then
  mkdir -p "$UNIT_DIR"
  install -m 644 "$SCRIPT_DIR/claude-auto-update.service" "$UNIT_DIR/claude-auto-update.service"
  install -m 644 "$SCRIPT_DIR/claude-auto-update.timer" "$UNIT_DIR/claude-auto-update.timer"
  systemctl --user daemon-reload
  if systemctl --user enable --now claude-auto-update.timer >/dev/null 2>&1; then
    echo "claude-update: enabled claude-auto-update.timer (hourly, up to 5 min jitter)"
  else
    echo "claude-update: could not enable the timer; try: systemctl --user enable --now claude-auto-update.timer" >&2
  fi
else
  echo "claude-update: no systemd user session (or --no-systemd given)."
  echo "  Nothing was scheduled. Add this to your crontab instead ('crontab -e'):"
  echo
  echo "      0 * * * * $BIN_DIR/claude-auto-update >/dev/null 2>&1"
  echo
fi

echo "claude-update: log is $HOME/logs/claude-update/auto.log"
