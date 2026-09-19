"""python3 -m bench.run -- real A/B benchmark: airlock enforce mode vs. no
guard at all, across 5 natural-language tasks, 3 trials per arm per task by
default, arms interleaved (jev, none, jev, none, ...) to spread cache/load
effects. One session at a time, always.

Each trial is one headless `claude -p` session, `--settings` pointing at
bench/settings/{jev,none}.json (see module docstring in hooks/airlock.py
for why the arms use AIRLOCK_DISABLE + AIRLOCK_BENCH_FORCE_MODE rather
than AIRLOCK_MODE directly -- --settings merges additively with the
account's own settings.json, which already registers the main checkout's
live shadow hook, so both arms must explicitly neutralise it).

Output: bench/results/<timestamp>.jsonl (one row per trial) and
bench/results/<timestamp>.md (a table per task).
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SETTINGS_DIR = REPO_ROOT / "bench" / "settings"
RESULTS_DIR = REPO_ROOT / "bench" / "results"
LOG_FILE = Path.home() / ".local" / "state" / "airlock" / "shadow.jsonl"
SCRATCH_DIR = Path("/tmp/jev-bench")
SCRATCH_SOURCE = Path.home() / "code" / "auto-mail" / "src" / "silence_alert.py"

sys.path.insert(0, str(REPO_ROOT))
from bench import tasks as T  # noqa: E402

ARMS = ("jev", "none")
TIMEOUT_S = 300
# Which Claude account tree the bench sessions run under. Machine-specific,
# so it comes from the environment (install/config.env.example is where a
# machine records its own value) and never from a name baked into this file.
CLAUDE_CONFIG_DIR = (
    os.environ.get("AIRLOCK_BENCH_CLAUDE_CONFIG_DIR")
    or os.environ.get("CLAUDE_CONFIG_DIR")
    or str(Path.home() / ".claude")
)
HOOK_PATH = str(REPO_ROOT / "hooks" / "airlock.py")


def _base_env(arm):
    env = dict(os.environ)
    env["CLAUDE_CONFIG_DIR"] = CLAUDE_CONFIG_DIR
    env["AIRLOCK_DISABLE"] = "1"  # always neutralise the live main-checkout shadow hook
    env.pop("AIRLOCK_MODE", None)
    if arm == "jev":
        env["AIRLOCK_BENCH_FORCE_MODE"] = "enforce"
    else:
        env.pop("AIRLOCK_BENCH_FORCE_MODE", None)
    return env


_RENDERED_SETTINGS = {}


def _settings_path(arm):
    """Render the arm's settings template into a temp file, substituting this
    checkout's own absolute hook path. The templates carry no absolute path,
    so the bench is portable to any machine and any worktree."""
    if arm in _RENDERED_SETTINGS:
        return _RENDERED_SETTINGS[arm]
    template = SETTINGS_DIR / ("jev.json" if arm == "jev" else "none.json")
    text = template.read_text().replace("__HOOK__", "%s %s" % (sys.executable, HOOK_PATH))
    out = Path(tempfile.mkdtemp(prefix="airlock-bench-")) / ("%s.json" % arm)
    out.write_text(text)
    _RENDERED_SETTINGS[arm] = str(out)
    return str(out)


def _run_session(prompt, cwd, arm):
    settings = _settings_path(arm)
    env = _base_env(arm)
    cmd = [
        "claude", "-p",
        "--model", "sonnet",
        "--effort", "low",
        "--output-format", "json",
        "--permission-mode", "bypassPermissions",
        "--settings", settings,
        prompt,
    ]
    start = time.monotonic()
    try:
        proc = subprocess.run(
            cmd, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
            capture_output=True, text=True, timeout=TIMEOUT_S,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False, "error": "timeout after %ss" % TIMEOUT_S,
            "wall_s": time.monotonic() - start,
            "stdout": (exc.stdout or "")[-2000:] if exc.stdout else "",
            "stderr": (exc.stderr or "")[-2000:] if exc.stderr else "",
        }
    wall_s = time.monotonic() - start
    if proc.returncode != 0:
        return {
            "ok": False, "error": "exit %d" % proc.returncode, "wall_s": wall_s,
            "stdout": proc.stdout[-2000:], "stderr": proc.stderr[-2000:],
        }
    try:
        parsed = json.loads(proc.stdout)
    except Exception as exc:
        return {
            "ok": False, "error": "bad JSON: %s" % exc, "wall_s": wall_s,
            "stdout": proc.stdout[-2000:], "stderr": proc.stderr[-2000:],
        }
    parsed["_wall_s"] = wall_s
    return {"ok": True, "parsed": parsed}


def _guard_rows_for_session(session_id):
    """Every airlock log row for this session, matched by session_id.
    Session IDs are freshly minted UUIDs per `claude -p` invocation, so a
    collision with a prior trial is not a practical concern."""
    rows = []
    try:
        with open(LOG_FILE, "r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                if row.get("session_id") == session_id:
                    rows.append(row)
    except FileNotFoundError:
        pass
    return rows


def _run_one_trial(task, arm, trial_idx, results):
    prompt = task.get("prompt")
    scratch_path = None
    if task["id"] == "T5":
        SCRATCH_DIR.mkdir(parents=True, exist_ok=True)
        scratch_path = SCRATCH_DIR / ("silence_alert_%s.py" % uuid.uuid4().hex[:8])
        shutil.copy(SCRATCH_SOURCE, scratch_path)
        prompt = task["prompt_template"].format(scratch_path=scratch_path)

    row = {
        "task_id": task["id"],
        "arm": arm,
        "trial": trial_idx,
        "prompt": prompt,
        "cwd": task["cwd"],
        "ts_start": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }

    outcome = _run_session(prompt, task["cwd"], arm)
    if not outcome["ok"]:
        row.update({"session_ok": False, "error": outcome["error"], "wall_s": outcome["wall_s"]})
        row["stdout_tail"] = outcome.get("stdout", "")
        row["stderr_tail"] = outcome.get("stderr", "")
        print("  [%s/%s trial %d] FAILED: %s" % (task["id"], arm, trial_idx, outcome["error"]))
        results.append(row)
        return

    p = outcome["parsed"]
    session_id = p.get("session_id")
    final_text = p.get("result") or ""

    row.update({
        "session_ok": True,
        "session_id": session_id,
        "wall_s": p.get("_wall_s"),
        "duration_ms": p.get("duration_ms"),
        "duration_api_ms": p.get("duration_api_ms"),
        "num_turns": p.get("num_turns"),
        "total_cost_usd": p.get("total_cost_usd"),
        "is_error": p.get("is_error"),
        "usage": p.get("usage"),
        "modelUsage": p.get("modelUsage"),
        "permission_denials": p.get("permission_denials"),
        "subagent_stats": p.get("subagent_stats"),
        "final_text": final_text[:2000],
    })

    checker = T.CHECKERS[task["id"]]
    if task["id"] == "T5":
        correct, detail = checker(final_text, str(scratch_path))
    else:
        correct = checker(final_text, task["_truth"])
        detail = None
    row["correct"] = correct
    if detail:
        row["correct_detail"] = detail

    guard_rows = _guard_rows_for_session(session_id) if session_id else []
    row["guard_rows"] = guard_rows
    row["guard_denies"] = sum(1 for g in guard_rows if g.get("enforced"))
    row["guard_overrides"] = sum(1 for g in guard_rows if g.get("override"))
    row["guard_loop_allows"] = sum(1 for g in guard_rows if g.get("loop_allow"))

    print(
        "  [%s/%s trial %d] ok=%s correct=%s wall=%.1fs cost=$%.4f turns=%s denies=%d"
        % (task["id"], arm, trial_idx, row["is_error"] is False, correct, row["wall_s"] or 0,
           row["total_cost_usd"] or 0, row["num_turns"], row["guard_denies"])
    )
    results.append(row)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--trials-per-arm", type=int, default=3)
    ap.add_argument("--tasks", default="T1,T2,T3,T4,T5")
    args = ap.parse_args()

    task_ids = [t.strip() for t in args.tasks.split(",") if t.strip()]
    selected = [t for t in T.TASKS if t["id"] in task_ids]

    print("Computing ground truth...")
    t1_truth = T.compute_t1_truth()
    t2_truth = T.compute_t2_truth()
    t3_truth = T._verify_t3_ground_truth()
    print("  T1 truth:", t1_truth)
    print("  T2 truth:", t2_truth)
    print("  T3 ground-truth check:", t3_truth)

    for t in selected:
        if t["id"] == "T1":
            t["_truth"] = t1_truth
        elif t["id"] == "T2":
            t["_truth"] = t2_truth
        elif t["id"] in ("T3", "T4"):
            t["_truth"] = t3_truth

    results = []
    for trial_idx in range(args.trials_per_arm):
        for task in selected:
            for arm in ARMS:
                _run_one_trial(task, arm, trial_idx, results)

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    jsonl_path = RESULTS_DIR / ("%s.jsonl" % stamp)
    with open(jsonl_path, "w") as f:
        for row in results:
            f.write(json.dumps(row, default=str) + "\n")
    print("Wrote %s" % jsonl_path)

    from bench.report import write_markdown
    md_path = RESULTS_DIR / ("%s.md" % stamp)
    write_markdown(results, selected, md_path)
    print("Wrote %s" % md_path)


if __name__ == "__main__":
    main()
