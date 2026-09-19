#!/usr/bin/env bash
# Clone browser-use/jev-ultrafast at a pinned commit and apply our work on top
# as patches.
#
# This directory deliberately does NOT contain a copy of upstream. Upstream is
# an active project; carrying a fork of it here would mean carrying every
# future merge conflict as well. What is ours -- the headless-CDP attach, the
# Claude text-model adapters, the thinking-off/trimmed-context measurements and
# the Jev-versus-Claude benchmark -- is six `git format-patch` files under
# patches/, produced from the commit named below. Re-pinning to a newer
# upstream is then a matter of changing UPSTREAM_COMMIT and re-running.
set -uo pipefail

UPSTREAM_URL="https://github.com/browser-use/jev-ultrafast"
UPSTREAM_COMMIT="1231850"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PATCH_DIR="$SCRIPT_DIR/patches"
DEST="${AIRLOCK_BROWSER_DIR:-$HOME/code/jev-ultrafast}"

usage() {
  cat >&2 <<EOF
usage: $0 [--dest <dir>] [--no-sync]

  --dest <dir>   where to clone (default: \$AIRLOCK_BROWSER_DIR, else
                 \$HOME/code/jev-ultrafast)
  --no-sync      clone and patch, but do not run 'uv sync'

Clones $UPSTREAM_URL at $UPSTREAM_COMMIT and applies browser/patches/*.patch
onto a branch called 'claude-text-model'.
EOF
  exit 2
}

SYNC=1
while [ "$#" -gt 0 ]; do
  case "$1" in
    --dest) DEST="${2:-}"; [ -n "$DEST" ] || usage; shift 2 ;;
    --no-sync) SYNC=0; shift ;;
    -h|--help) usage ;;
    *) usage ;;
  esac
done

for tool in git; do
  command -v "$tool" >/dev/null 2>&1 || { echo "browser: '$tool' not found" >&2; exit 1; }
done

if [ -e "$DEST" ]; then
  if [ -d "$DEST/.git" ]; then
    echo "browser: $DEST already exists and is a git repo."
    echo "  Leaving it alone. Remove it, or pass --dest <somewhere-else>, to"
    echo "  install a fresh copy. Nothing here overwrites a working tree."
    exit 0
  fi
  echo "browser: $DEST exists but is not a git repo; refusing to touch it." >&2
  exit 1
fi

echo "browser: cloning $UPSTREAM_URL -> $DEST"
mkdir -p "$(dirname "$DEST")"
git clone "$UPSTREAM_URL" "$DEST" || { echo "browser: clone failed" >&2; exit 1; }

echo "browser: checking out the pinned commit $UPSTREAM_COMMIT"
git -C "$DEST" checkout -q "$UPSTREAM_COMMIT" || {
  echo "browser: commit $UPSTREAM_COMMIT not found in the clone." >&2
  echo "  Upstream may have rewritten history. Re-pin UPSTREAM_COMMIT in this" >&2
  echo "  script and regenerate the patches before trying again." >&2
  exit 1
}
git -C "$DEST" switch -c claude-text-model

echo "browser: applying $(ls -1 "$PATCH_DIR"/*.patch | wc -l) patches"
if ! git -C "$DEST" am --keep-non-patch "$PATCH_DIR"/*.patch; then
  echo >&2
  echo "browser: a patch did not apply. The clone is left mid-'git am' so you" >&2
  echo "  can look at it:  git -C $DEST am --show-current-patch=diff" >&2
  echo "  Abandon with:    git -C $DEST am --abort" >&2
  exit 1
fi
echo "browser: patches applied; branch 'claude-text-model' is at $(git -C "$DEST" rev-parse --short HEAD)"

if [ "$SYNC" = "1" ]; then
  if command -v uv >/dev/null 2>&1; then
    echo "browser: uv sync (nice'd; this box has 4 shared vCPUs)"
    ( cd "$DEST" && nice -n 10 uv sync )
  else
    echo "browser: 'uv' is not installed, so dependencies were not synced." >&2
    echo "  Install uv, then run:  (cd $DEST && uv sync)" >&2
  fi
fi

cat <<EOF

Installed at $DEST on branch claude-text-model.

Next, launch headless Chromium and point the harness at it -- see
browser/README.md. Short version:

    node "$DEST/scripts/launch_chromium.js" &      # needs NODE_PATH if
                                                   # playwright is global
    export BU_CDP_URL=http://127.0.0.1:9333
    cd "$DEST" && uv run python examples/run.py

One Chromium at a time on this box, and close it when you are done
(\`pgrep -a chrom\` should come back empty).
EOF
