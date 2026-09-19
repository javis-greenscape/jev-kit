"""Jev vs Claude as decision-maker: the benchmark sweep described in SPIKE-NOTES.md.

Usage (TYPESAFE_API_KEY must already be loaded in this shell, never printed):

    set -a; . ~/.config/airlock/env 2>/dev/null; set +a
    NODE_PATH=$HOME/.npm-global/lib/node_modules \\
      CLAUDE_CONFIG_DIR=$HOME/.claude uv run python bench/run_bench.py

Runs 3 reps x 3 goals x 3 arms (J=jev, H=claude-haiku, S=claude-sonnet) = 27 runs, one headless
Chromium the whole time, strictly sequential, arms interleaved within each (goal, rep). Each run
is its own subprocess (bench/run_one.py) so a 120s-per-run hang can be hard-killed (whole process
group, including any standing `claude -p` child it spawned) without touching anything else on the
box. Writes bench/results-<timestamp>.jsonl, one row per run.

Arm M (a full headless Claude Code + Playwright-MCP session, no element-table loop) is attempted
only if `claude mcp list` under this account actually shows Playwright tools connected; skip is
reported, never faked.
"""

import json
import os
import signal
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
RUN_ONE = Path(__file__).resolve().parent / "run_one.py"
CDP_URL = os.environ.get("BU_CDP_URL", "http://127.0.0.1:9333")
CLAUDE_CONFIG_DIR = os.environ.get("CLAUDE_CONFIG_DIR", os.path.expanduser("~/.claude"))
NODE_PATH = os.environ.get("NODE_PATH", os.path.expanduser("~/.npm-global/lib/node_modules"))

GOALS = {
    "G1": "Find and open the Wikipedia article about Gödel's incompleteness theorems.",
    "G2": "Open the 'Create account' page.",
    "G3": "Search for 'Building automation', open the article, then open its 'Talk' page.",
}
ARMS = [("J", "jev"), ("H", "claude-haiku"), ("S", "claude-sonnet")]
REPS = 3
HARD_TIMEOUT_S = 125  # 5s over run_one.py's own 120s SIGALRM budget


def free_h():
    out = subprocess.run(["free", "-h"], capture_output=True, text=True, check=True).stdout
    print(out)
    return out


def cdp_ready():
    try:
        with urllib.request.urlopen(f"{CDP_URL}/json/version", timeout=2) as resp:
            return resp.status == 200
    except OSError:
        return False


def launch_chromium():
    if cdp_ready():
        print(f"Chromium already reachable at {CDP_URL}")
        return None
    print("Launching headless Chromium via scripts/launch_chromium.js ...")
    env = {**os.environ, "NODE_PATH": NODE_PATH}
    proc = subprocess.Popen(
        ["node", str(REPO_ROOT / "scripts" / "launch_chromium.js")],
        cwd=REPO_ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if cdp_ready():
            print(f"Chromium ready at {CDP_URL} (pid {proc.pid})")
            return proc
        time.sleep(0.2)
    proc.kill()
    raise RuntimeError("Chromium did not expose a CDP endpoint within 15s")


def playwright_mcp_available():
    """Probe, never assume: does this account have Playwright MCP tools connected?"""
    try:
        out = subprocess.run(
            ["claude", "mcp", "list"],
            env={**os.environ, "CLAUDE_CONFIG_DIR": CLAUDE_CONFIG_DIR},
            capture_output=True,
            text=True,
            timeout=30,
        ).stdout
    except (subprocess.SubprocessError, OSError) as exc:
        print(f"Arm M probe failed to run `claude mcp list`: {exc}")
        return False
    print("`claude mcp list` output:\n" + out)
    return "playwright" in out.lower() and "connected" in out.lower()


def run_trial(arm, provider, goal_id, goal_text, rep, env):
    cmd = [
        sys.executable,
        str(RUN_ONE),
        "--goal-id",
        goal_id,
        "--goal",
        goal_text,
        "--arm",
        arm,
        "--provider",
        provider,
        "--rep",
        str(rep),
    ]
    proc = subprocess.Popen(
        cmd, cwd=REPO_ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True
    )
    try:
        stdout, stderr = proc.communicate(timeout=HARD_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except ProcessLookupError:
            pass
        stdout, stderr = proc.communicate()
        return {
            "arm": arm,
            "provider": provider,
            "goal_id": goal_id,
            "goal": goal_text,
            "rep": rep,
            "success": False,
            "wall_s": float(HARD_TIMEOUT_S),
            "failure_reason": f"subprocess exceeded the {HARD_TIMEOUT_S}s hard timeout; process group killed",
            "decisions": [],
            "text_calls": [],
        }
    if stderr.strip():
        print(f"  [{arm}/{goal_id}/rep{rep} stderr tail] {stderr.strip()[-500:]}", file=sys.stderr)
    if proc.returncode != 0:
        return {
            "arm": arm,
            "provider": provider,
            "goal_id": goal_id,
            "goal": goal_text,
            "rep": rep,
            "success": False,
            "wall_s": None,
            "failure_reason": f"run_one.py exited {proc.returncode}: {stderr.strip()[-1000:]}",
            "decisions": [],
            "text_calls": [],
        }
    line = stdout.strip().splitlines()[-1] if stdout.strip() else ""
    try:
        return json.loads(line)
    except json.JSONDecodeError:
        return {
            "arm": arm,
            "provider": provider,
            "goal_id": goal_id,
            "goal": goal_text,
            "rep": rep,
            "success": False,
            "wall_s": None,
            "failure_reason": f"could not parse run_one.py stdout: {stdout.strip()[-1000:]}",
            "decisions": [],
            "text_calls": [],
        }


def main():
    print("free -h before starting:")
    free_h()

    print("pgrep -af 'claude -p' before:")
    subprocess.run("pgrep -af 'claude -p' || true", shell=True)

    chromium_proc = launch_chromium()

    env = {
        **os.environ,
        "BU_CDP_URL": CDP_URL,
        "CLAUDE_CONFIG_DIR": CLAUDE_CONFIG_DIR,
        "TEXT_MODEL_PROVIDER": "claude-standing",
        "MAX_THINKING_TOKENS": "0",
    }

    results_path = REPO_ROOT / "bench" / f"results-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.jsonl"
    rows = []
    with open(results_path, "w") as fh:
        for goal_id, goal_text in GOALS.items():
            for rep in range(1, REPS + 1):
                for arm, provider in ARMS:
                    print(f"--- {goal_id} rep {rep} arm {arm} ({provider}) ---")
                    row = run_trial(arm, provider, goal_id, goal_text, rep, env)
                    rows.append(row)
                    fh.write(json.dumps(row) + "\n")
                    fh.flush()
                    print(
                        f"    success={row.get('success')} wall_s={row.get('wall_s')} "
                        f"status={row.get('status')} reason={row.get('failure_reason')}"
                    )

    if playwright_mcp_available():
        print("Arm M: Playwright MCP tools are available; running one M trial per goal.")
        for goal_id, goal_text in GOALS.items():
            prompt = f"{goal_text} Use the Playwright browser tools. Stop when done and state the final URL."
            cmd = [
                "claude",
                "-p",
                "--model",
                "sonnet",
                "--effort",
                "low",
                "--output-format",
                "json",
                "--permission-mode",
                "bypassPermissions",
                prompt,
            ]
            started = time.perf_counter()
            proc = subprocess.run(
                cmd, cwd=REPO_ROOT, env={**env, "CLAUDE_CONFIG_DIR": CLAUDE_CONFIG_DIR}, capture_output=True, text=True
            )
            wall_s = round(time.perf_counter() - started, 3)
            try:
                payload = json.loads(proc.stdout)
            except json.JSONDecodeError:
                payload = {}
            row = {
                "arm": "M",
                "provider": "claude-code-mcp-sonnet",
                "goal_id": goal_id,
                "goal": goal_text,
                "rep": 1,
                "success": None,  # verified separately below, not from the agent's own claim
                "wall_s": wall_s,
                "duration_ms": payload.get("duration_ms"),
                "num_turns": payload.get("num_turns"),
                "total_cost_usd": payload.get("total_cost_usd"),
                "usage": payload.get("usage", {}),
                "final_text": payload.get("result"),
                "returncode": proc.returncode,
            }
            rows.append(row)
            with open(results_path, "a") as fh:
                fh.write(json.dumps(row) + "\n")
            print(f"    arm M {goal_id}: wall_s={wall_s} cost=${row['total_cost_usd']}")
    else:
        print(
            "Arm M SKIPPED: this account's `claude mcp list` does not show a connected Playwright "
            "MCP server. Not faked, not simulated. See SPIKE-NOTES.md."
        )

    if chromium_proc is not None:
        print(f"Stopping the Chromium process this script launched (pid {chromium_proc.pid}) ...")
        try:
            os.killpg(os.getpgid(chromium_proc.pid), signal.SIGTERM)
            chromium_proc.wait(timeout=5)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            try:
                os.killpg(os.getpgid(chromium_proc.pid), signal.SIGKILL)
            except ProcessLookupError:
                pass

    print("Stopping the browser_harness daemon (uv run browser-harness --reload) ...")
    subprocess.run(["uv", "run", "browser-harness", "--reload"], cwd=REPO_ROOT)

    print("pgrep -af 'chrom|browser_harness' after cleanup:")
    subprocess.run("pgrep -af 'chrom|browser_harness' || echo '(none)'", shell=True)
    print("pgrep -af 'claude -p' after:")
    subprocess.run("pgrep -af 'claude -p' || true", shell=True)

    print(f"\nWrote {len(rows)} rows to {results_path}")


if __name__ == "__main__":
    main()
