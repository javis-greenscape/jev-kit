#!/usr/bin/env bash
# Clone devagrawal09/jev-review at a pinned commit and install its
# dependencies under Node 24, WITHOUT changing this machine's default Node.
#
# The default on this box is the system Node (22), and several things are
# built against it. jev-review's package.json says `"node": ">=24"`, so it
# gets Node 24 through nvm, selected per-command. `nvm alias default` is never
# touched here: a component's version requirement is not a reason to move the
# whole machine.
set -uo pipefail

UPSTREAM_URL="https://github.com/devagrawal09/jev-review"
UPSTREAM_COMMIT="31f8960"
NODE_MAJOR="24"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST="${AIRLOCK_REVIEW_DIR:-$HOME/code/jev-review}"

usage() {
  cat >&2 <<EOF
usage: $0 [--dest <dir>] [--no-install]

  --dest <dir>   where to clone (default: \$AIRLOCK_REVIEW_DIR, else
                 \$HOME/code/jev-review)
  --no-install   clone only; skip 'npm install'

Clones $UPSTREAM_URL at $UPSTREAM_COMMIT.
Needs Node $NODE_MAJOR via nvm. The machine default Node is left alone.
EOF
  exit 2
}

INSTALL=1
while [ "$#" -gt 0 ]; do
  case "$1" in
    --dest) DEST="${2:-}"; [ -n "$DEST" ] || usage; shift 2 ;;
    --no-install) INSTALL=0; shift ;;
    -h|--help) usage ;;
    *) usage ;;
  esac
done

command -v git >/dev/null 2>&1 || { echo "review: 'git' not found" >&2; exit 1; }

# --- Node 24, without touching the default ---------------------------------
NVM_DIR="${NVM_DIR:-$HOME/.nvm}"
NODE24_BIN=""

if [ -s "$NVM_DIR/nvm.sh" ]; then
  # shellcheck disable=SC1091
  . "$NVM_DIR/nvm.sh" --no-use
  if ! nvm which "$NODE_MAJOR" >/dev/null 2>&1; then
    echo "review: installing Node $NODE_MAJOR via nvm (the default stays where it is)"
    nvm install "$NODE_MAJOR" >/dev/null 2>&1 || true
  fi
  NODE24_BIN="$(dirname "$(nvm which "$NODE_MAJOR" 2>/dev/null)" 2>/dev/null)"
fi

if [ -z "$NODE24_BIN" ] || [ ! -x "$NODE24_BIN/node" ]; then
  echo "review: could not find Node $NODE_MAJOR." >&2
  echo "  nvm is expected at \$NVM_DIR (currently $NVM_DIR). Install it, then:" >&2
  echo "      nvm install $NODE_MAJOR" >&2
  echo "  Do NOT run 'nvm alias default $NODE_MAJOR' -- this box's default Node" >&2
  echo "  is deliberately the system one, and several things are built against it." >&2
  exit 1
fi
echo "review: using Node $("$NODE24_BIN/node" --version) from $NODE24_BIN"

# --- clone -----------------------------------------------------------------
if [ -e "$DEST" ]; then
  if [ -d "$DEST/.git" ]; then
    echo "review: $DEST already exists; leaving it alone."
  else
    echo "review: $DEST exists but is not a git repo; refusing to touch it." >&2
    exit 1
  fi
else
  echo "review: cloning $UPSTREAM_URL -> $DEST"
  mkdir -p "$(dirname "$DEST")"
  git clone "$UPSTREAM_URL" "$DEST" || { echo "review: clone failed" >&2; exit 1; }
  git -C "$DEST" checkout -q "$UPSTREAM_COMMIT" || {
    echo "review: commit $UPSTREAM_COMMIT not found; upstream may have rewritten history." >&2
    exit 1
  }
fi
echo "review: at $(git -C "$DEST" rev-parse --short HEAD)"

# --- dependencies ----------------------------------------------------------
if [ "$INSTALL" = "1" ]; then
  echo "review: npm install (nice'd; 4 shared vCPUs)"
  ( cd "$DEST" && PATH="$NODE24_BIN:$PATH" nice -n 10 npm install ) || {
    echo "review: npm install failed" >&2
    exit 1
  }
fi

# --- the key ---------------------------------------------------------------
# jev-review's scripts use `node --env-file=.env`, so the key has to be in a
# .env inside the clone. It is written here from TYPESAFE_API_KEY if that is
# already loaded, mode 600, and never echoed.
if [ ! -f "$DEST/.env" ]; then
  if [ -n "${TYPESAFE_API_KEY:-}" ]; then
    umask 077
    printf 'TYPESAFE_API_KEY=%s\n' "$TYPESAFE_API_KEY" > "$DEST/.env"
    chmod 600 "$DEST/.env"
    echo "review: wrote $DEST/.env (mode 600) from TYPESAFE_API_KEY"
  else
    echo "review: no TYPESAFE_API_KEY in the environment, so no .env was written."
    echo "  Load the key and re-run, or write it yourself:"
    echo "      set -a; . \"\$AIRLOCK_KEY_FILE\"; set +a"
    echo "      umask 077; printf 'TYPESAFE_API_KEY=%s\\n' \"\$TYPESAFE_API_KEY\" > $DEST/.env"
  fi
fi

cat <<EOF

Installed at $DEST.

It reviews JavaScript and TypeScript only. Pointing it at a Python or shell
repository will screen the files it can and report nothing useful about the
rest; that is a property of the tool, not a misconfiguration.

Use it through the wrapper, which fails open:

    $SCRIPT_DIR/jev-prefilter.sh --repo <path> --gate '<review command>'
EOF
