#!/usr/bin/env bash
# Pick and lock the guard's confidence thresholds with jevcal, instead of by
# hand.
#
# airlock/policy.py ships CONFIDENCE_THRESHOLD = 0.8 and MARGIN_THRESHOLD = 0.4.
# Those were chosen by judgement, not measurement. jevcal (abhixhek/jevcal,
# MIT, see docs/CREDITS.md) picks a threshold that holds a stated
# accuracy among the decisions kept, verifies it on held-out rows, and writes a
# lock file `jevcal check` can later fail CI on when the model drifts.
#
# What this script runs, and deliberately does not:
#   lint      yes, always: it needs no key and no calls
#   measure   yes, with --provider typesafe
#   compile   yes: writes eval/decisions-<set>.lock.json and an HTML report
#   check     not here; that is the CI gate, run separately
#   label     NEVER. It calls a third-party LLM (OpenAI/OpenRouter/Anthropic)
#             with our dataset.
#   optimize  NEVER, for the same reason.
#
# Tune against OBSERVED CORRECTNESS, never against reported confidence. The
# behaviour study measured a case where the mean reported confidence of an
# entirely wrong answer was 0.9744 (RINNECODER/jev-behavior-study, see
# docs/CREDITS.md). jevcal scores against the gold labels in
# eval/cases.jsonl, which is the right way round.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

if [ -f "$REPO_ROOT/install/config.env" ]; then
  set -a
  # shellcheck disable=SC1091
  . "$REPO_ROOT/install/config.env"
  set +a
fi

# The jevcal binary is machine-specific, so it comes from config, never from a
# path baked into this file.
JEVCAL="${AIRLOCK_JEVCAL_BIN:-$HOME/tools/jevcal/.venv/bin/jevcal}"
OUT_DIR="$REPO_ROOT/eval/jevcal"
LOCK_DIR="$REPO_ROOT/eval"
TARGET="${AIRLOCK_JEVCAL_TARGET:-0.99}"
MODEL="${AIRLOCK_JEVCAL_MODEL:-jev-latest}"
SETS="${AIRLOCK_JEVCAL_SETS:-tier search}"

LINT_ONLY=0
SKIP_MEASURE=0
for arg in "$@"; do
  case "$arg" in
    --lint-only) LINT_ONLY=1 ;;
    --no-measure) SKIP_MEASURE=1 ;;
    -h|--help)
      echo "usage: $0 [--lint-only] [--no-measure]" >&2
      echo "  --lint-only   export and lint; make no API calls" >&2
      echo "  --no-measure  reuse an existing predictions file" >&2
      exit 0 ;;
    *) echo "unknown argument: $arg" >&2; exit 2 ;;
  esac
done

if [ ! -x "$JEVCAL" ]; then
  echo "calibrate: no jevcal at $JEVCAL" >&2
  echo "  Install it into its own venv and set AIRLOCK_JEVCAL_BIN in" >&2
  echo "  install/config.env:" >&2
  echo "      python3 -m venv ~/tools/jevcal/.venv" >&2
  echo "      ~/tools/jevcal/.venv/bin/pip install jevcal" >&2
  exit 1
fi

echo "== export"
python3 "$SCRIPT_DIR/jevcal_export.py" --model "$MODEL" --target "$TARGET" || exit 1

echo
echo "== lint (no API key, no calls)"
# Non-zero exit is expected while there are findings; the findings are the
# output, so it must not stop the run.
"$JEVCAL" lint --questions "$OUT_DIR/questions.yaml" || true

if [ "$LINT_ONLY" = "1" ]; then
  echo
  echo "--lint-only: stopping before any API call."
  exit 0
fi

if [ -z "${TYPESAFE_API_KEY:-}" ]; then
  echo
  echo "calibrate: TYPESAFE_API_KEY is not set, so measure cannot run." >&2
  echo "  Load it into this shell first:" >&2
  echo '      set -a; . "$AIRLOCK_KEY_FILE"; set +a' >&2
  exit 1
fi

status=0
for set_name in $SETS; do
  questions="$OUT_DIR/questions-$set_name.yaml"
  data="$OUT_DIR/data-$set_name.jsonl"
  preds="$OUT_DIR/predictions-$set_name.jsonl"
  lock="$LOCK_DIR/decisions-$set_name.lock.json"
  report="$OUT_DIR/report-$set_name.html"

  if [ ! -f "$questions" ] || [ ! -f "$data" ]; then
    echo "calibrate: missing $questions or $data, skipping '$set_name'" >&2
    continue
  fi

  echo
  echo "== $set_name: measure ($(wc -l < "$data") rows)"
  if [ "$SKIP_MEASURE" = "1" ] && [ -f "$preds" ]; then
    echo "   --no-measure: reusing $preds"
  else
    "$JEVCAL" measure \
      --questions "$questions" \
      --data "$data" \
      --provider typesafe \
      --model "$MODEL" \
      --concurrency 4 \
      --out "$preds" || { echo "calibrate: measure failed for $set_name" >&2; status=1; continue; }
  fi

  echo "== $set_name: compile (target $TARGET)"
  "$JEVCAL" compile \
    --questions "$questions" \
    --data "$data" \
    --preds "$preds" \
    --provider-name typesafe \
    --target "$TARGET" \
    --lock "$lock" \
    --report "$report" || { echo "calibrate: compile failed for $set_name" >&2; status=1; continue; }

  echo "   lock:   $lock"
  echo "   report: $report"
done

echo
echo "Locks are under $LOCK_DIR. The CI drift gate is a separate, deliberate step:"
echo "    $JEVCAL check --questions $OUT_DIR/questions-<set>.yaml \\"
echo "        --data $OUT_DIR/data-<set>.jsonl --lock $LOCK_DIR/decisions-<set>.lock.json"
echo "which exits 1 when the model has drifted away from the locked thresholds."
exit "$status"
