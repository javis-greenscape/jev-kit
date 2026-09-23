"""Head-to-head on bench/wiki_tasks.py: Jev through `browse`, against a Sonnet
Claude Code session driving Playwright MCP.

    uv run python bench/run_wiki_bench.py            # 6 tasks x 3 reps x 2 arms
    uv run python bench/run_wiki_bench.py --reps 1   # a shorter smoke sweep

Both arms get the same goal text and the same 180 s budget, and the two arms run
one after another, never side by side, so neither is timed against the other's
load. Each run is its own process with its own fresh headless Chromium, so no
run inherits a page from the one before it.

  jev              the goal goes to the installed `browse` MCP server over stdio
                   (~/.local/share/airlock/current/browse/server.py, or
                   $JEV_BROWSE_SERVER). Jev picks every click. The answer is read
                   off the final page with the task's `extract` selector, because
                   `browse` returns a page, not a sentence.
  sonnet-playwright `claude -p --model sonnet` with one MCP server (Playwright,
                   headless and isolated) and nothing else: `--strict-mcp-config`
                   so no other MCP server loads, `--setting-sources ''` plus a
                   hookless `--settings` file so this child runs without the
                   account's hooks, and the shell and fetch tools disallowed so
                   the page is the only source. Cost and tokens come out of
                   `--output-format json`; they are never computed from rates.

Neither arm is believed about its own success. bench/wiki_tasks.check reads the
final URL and the returned text. Rows land in bench/results-wiki-<timestamp>.jsonl.

`browse` reports steps and elapsed_ms but no Jev token count or per-decision
latency, so those columns are null for the jev arm rather than guessed.
"""

import argparse
import json
import os
import re
import select
import signal
import statistics
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

BENCH_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BENCH_DIR))

from wiki_tasks import TASKS, check  # noqa: E402

RUN_BUDGET_S = 180.0
KILL_GRACE_S = 20.0
DEFAULT_BROWSE_SERVER = Path.home() / ".local/share/airlock/current/browse/server.py"


def log(msg):
    sys.stderr.write(msg + "\n")
    sys.stderr.flush()


def chromium_pids():
    out = subprocess.run(
        "pgrep -f chrom || true", shell=True, capture_output=True, text=True
    ).stdout
    return {int(p) for p in out.split() if p.strip().isdigit()}


# --- arm: jev via the installed browse MCP server -----------------------------


def read_json_line(proc, want_id, deadline):
    """Read stdout lines until the reply with `want_id` arrives or time runs out."""
    buf = ""
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None
        ready, _, _ = select.select([proc.stdout], [], [], min(remaining, 1.0))
        if not ready:
            if proc.poll() is not None:
                return None
            continue
        chunk = proc.stdout.readline()
        if not chunk:
            return None
        buf = chunk.strip()
        if not buf:
            continue
        try:
            msg = json.loads(buf)
        except json.JSONDecodeError:
            continue
        if msg.get("id") == want_id:
            return msg


def run_jev(task, log_path):
    server = Path(os.environ.get("JEV_BROWSE_SERVER") or DEFAULT_BROWSE_SERVER)
    env = dict(os.environ, JEV_BROWSE_TIMEOUT=str(int(RUN_BUDGET_S)))
    row = {"arm": "jev", "task_id": task["id"]}
    started = time.perf_counter()
    proc = subprocess.Popen(
        [sys.executable, str(server)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=open(log_path, "a"),
        text=True,
        env=env,
        start_new_session=True,
    )
    try:
        deadline = time.monotonic() + RUN_BUDGET_S + KILL_GRACE_S

        def send(payload):
            proc.stdin.write(json.dumps(payload) + "\n")
            proc.stdin.flush()

        send({
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                       "clientInfo": {"name": "wiki-bench", "version": "1"}},
        })
        if read_json_line(proc, 1, min(deadline, time.monotonic() + 30)) is None:
            raise RuntimeError("the browse server did not answer initialize")
        send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        send({
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "browse", "arguments": {
                "goal": task["goal"],
                "start_url": task["start_url"],
                "extract": task["extract"],
            }},
        })
        reply = read_json_line(proc, 2, deadline)
        row["wall_s"] = round(time.perf_counter() - started, 2)
        if reply is None:
            row.update(passed=False, reason="no reply within the %.0fs budget" % RUN_BUDGET_S,
                       steps=None, final_url=None)
            return row
        result = reply.get("result") or {}
        text = (result.get("content") or [{}])[0].get("text", "")
        if result.get("isError"):
            row.update(passed=False, reason="browse returned an error: " + text[:300],
                       steps=None, final_url=None)
            return row
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            row.update(passed=False, reason="browse returned text that is not JSON: " + text[:300],
                       steps=None, final_url=None)
            return row
        answer = (payload.get("extracted") or "") + "\n" + (payload.get("text") or "")
        passed, reason = check(task["id"], payload.get("final_url"), answer)
        row.update(
            passed=passed, reason=reason,
            final_url=payload.get("final_url"), title=payload.get("title"),
            steps=payload.get("steps"), status=payload.get("status"),
            elapsed_ms=payload.get("elapsed_ms"),
            extracted=(payload.get("extracted") or "")[:600],
            # `browse` reports no Jev token count or per-decision latency.
            jev_tokens=None, jev_latency_ms=None,
            cost_usd=None, tokens=None,
        )
        return row
    except Exception as exc:  # noqa: BLE001 - every failure has to become a row
        row.setdefault("wall_s", round(time.perf_counter() - started, 2))
        row.update(passed=False, reason="%s: %s" % (type(exc).__name__, exc),
                   steps=None, final_url=None)
        return row
    finally:
        try:
            proc.stdin.close()
        except Exception:
            pass
        try:
            proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except ProcessLookupError:
                pass


# --- arm: sonnet driving Playwright MCP ---------------------------------------

SONNET_SUFFIX = " Use the Playwright browser tools. Answer with the final URL and the answer."
BLOCKED_TOOLS = ["Bash", "WebFetch", "WebSearch", "Read", "Write", "Edit",
                 "Glob", "Grep", "Agent", "Task", "NotebookEdit"]


def extract_final_url(text):
    urls = [u.rstrip(".,;:") for u in re.findall(r"https?://[^\s\)\]\"'>`,]+", text or "")]
    articles = [u for u in urls if "wikipedia.org/wiki/" in u]
    if articles:
        return articles[-1]
    return urls[-1] if urls else None


def run_sonnet(task, mcp_config, settings_file, log_path):
    row = {"arm": "sonnet-playwright", "task_id": task["id"]}
    cmd = [
        "claude", "-p", "--model", "sonnet",
        "--output-format", "json",
        "--permission-mode", "bypassPermissions",
        "--mcp-config", str(mcp_config), "--strict-mcp-config",
        "--settings", str(settings_file), "--setting-sources", "",
        "--disallowedTools", *BLOCKED_TOOLS,
    ]
    started = time.perf_counter()
    try:
        proc = subprocess.run(
            cmd, input=task["goal"] + SONNET_SUFFIX,
            capture_output=True, text=True, timeout=RUN_BUDGET_S,
            start_new_session=True,
        )
        stdout, stderr, returncode = proc.stdout, proc.stderr, proc.returncode
        timed_out = False
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        stderr, returncode, timed_out = "", None, True
    row["wall_s"] = round(time.perf_counter() - started, 2)
    with open(log_path, "a") as fh:
        fh.write("\n=== %s ===\n%s\n" % (task["id"], (stderr or "")[-2000:]))
    if timed_out:
        row.update(passed=False, reason="timed out at %.0fs" % RUN_BUDGET_S,
                   steps=None, final_url=None, cost_usd=None, tokens=None)
        return row
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        row.update(passed=False, steps=None, final_url=None, cost_usd=None, tokens=None,
                   reason="could not parse the session JSON (exit %s): %s"
                          % (returncode, (stdout or "")[-300:]))
        return row
    final_text = payload.get("result") or ""
    final_url = extract_final_url(final_text)
    passed, reason = check(task["id"], final_url, final_text)
    usage = payload.get("usage") or {}
    row.update(
        passed=passed, reason=reason, final_url=final_url,
        # num_turns counts the session's assistant turns; each is a tool call or the answer.
        steps=payload.get("num_turns"),
        cost_usd=payload.get("total_cost_usd"),
        tokens={
            "input": usage.get("input_tokens"),
            "output": usage.get("output_tokens"),
            "cache_read": usage.get("cache_read_input_tokens"),
            "cache_creation": usage.get("cache_creation_input_tokens"),
        },
        duration_ms=payload.get("duration_ms"),
        final_text=final_text[:1200],
        returncode=returncode,
    )
    return row


# --- the sweep ----------------------------------------------------------------


def summarise(rows):
    lines = []
    lines.append("| arm | pass rate | median s (passes) | p90 s (passes) | median cost USD |")
    lines.append("|---|---|---|---|---|")
    for arm in ("jev", "sonnet-playwright"):
        mine = [r for r in rows if r["arm"] == arm]
        if not mine:
            continue
        passes = [r for r in mine if r.get("passed")]
        secs = sorted(r["wall_s"] for r in passes if r.get("wall_s") is not None)
        median = "%.1f" % statistics.median(secs) if secs else "n/a"
        p90 = "%.1f" % secs[min(len(secs) - 1, int(round(0.9 * (len(secs) - 1))))] if secs else "n/a"
        costs = [r["cost_usd"] for r in mine if r.get("cost_usd") is not None]
        cost = "$%.3f" % statistics.median(costs) if costs else "n/a"
        lines.append("| %s | %d/%d (%.0f%%) | %s | %s | %s |"
                     % (arm, len(passes), len(mine), 100.0 * len(passes) / len(mine),
                        median, p90, cost))
    lines.append("")
    lines.append("| task | jev | sonnet-playwright |")
    lines.append("|---|---|---|")
    for task in TASKS:
        cells = []
        for arm in ("jev", "sonnet-playwright"):
            mine = [r for r in rows if r["arm"] == arm and r["task_id"] == task["id"]]
            cells.append("%d/%d" % (sum(1 for r in mine if r.get("passed")), len(mine)))
        lines.append("| %s | %s | %s |" % (task["id"], cells[0], cells[1]))
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--reps", type=int, default=3)
    parser.add_argument("--tasks", default="", help="comma-separated task ids, default all")
    parser.add_argument("--arms", default="jev,sonnet-playwright")
    args = parser.parse_args()

    wanted = [t for t in TASKS if not args.tasks or t["id"] in args.tasks.split(",")]
    arms = args.arms.split(",")

    before = chromium_pids()
    log("chromium pids before: %d process(es)" % len(before))

    workdir = Path(tempfile.mkdtemp(prefix="wiki-bench-"))
    mcp_config = workdir / "mcp.json"
    mcp_config.write_text(json.dumps({"mcpServers": {"pw": {
        "command": "npx",
        "args": ["@playwright/mcp@latest", "--headless", "--isolated"],
    }}}))
    settings_file = workdir / "no-hooks.json"
    settings_file.write_text(json.dumps({"hooks": {}}))
    jev_log = workdir / "browse.log"
    sonnet_log = workdir / "sonnet.log"

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    results_path = BENCH_DIR / ("results-wiki-%s.jsonl" % stamp)
    rows = []
    with open(results_path, "w") as fh:
        for task in wanted:
            for rep in range(1, args.reps + 1):
                for arm in arms:
                    log("--- %s rep %d arm %s ---" % (task["id"], rep, arm))
                    if arm == "jev":
                        row = run_jev(task, jev_log)
                    else:
                        row = run_sonnet(task, mcp_config, settings_file, sonnet_log)
                    row["rep"] = rep
                    row["goal"] = task["goal"]
                    row["started_utc"] = datetime.now(timezone.utc).isoformat()
                    rows.append(row)
                    fh.write(json.dumps(row) + "\n")
                    fh.flush()
                    log("    passed=%s wall_s=%s steps=%s url=%s reason=%s"
                        % (row.get("passed"), row.get("wall_s"), row.get("steps"),
                           row.get("final_url"), row.get("reason")))

    after = chromium_pids()
    strays = after - before
    log("chromium pids after: %d; started by this sweep and still alive: %s"
        % (len(after), sorted(strays) or "none"))
    for pid in strays:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    if strays:
        time.sleep(3)
        still = chromium_pids() & strays
        for pid in still:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
    log("chromium pids at exit: %d" % len(chromium_pids()))

    print(summarise(rows))
    print("\nwrote %d rows to %s" % (len(rows), results_path))
    print("logs: %s" % workdir)


if __name__ == "__main__":
    main()
