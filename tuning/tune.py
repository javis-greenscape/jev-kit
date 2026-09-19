#!/usr/bin/env python3
"""airlock unattended tuning loop -- one run.

Reads NEW rows from the shadow log, has a headless Claude judge them against
the current criteria, turns the ones Jev got wrong into new eval cases, asks
a second headless Claude for replacement criteria text, and only commits the
result on branch auto-tune (in a dedicated worktree, never the main working
tree) if a hard gate passes: the diff touches only airlock/questions.py and
eval/cases.jsonl, unit tests pass, and eval accuracy does not regress.

Designed to do nothing rather than something wrong: every failure mode is a
discard-and-log, never a partial commit. See tuning/README.md for the full
design and the install/promote steps a human runs separately.
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

HOME = Path(os.environ.get("HOME") or os.path.expanduser("~"))
STATE_DIR = Path(os.environ.get("AIRLOCK_TUNE_STATE_DIR") or (HOME / ".local" / "state" / "airlock"))
STATE_FILE = STATE_DIR / "tune_state.json"
TUNE_LOG_FILE = STATE_DIR / "tune_log.jsonl"
WORKTREE_DIR = Path(os.environ.get("AIRLOCK_TUNE_WORKTREE_DIR") or (STATE_DIR / "tune-worktree"))
SHADOW_LOG_FILE = HOME / ".local" / "state" / "airlock" / "shadow.jsonl"
CONFIG_DIR = HOME / ".config" / "airlock"
DISABLED_FILE = CONFIG_DIR / "disabled"
TUNING_DISABLED_FILE = CONFIG_DIR / "tuning-disabled"

# Machine-specific: which Claude account tree the tuning judge runs under.
# A box with a single account leaves it unset and gets ~/.claude; a box with
# several records its own value in install/config.env (see
# install/config.env.example). No account name is baked in here.
CLAUDE_CONFIG_DIR = (
    os.environ.get("AIRLOCK_TUNE_CLAUDE_CONFIG_DIR")
    or os.environ.get("PLUMBLINE_TUNE_CLAUDE_CONFIG_DIR")
    or os.environ.get("JEV_TUNE_CLAUDE_CONFIG_DIR")
    or os.environ.get("CLAUDE_CONFIG_DIR")
    or str(HOME / ".claude")
)
CLAUDE_BIN = os.environ.get("AIRLOCK_TUNE_CLAUDE_BIN") or "claude"

BASE_BRANCH = os.environ.get("AIRLOCK_TUNE_BASE_BRANCH") or "main"

# Judge and criteria-rewrite calls share the same model/effort: a much more
# expensive judge is only worth it if it's also the one rewriting criteria
# from what it found wrong. Opus at high effort is the default because a
# cheaper judge is exactly what auto-tune exists to correct for -- Sonnet at
# low/medium was cheap but also the thing most likely to rubber-stamp Jev's
# own mistakes.
AIRLOCK_TUNE_JUDGE_MODEL = os.environ.get("AIRLOCK_TUNE_JUDGE_MODEL") or "opus"
AIRLOCK_TUNE_JUDGE_EFFORT = os.environ.get("AIRLOCK_TUNE_JUDGE_EFFORT") or "high"

MIN_INTERVAL_MIN = 30
MAX_INTERVAL_MIN = 1440
DEFAULT_MIN_ROWS = 10
# Lowered from 40: the judge is now Opus-high, a much more expensive call
# per row, so each run's row cap is halved to keep run cost in check.
MAX_JUDGE_ROWS = 20
MAX_CASES = 400

TIER_QUESTION_IDS = {"task_kind"}
BASH_QUESTION_IDS = {"search_intent"}


def log(msg):
    print("[tune] %s" % msg, file=sys.stderr)


# --- state -------------------------------------------------------------


def load_state():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            pass
    return {"interval_min": MIN_INTERVAL_MIN, "last_run_epoch": 0, "last_processed_ts": ""}


def save_state(state):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2))
    tmp.replace(STATE_FILE)


def append_tune_log(entry):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with open(TUNE_LOG_FILE, "a") as f:
        f.write(json.dumps(entry, default=str) + "\n")


# --- shadow rows ---------------------------------------------------------


def read_shadow_rows():
    rows = []
    if not SHADOW_LOG_FILE.exists():
        return rows
    with open(SHADOW_LOG_FILE, "r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except Exception:
                continue
    return rows


def new_rows_since(rows, last_processed_ts):
    if not last_processed_ts:
        return rows
    return [r for r in rows if str(r.get("ts", "")) > last_processed_ts]


# --- git worktree management ---------------------------------------------


def run(cmd, cwd=None, check=True, input_text=None, timeout=None):
    proc = subprocess.run(
        cmd, cwd=cwd, check=False, capture_output=True, text=True,
        input=input_text, timeout=timeout,
    )
    if check and proc.returncode != 0:
        raise RuntimeError(
            "command failed (%s): %s\nstdout:\n%s\nstderr:\n%s"
            % (proc.returncode, " ".join(cmd), proc.stdout, proc.stderr)
        )
    return proc


def resolve_main_repo(script_dir):
    """The git checkout the tuning loop operates on, or None.

    Delegates to `airlock.repo_path.resolve_repo`, the one place this order is
    written down: AIRLOCK_TUNE_REPO env -> the repo.path pointer the installer
    records -> script_dir's own parent if THAT is a git checkout -> None. A
    deployed release (script_dir under $AIRLOCK_HOME/current/tuning, no
    .git anywhere in it) resolves via the pointer or not at all; a plain
    checkout resolves via its own parent with no pointer needed.
    """
    repo_root = Path(script_dir).resolve().parent
    sys.path.insert(0, str(repo_root))
    from airlock import repo_path as _repo_path  # local import: sys.path just set up above

    resolved = _repo_path.resolve_repo(script_dir)
    return Path(resolved).resolve() if resolved else None


def _worktree_belongs_to(worktree_dir, repo_dir):
    """True iff `worktree_dir` is a linked worktree of `repo_dir` (its own
    main checkout is `repo_dir`, per `git worktree list` run from inside it).
    False (never raises) for anything else, including a directory that isn't
    a git worktree at all."""
    if not (worktree_dir / ".git").exists():
        return False
    proc = run(["git", "-C", str(worktree_dir), "worktree", "list", "--porcelain"], check=False)
    if proc.returncode != 0:
        return False
    for line in proc.stdout.splitlines():
        if line.startswith("worktree "):
            try:
                return Path(line[len("worktree "):].strip()).resolve() == Path(repo_dir).resolve()
            except Exception:
                return False
    return False


def retire_foreign_worktree(worktree_dir):
    """Move a tune-worktree that belongs to a DIFFERENT repository aside
    rather than reusing or deleting it -- it may hold auto-tune commits worth
    keeping around for inspection. tune_state.json (the interval backoff)
    lives in STATE_DIR, not inside the worktree, so it is untouched."""
    retired = worktree_dir.parent / ("%s.retired-%d" % (worktree_dir.name, int(time.time())))
    worktree_dir.rename(retired)
    log("existing tune-worktree at %s belongs to a different repository; moved it aside to %s" % (worktree_dir, retired))
    return retired


def ensure_tune_worktree(main_repo):
    if WORKTREE_DIR.exists():
        if not _worktree_belongs_to(WORKTREE_DIR, main_repo):
            retire_foreign_worktree(WORKTREE_DIR)
        else:
            # Rebase auto-tune onto the base branch. On conflict, abort and report.
            run(["git", "-C", str(WORKTREE_DIR), "fetch", str(main_repo), "%s:refs/heads/tune-main-ref" % BASE_BRANCH])
            proc = run(
                ["git", "-C", str(WORKTREE_DIR), "rebase", "refs/heads/tune-main-ref"],
                check=False,
            )
            if proc.returncode != 0:
                run(["git", "-C", str(WORKTREE_DIR), "rebase", "--abort"], check=False)
                raise RuntimeError("rebase of auto-tune onto %s conflicted; aborted:\n%s" % (BASE_BRANCH, proc.stdout))
            return
    WORKTREE_DIR.parent.mkdir(parents=True, exist_ok=True)
    # Branch may already exist (e.g. worktree dir was removed manually).
    proc = run(["git", "-C", str(main_repo), "branch", "--list", "auto-tune"], check=False)
    if proc.stdout.strip():
        run(["git", "worktree", "add", str(WORKTREE_DIR), "auto-tune"], cwd=str(main_repo))
    else:
        run(["git", "worktree", "add", "-b", "auto-tune", str(WORKTREE_DIR), BASE_BRANCH], cwd=str(main_repo))


def worktree_diff_files():
    proc = run(["git", "-C", str(WORKTREE_DIR), "diff", "--name-only"], check=False)
    return [line.strip() for line in proc.stdout.splitlines() if line.strip()]


def worktree_discard_changes():
    run(["git", "-C", str(WORKTREE_DIR), "checkout", "--"] + ["."], check=False)
    run(["git", "-C", str(WORKTREE_DIR), "clean", "-fd", "eval/", "airlock/"], check=False)


# --- redaction sanity (rows are already redacted at log time; belt+braces) -


_SECRET_LIKE = ("apikey_", "sk-")


def _looks_redacted(obj):
    text = json.dumps(obj, default=str)
    return not any(marker in text for marker in _SECRET_LIKE)


# --- headless Claude judge calls ------------------------------------------


def _run_claude(prompt, effort, model="sonnet"):
    env = dict(os.environ)
    env["CLAUDE_CONFIG_DIR"] = CLAUDE_CONFIG_DIR
    cmd = [
        CLAUDE_BIN, "-p", prompt,
        "--model", model,
        "--effort", effort,
        "--safe-mode",
        "--tools", "",
    ]
    proc = subprocess.run(
        cmd, env=env, stdin=subprocess.DEVNULL,
        capture_output=True, text=True, timeout=300,
    )
    if proc.returncode != 0:
        raise RuntimeError("claude call failed (%s): %s" % (proc.returncode, proc.stderr[:2000]))
    return proc.stdout


def _extract_json(text):
    text = text.strip()
    start = None
    for i, ch in enumerate(text):
        if ch in "{[":
            start = i
            break
    if start is None:
        raise ValueError("no JSON object/array found in claude output")
    # Use raw_decode so trailing prose/fences after the JSON value (or a
    # closing ``` fence) don't turn a valid answer into a parse error.
    decoder = json.JSONDecoder()
    value, _ = decoder.raw_decode(text, start)
    return value


RUBRIC_SUMMARY = """
Guard rubrics (both guards judge a single narrow question about one tool call):

tier_guard.task_kind options: lookup, mechanical_edit, scoped_implementation,
judgement, hard_problem, unclear. lookup = a fact already on disk, no edits.
mechanical_edit = a rename/format/boilerplate/one-line change with zero
ambiguity. scoped_implementation = a specified feature/test/refactor/bugfix
whose approach is already decided. judgement = ambiguous requirements, an
architecture decision, an unknown root cause, or reviewing another agent's
work. hard_problem = the prompt explicitly names a prior failed attempt at
this exact task. unclear = not enough information to tell.

tool_choice_guard.search_intent options: filename_search, code_structure_search,
literal_text_search, not_a_search, unclear. filename_search = matches file
NAMES, not contents. code_structure_search = the pattern is shaped like code
syntax (a definition, a call, an import, a type) -- a DEFINITION search is
always code_structure_search even via grep. literal_text_search = an exact
error message, config key/value, log line, or filtering piped stdout.
not_a_search = find -delete, du, tree with no pattern, grep -c just counting.
unclear = not enough information.

Scope (disk_wide/single_repo/single_dir/stdin/unknown) is decided in CODE, not
by the model -- every tool_choice_guard row below already carries its own
code-computed "scope" field as ground truth. Treat it as given and judge
search_intent only; do not re-derive or second-guess scope, and it plays no
part in whether Jev's search_intent answer was correct. It only affects the
would_deny policy downstream, which is out of scope for this judgement.
""".strip()


def build_judge_prompt(rows):
    rows_json = json.dumps(rows, indent=2, default=str)
    return (
        "You are auditing a shadow-mode AI guard's judgements for correctness. "
        "Each row below is one already-redacted log entry: a guard name, the "
        "input it judged, and what it (Jev) answered.\n\n"
        + RUBRIC_SUMMARY
        + "\n\nRows (JSON array):\n" + rows_json + "\n\n"
        "For EACH row, decide the correct label per the rubric above, and "
        "whether Jev's answer for that row was right. Respond with ONLY a "
        "JSON array, no prose before or after, one object per row in the same "
        "order, each shaped exactly like:\n"
        '{"id": "<row id or index>", "guard": "<guard>", '
        '"correct_label": "<option name>", "jev_correct": true|false, '
        '"reason": "<one line>"}\n'
    )


def build_criteria_prompt(current_questions_source, wrong_cases, docs_summary):
    wrong_json = json.dumps(wrong_cases, indent=2, default=str)
    return (
        "You are improving the CRITERIA TEXT of a TypeSafe System One Choice "
        "question, based on cases the model got wrong. Do not change option "
        "names, question ids, or add/remove options -- only sharpen the "
        "wording of the criteria for options that are being confused.\n\n"
        + docs_summary + "\n\n"
        "Current airlock/questions.py source (for context only):\n"
        "```python\n" + current_questions_source + "\n```\n\n"
        "Cases the model got wrong (each has the command/prompt, the wrong "
        "answer, and the correct label):\n" + wrong_json + "\n\n"
        "Respond with ONLY a JSON object, no prose, shaped exactly like:\n"
        '{"task_kind": {"<option_name>": "<new criteria text>", ...}, '
        '"search_intent": {"<option_name>": "<new criteria text>", ...}}\n'
        "Include only the options you are actually changing. Criteria text "
        "may be a plain string (it will replace the option's current value "
        "verbatim, whatever shape it was before)."
    )


DOCS_GUIDANCE_SUMMARY = (
    "TypeSafe guidance: give each Choice option a concrete description; when "
    "two options are confused, describe each with what it covers, what "
    "belongs to a neighbouring option instead, and 1-3 concrete examples, "
    "using the same field names across options so they can be compared "
    "directly. Ask the narrow fuzzy question only; anything decidable from "
    "code (like scope) should not be re-litigated in the criteria text."
)


# --- applying a criteria replacement (AST-based, criteria text only) -------


def apply_criteria_replacement(source, replacements):
    """Replace only the criteria VALUE nodes for named options inside
    tier_questions()/bash_questions(), leaving every other structure, key,
    option name, and formatting untouched. Returns the new source, or raises
    if a referenced question id / option name cannot be found (fail loud
    rather than silently no-op).

    Implemented as a source-level splice using each target node's own
    (lineno, col_offset, end_lineno, end_col_offset), not ast.unparse of the
    whole tree -- ast.unparse is semantically correct but reformats every
    line, which would turn a hand-written, human-reviewable diff into a
    wall of noise. Splicing only the changed spans keeps the diff to exactly
    the criteria text that changed."""
    import ast

    tree = ast.parse(source)
    question_fn_names = {"task_kind": "tier_questions", "search_intent": "bash_questions"}

    def find_dict_value_for_key(dict_node, key_name):
        for k, v in zip(dict_node.keys, dict_node.values):
            if isinstance(k, ast.Constant) and k.value == key_name:
                return v
        return None

    # Collect (node_to_replace, new_text) targets first; only splice the
    # source afterwards, in reverse position order, so earlier edits never
    # invalidate the offsets of edits still to come.
    targets = []
    applied = set()
    skipped = []

    for question_id, option_map in replacements.items():
        fn_name = question_fn_names.get(question_id)
        if not fn_name:
            skipped.append((question_id, None, "unknown question id"))
            continue
        fn_node = None
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == fn_name:
                fn_node = node
                break
        if fn_node is None:
            raise ValueError("could not find function %s in questions.py" % fn_name)

        # Find the Return statement's dict, then [question_id]["criteria"].
        top_dict = None
        for node in ast.walk(fn_node):
            if isinstance(node, ast.Return) and isinstance(node.value, ast.Dict):
                top_dict = node.value
                break
        if top_dict is None:
            raise ValueError("could not find return dict in %s" % fn_name)

        question_spec = find_dict_value_for_key(top_dict, question_id)
        if question_spec is None or not isinstance(question_spec, ast.Dict):
            raise ValueError("could not find question %r in %s" % (question_id, fn_name))

        criteria_dict = find_dict_value_for_key(question_spec, "criteria")
        if criteria_dict is None or not isinstance(criteria_dict, ast.Dict):
            raise ValueError("could not find criteria dict for %r" % question_id)

        if not isinstance(option_map, dict):
            raise ValueError("replacement for %r is not an object of options" % question_id)

        for option_name, new_text in option_map.items():
            # Be lenient about a judge model wandering off-schema (an unknown
            # option name, or a value that isn't plain text): skip and note
            # it rather than discarding an otherwise-good run over one bad
            # key. Skipped edits still respect "criteria text only" -- they
            # just don't happen.
            if not isinstance(new_text, str):
                skipped.append((question_id, option_name, "value is not a string"))
                continue
            found = False
            for i, k in enumerate(criteria_dict.keys):
                if isinstance(k, ast.Constant) and k.value == option_name:
                    node = criteria_dict.values[i]
                    if not hasattr(node, "end_lineno"):
                        skipped.append((question_id, option_name, "AST node has no end position"))
                        found = True  # known option, just can't splice it
                        break
                    targets.append((node, new_text))
                    found = True
                    applied.add((question_id, option_name))
                    break
            if not found:
                skipped.append((question_id, option_name, "unknown option name"))

    lines = source.splitlines(keepends=True)

    def offset(lineno, col):
        """Absolute character offset into `source` for a 1-indexed line and
        0-indexed column, as ast gives them."""
        return sum(len(l) for l in lines[: lineno - 1]) + col

    # Replace furthest-in-the-file first so earlier offsets stay valid.
    targets.sort(key=lambda t: (t[0].lineno, t[0].col_offset), reverse=True)

    new_source = source
    for node, new_text in targets:
        start = offset(node.lineno, node.col_offset)
        end = offset(node.end_lineno, node.end_col_offset)
        replacement_literal = repr(new_text)
        new_source = new_source[:start] + replacement_literal + new_source[end:]

    # Confirm the spliced result is still valid Python before handing it back.
    ast.parse(new_source)
    return new_source, applied, skipped


# --- case dedupe / capping -------------------------------------------------


def normalize_key(case):
    payload = case.get("payload") or {}
    ti = payload.get("tool_input") or {}
    if case.get("guard") == "tier_guard":
        raw = (ti.get("subagent_type", ""), ti.get("prompt", ""))
    else:
        raw = (ti.get("command", ""),)
    return hashlib.sha256(json.dumps(raw, default=str).encode("utf-8")).hexdigest()


def load_cases(path):
    cases = []
    if not path.exists():
        return cases
    with open(path, "r") as f:
        for line in f:
            line = line.strip()
            if line:
                cases.append(json.loads(line))
    return cases


def write_cases(path, cases):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        for c in cases:
            f.write(json.dumps(c, default=str) + "\n")


def merge_new_cases(existing_cases, new_cases):
    seen = {normalize_key(c) for c in existing_cases}
    merged = list(existing_cases)
    for c in new_cases:
        key = normalize_key(c)
        if key in seen:
            continue
        seen.add(key)
        merged.append(c)

    if len(merged) > MAX_CASES:
        # Drop oldest shadow cases first; never drop seed cases.
        seeds = [c for c in merged if c.get("source") == "seed"]
        shadows = [c for c in merged if c.get("source") != "seed"]
        keep_shadow_count = max(0, MAX_CASES - len(seeds))
        shadows = shadows[-keep_shadow_count:] if keep_shadow_count else []
        merged = seeds + shadows
    return merged


# --- eval invocation (real Jev calls) --------------------------------------


def run_eval_in_worktree(cases_path=None):
    """Run airlock.eval inside the worktree's own venv-less interpreter,
    with the worktree on sys.path first, and return the parsed summary."""
    cmd = [sys.executable, "-m", "airlock.eval", "--json"]
    if cases_path:
        cmd += ["--cases", str(cases_path)]
    env = dict(os.environ)
    env["PYTHONPATH"] = str(WORKTREE_DIR) + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.run(cmd, cwd=str(WORKTREE_DIR), env=env, capture_output=True, text=True, timeout=1800)
    if proc.returncode != 0:
        raise RuntimeError("airlock.eval failed: %s" % proc.stderr[:2000])
    result_file = WORKTREE_DIR / "eval" / "last_result.json"
    with open(result_file) as f:
        data = json.load(f)
    return data["summary"]


def overall_accuracy(summary):
    total_correct = 0
    total_judged = 0
    total_false_deny = 0
    for s in summary.values():
        total_judged += s["judged"]
        total_correct += round(s["accuracy"] * s["judged"])
        total_false_deny += s["false_deny"]
    accuracy = (total_correct / total_judged) if total_judged else 0.0
    return accuracy, total_false_deny


# --- main run --------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="bypass the interval backoff check")
    parser.add_argument("--min-rows", type=int, default=DEFAULT_MIN_ROWS)
    args = parser.parse_args()

    run_start = time.time()
    state = load_state()

    if DISABLED_FILE.exists() or TUNING_DISABLED_FILE.exists():
        log("disabled via %s or %s; exiting" % (DISABLED_FILE, TUNING_DISABLED_FILE))
        return 0

    now_epoch = int(time.time())
    last_run_epoch = int(state.get("last_run_epoch", 0) or 0)
    interval_min = int(state.get("interval_min", MIN_INTERVAL_MIN) or MIN_INTERVAL_MIN)

    if not args.force:
        elapsed_min = (now_epoch - last_run_epoch) / 60.0
        if elapsed_min < interval_min:
            log("interval not elapsed (%.1f/%d min); exiting" % (elapsed_min, interval_min))
            return 0

    main_repo = resolve_main_repo(Path(__file__).resolve().parent)
    if main_repo is None:
        log("no repository resolved (no AIRLOCK_TUNE_REPO, no repo.path pointer, and this "
            "checkout has no .git); tuning is optional, exiting without touching state")
        return 0

    all_rows = read_shadow_rows()
    new_rows = new_rows_since(all_rows, state.get("last_processed_ts", ""))

    if len(new_rows) < args.min_rows:
        log("only %d new shadow rows (< %d); exiting without touching interval" % (len(new_rows), args.min_rows))
        return 0

    # newest first, capped
    new_rows_sorted = sorted(new_rows, key=lambda r: str(r.get("ts", "")), reverse=True)
    judge_rows = new_rows_sorted[:MAX_JUDGE_ROWS]

    for r in judge_rows:
        if not _looks_redacted(r):
            log("row failed redaction sanity check; refusing to send to judge")
            _finish(state, interval_min, committed=False, new_rows=len(new_rows), judged=0, wrong=0,
                    error_rate=0.0, accuracy_before=None, accuracy_after=None, tokens_jev=0,
                    wall_s=time.time() - run_start, reason="redaction sanity check failed")
            return 0

    try:
        ensure_tune_worktree(main_repo)
    except Exception as exc:
        log("worktree setup failed: %s" % exc)
        _finish(state, interval_min, committed=False, new_rows=len(new_rows), judged=0, wrong=0,
                error_rate=0.0, accuracy_before=None, accuracy_after=None, tokens_jev=0,
                wall_s=time.time() - run_start, reason="worktree setup failed: %s" % exc)
        return 0

    # Advance the "new rows" cursor now, even if this run doesn't end up
    # committing anything -- rows are judged once; a discard just means no
    # change was warranted, not that we should re-judge them forever.
    newest_ts = new_rows_sorted[0].get("ts", "") if new_rows_sorted else state.get("last_processed_ts", "")

    # --- 3. label with a headless Claude judge -----------------------------
    try:
        judge_prompt = build_judge_prompt(judge_rows)
        judge_output = _run_claude(judge_prompt, effort=AIRLOCK_TUNE_JUDGE_EFFORT, model=AIRLOCK_TUNE_JUDGE_MODEL)
        judgements = _extract_json(judge_output)
        if not isinstance(judgements, list):
            raise ValueError("judge output is not a JSON array")
    except Exception as exc:
        log("judge call failed or returned invalid JSON: %s" % exc)
        state["last_processed_ts"] = newest_ts
        _finish(state, interval_min, committed=False, new_rows=len(new_rows), judged=0, wrong=0,
                error_rate=0.0, accuracy_before=None, accuracy_after=None, tokens_jev=0,
                wall_s=time.time() - run_start, reason="judge call invalid: %s" % exc)
        return 0

    wrong = []
    for row, verdict in zip(judge_rows, judgements):
        if not isinstance(verdict, dict):
            continue
        if verdict.get("jev_correct") is False:
            wrong.append({"row": row, "verdict": verdict})

    judged_count = len(judge_rows)
    wrong_count = len(wrong)
    error_rate = (wrong_count / judged_count) if judged_count else 0.0

    if not wrong:
        log("judge found no wrong rows; nothing to tune")
        state["last_processed_ts"] = newest_ts
        _finish(state, interval_min, committed=False, new_rows=len(new_rows), judged=judged_count, wrong=0,
                error_rate=error_rate, accuracy_before=None, accuracy_after=None, tokens_jev=0,
                wall_s=time.time() - run_start, reason="no wrong rows")
        return 0

    # --- 4. turn wrong rows into new eval cases -----------------------------
    cases_path = WORKTREE_DIR / "eval" / "cases.jsonl"
    existing_cases = load_cases(cases_path)
    existing_ids = {c.get("id") for c in existing_cases}

    new_cases = []
    for item in wrong:
        row = item["row"]
        verdict = item["verdict"]
        guard = row.get("guard")
        if guard == "tier_guard":
            expected = {"task_kind": verdict.get("correct_label"), "would_deny": bool(row.get("would_deny"))}
        else:
            expected = {"search_intent": verdict.get("correct_label"), "would_deny": bool(row.get("would_deny"))}
        cid = "shadow-%s" % hashlib.sha256(json.dumps(row, default=str).encode("utf-8")).hexdigest()[:16]
        if cid in existing_ids:
            continue
        new_cases.append({
            "id": cid,
            "guard": guard,
            "payload": {
                "session_id": row.get("session_id"),
                "cwd": row.get("cwd"),
                "tool_name": row.get("tool_name"),
                "tool_input": row.get("input_summary") or {},
            },
            "expected": expected,
            "source": "shadow",
            "note": verdict.get("reason", ""),
        })

    if not new_cases:
        log("all wrong rows already have cases; nothing new to add")
        state["last_processed_ts"] = newest_ts
        _finish(state, interval_min, committed=False, new_rows=len(new_rows), judged=judged_count, wrong=wrong_count,
                error_rate=error_rate, accuracy_before=None, accuracy_after=None, tokens_jev=0,
                wall_s=time.time() - run_start, reason="no new cases after dedupe")
        return 0

    merged_cases = merge_new_cases(existing_cases, new_cases)
    baseline_ids = {c.get("id") for c in existing_cases}

    # --- baseline eval, BEFORE any edits, on the full (existing + new) set --
    write_cases(cases_path, merged_cases)
    tokens_jev = 0
    try:
        before_summary = run_eval_in_worktree()
        acc_before, false_deny_before = overall_accuracy(before_summary)
        tokens_jev += sum(s.get("total_tokens", 0) for s in before_summary.values())
    except Exception as exc:
        log("baseline eval failed: %s" % exc)
        worktree_discard_changes()
        state["last_processed_ts"] = newest_ts
        _finish(state, interval_min, committed=False, new_rows=len(new_rows), judged=judged_count, wrong=wrong_count,
                error_rate=error_rate, accuracy_before=None, accuracy_after=None, tokens_jev=tokens_jev,
                wall_s=time.time() - run_start, reason="baseline eval failed: %s" % exc)
        return 0

    # --- 5. ask for replacement criteria text ------------------------------
    questions_path = WORKTREE_DIR / "airlock" / "questions.py"
    try:
        current_source = questions_path.read_text()
        criteria_prompt = build_criteria_prompt(current_source, [w["row"] for w in wrong] , DOCS_GUIDANCE_SUMMARY)
        criteria_output = _run_claude(criteria_prompt, effort=AIRLOCK_TUNE_JUDGE_EFFORT, model=AIRLOCK_TUNE_JUDGE_MODEL)
        replacement = _extract_json(criteria_output)
        if not isinstance(replacement, dict):
            raise ValueError("criteria output is not a JSON object")
        new_source, applied, skipped = apply_criteria_replacement(current_source, replacement)
        if skipped:
            log("criteria rewrite skipped %d off-schema entr(y/ies): %s" % (len(skipped), skipped))
        if applied:
            questions_path.write_text(new_source)
        else:
            log("criteria rewrite applied nothing (all entries skipped); no questions.py change")
    except Exception as exc:
        log("criteria rewrite failed or invalid: %s" % exc)
        worktree_discard_changes()
        state["last_processed_ts"] = newest_ts
        _finish(state, interval_min, committed=False, new_rows=len(new_rows), judged=judged_count, wrong=wrong_count,
                error_rate=error_rate, accuracy_before=acc_before, accuracy_after=None, tokens_jev=tokens_jev,
                wall_s=time.time() - run_start, reason="criteria rewrite failed: %s" % exc)
        return 0

    # --- 6. gate ------------------------------------------------------------
    changed_files = worktree_diff_files()
    allowed = {"airlock/questions.py", "eval/cases.jsonl"}
    if not set(changed_files) <= allowed:
        log("gate failed: unexpected files changed: %s" % changed_files)
        worktree_discard_changes()
        state["last_processed_ts"] = newest_ts
        _finish(state, interval_min, committed=False, new_rows=len(new_rows), judged=judged_count, wrong=wrong_count,
                error_rate=error_rate, accuracy_before=acc_before, accuracy_after=None, tokens_jev=tokens_jev,
                wall_s=time.time() - run_start, reason="unexpected files changed: %s" % changed_files)
        return 0

    test_proc = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests"],
        cwd=str(WORKTREE_DIR), capture_output=True, text=True,
    )
    if test_proc.returncode != 0:
        log("gate failed: unit tests failed")
        worktree_discard_changes()
        state["last_processed_ts"] = newest_ts
        _finish(state, interval_min, committed=False, new_rows=len(new_rows), judged=judged_count, wrong=wrong_count,
                error_rate=error_rate, accuracy_before=acc_before, accuracy_after=None, tokens_jev=tokens_jev,
                wall_s=time.time() - run_start, reason="unit tests failed: %s" % test_proc.stdout[-2000:])
        return 0

    try:
        after_summary = run_eval_in_worktree()
        acc_after, false_deny_after = overall_accuracy(after_summary)
        tokens_jev += sum(s.get("total_tokens", 0) for s in after_summary.values())

        # Re-run restricted to the previously-existing case ids to check for
        # a regression on cases that already existed before this run.
        subset_cases = [c for c in merged_cases if c.get("id") in baseline_ids]
        subset_path = WORKTREE_DIR / "eval" / "_tune_subset_cases.jsonl"
        write_cases(subset_path, subset_cases)
        subset_summary = run_eval_in_worktree(cases_path=subset_path)
        acc_after_on_old, _ = overall_accuracy(subset_summary)
        tokens_jev += sum(s.get("total_tokens", 0) for s in subset_summary.values())
        subset_path.unlink(missing_ok=True)
    except Exception as exc:
        log("post-change eval failed: %s" % exc)
        worktree_discard_changes()
        state["last_processed_ts"] = newest_ts
        _finish(state, interval_min, committed=False, new_rows=len(new_rows), judged=judged_count, wrong=wrong_count,
                error_rate=error_rate, accuracy_before=acc_before, accuracy_after=None, tokens_jev=tokens_jev,
                wall_s=time.time() - run_start, reason="post-change eval failed: %s" % exc)
        return 0

    gate_ok = (
        acc_after_on_old >= acc_before
        and acc_after > acc_before
        and false_deny_after <= false_deny_before
    )

    if not gate_ok:
        log(
            "gate failed: acc_before=%.3f acc_after=%.3f acc_after_on_old=%.3f "
            "false_deny_before=%d false_deny_after=%d"
            % (acc_before, acc_after, acc_after_on_old, false_deny_before, false_deny_after)
        )
        worktree_discard_changes()
        state["last_processed_ts"] = newest_ts
        interval_min = min(interval_min * 2, MAX_INTERVAL_MIN) if error_rate <= 0.10 else MIN_INTERVAL_MIN
        _finish(state, interval_min, committed=False, new_rows=len(new_rows), judged=judged_count, wrong=wrong_count,
                error_rate=error_rate, accuracy_before=acc_before, accuracy_after=acc_after, tokens_jev=tokens_jev,
                wall_s=time.time() - run_start,
                reason="gate failed: before=%.3f after=%.3f after_on_old=%.3f" % (acc_before, acc_after, acc_after_on_old))
        return 0

    commit_msg = (
        "Auto-tune: %d wrong row(s) reclassified, criteria updated\n\n"
        "accuracy overall: %.3f -> %.3f (on previously-existing cases: %.3f)\n"
        "false_deny: %d -> %d\n"
        "judged=%d wrong=%d error_rate=%.1f%%\n"
    ) % (wrong_count, acc_before, acc_after, acc_after_on_old, false_deny_before, false_deny_after,
         judged_count, wrong_count, error_rate * 100)

    run(["git", "-C", str(WORKTREE_DIR), "add", "airlock/questions.py", "eval/cases.jsonl"])
    run(["git", "-C", str(WORKTREE_DIR), "commit", "-m", commit_msg])

    state["last_processed_ts"] = newest_ts
    _finish(state, MIN_INTERVAL_MIN, committed=True, new_rows=len(new_rows), judged=judged_count, wrong=wrong_count,
            error_rate=error_rate, accuracy_before=acc_before, accuracy_after=acc_after, tokens_jev=tokens_jev,
            wall_s=time.time() - run_start, reason="committed")
    return 0


def _finish(state, next_interval_min, committed, new_rows, judged, wrong, error_rate,
            accuracy_before, accuracy_after, tokens_jev, wall_s, reason):
    state["interval_min"] = next_interval_min
    state["last_run_epoch"] = int(time.time())
    save_state(state)
    entry = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "new_rows": new_rows,
        "judged": judged,
        "wrong": wrong,
        "error_rate": error_rate,
        "accuracy_before": accuracy_before,
        "accuracy_after": accuracy_after,
        "committed": committed,
        "interval_min_next": next_interval_min,
        "tokens_jev": tokens_jev,
        "wall_time_s": wall_s,
        "reason": reason,
    }
    append_tune_log(entry)
    log("run finished: %s" % json.dumps(entry, default=str))


if __name__ == "__main__":
    sys.exit(main())
