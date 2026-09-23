#!/usr/bin/env bash
# Sync the Python environment for the vendored browser agent.
#
# Upstream browser-use/jev-ultrafast now lives in this repository, at
# vendor/jev-ultrafast, added with `git subtree` from the pin recorded in
# browser/README.md. Our own work sits on top of it as ordinary commits, so
# there is nothing left to clone and nothing left to patch: a fresh checkout
# already has the agent. What it does not have is the agent's dependencies,
# and that is all this script does now.
#
# The environment is deliberately NOT a .venv inside vendor/jev-ultrafast. A
# deployed release is an immutable `git archive` export that install/deploy.sh
# prunes, so a venv inside one would be built again on every deploy and thrown
# away again. One venv under $AIRLOCK_HOME is shared by the checkout and by
# every release, and only the project's dependencies go into it: jev_ultrafast
# itself is imported from whichever tree browse/server.py resolved.
#
# Nothing here touches ~/code/jev-ultrafast. An older hand-made clone is
# somebody's working tree and stays exactly as it is.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
PROJECT="${JEV_ULTRAFAST_DIR:-$REPO_ROOT/vendor/jev-ultrafast}"
AIRLOCK_HOME="${AIRLOCK_HOME:-$HOME/.local/share/airlock}"
VENV="${JEV_ULTRAFAST_VENV:-$AIRLOCK_HOME/jev-ultrafast-venv}"

usage() {
  cat >&2 <<EOF
usage: $0 [--venv <dir>] [--no-sync]

  --venv <dir>   the environment to sync (default: \$JEV_ULTRAFAST_VENV, else
                 \$AIRLOCK_HOME/jev-ultrafast-venv)
  --no-sync      report what would be synced and stop

Syncs the dependencies of the vendored agent at $PROJECT.
Nothing is cloned and no patch is applied: the agent is part of this
repository. See browser/README.md for how to pull a newer upstream.
EOF
  exit 2
}

SYNC=1
while [ "$#" -gt 0 ]; do
  case "$1" in
    --venv) VENV="${2:-}"; [ -n "$VENV" ] || usage; shift 2 ;;
    --no-sync) SYNC=0; shift ;;
    -h|--help) usage ;;
    *) usage ;;
  esac
done

if [ ! -f "$PROJECT/jev_ultrafast/agent.py" ]; then
  echo "browser: no vendored agent at $PROJECT" >&2
  echo "  Expected vendor/jev-ultrafast in the jev-kit checkout ($REPO_ROOT)." >&2
  echo "  Set JEV_ULTRAFAST_DIR if yours lives somewhere else." >&2
  exit 1
fi

echo "browser: vendored agent at $PROJECT"
echo "browser: environment at $VENV"

if [ "$SYNC" != "1" ]; then
  echo "browser: --no-sync, so nothing was installed."
  exit 0
fi

if ! command -v uv >/dev/null 2>&1; then
  echo "browser: 'uv' is not installed, so dependencies were not synced." >&2
  echo "  Install uv (https://docs.astral.sh/uv/), then run me again." >&2
  exit 1
fi

# --no-install-project: only the dependencies go in. Installing the project
# itself would bake one release's absolute path into the environment, and the
# next deploy would leave it pointing at a pruned directory.
echo "browser: uv sync (nice'd; this box has 4 shared vCPUs)"
mkdir -p "$(dirname "$VENV")"
if ! ( cd "$PROJECT" && UV_PROJECT_ENVIRONMENT="$VENV" nice -n 10 uv sync --no-install-project ); then
  echo "browser: uv sync failed." >&2
  exit 1
fi

# The lock the environment was built from, so install/deploy.sh can tell
# whether a newer release needs a re-sync without running uv every time.
if [ -f "$PROJECT/uv.lock" ]; then
  cp "$PROJECT/uv.lock" "$VENV/.jev-kit-uv.lock"
fi

cat <<EOF

The agent's dependencies are in $VENV.
browse/server.py finds both on its own; JEV_ULTRAFAST_DIR and
JEV_ULTRAFAST_VENV override them.

To drive it by hand, launch headless Chromium and point the harness at it:

    node "$PROJECT/scripts/launch_chromium.js" &      # needs NODE_PATH if
                                                      # playwright is global
    export BU_CDP_URL=http://127.0.0.1:9333
    PYTHONPATH="$PROJECT" "$VENV/bin/python" "$PROJECT/examples/run.py"

One Chromium at a time on this box, and close it when you are done
(\`pgrep -a chrom\` should come back empty).
EOF
