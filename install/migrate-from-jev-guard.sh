#!/usr/bin/env bash
# Compatibility wrapper. The project was renamed twice (jev-guard ->
# plumbline -> airlock), and one script now handles both old names:
# install/migrate-to-airlock.sh.
#
# This file stays so that a runbook, a note or a habit naming the old script
# keeps working. It is a wrapper, not a second implementation: there is
# nothing here to drift out of step with the real migration.
set -uo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
echo "install/migrate-from-jev-guard.sh is now install/migrate-to-airlock.sh; running that." >&2
exec bash "$DIR/migrate-to-airlock.sh" "$@"
