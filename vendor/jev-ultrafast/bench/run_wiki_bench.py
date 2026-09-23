"""Three ways to do bench/wiki_tasks.py: Jev alone through `browse`, a Sonnet
Claude Code session driving Playwright MCP, and Sonnet planning the route while
`browse` executes each step.

    uv run python bench/run_wiki_bench.py            # 8 tasks x 3 reps x 3 arms
    uv run python bench/run_wiki_bench.py --reps 1   # a shorter smoke sweep

Every arm gets the same goal text and the same 180 s budget, and the arms run
one after another, never side by side, so none is timed against another's load.

  jev              One `browse` MCP server is started for the whole sweep and
                   kept open across every jev task and rep, the way a real
                   session reuses it: the warm worker process, the warm text
                   model and the warm browser_harness connection
                   (browse/runner.py, "Warm worker and warm Haiku") all carry
                   over from call to call. Only the very first call pays the
                   cold-start cost; its row is marked `"cold_start": true` so
                   it can be reported separately rather than folded into the
                   warm median. `browse` still enforces its own per-call
                   timeout and restarts a fresh worker after one, without the
                   server process itself restarting.
  sonnet-playwright `claude -p --model sonnet` with one MCP server
                   (Playwright, headless and isolated) and nothing else:
                   `--strict-mcp-config` so no other MCP server loads,
                   `--setting-sources ''` plus a hookless `--settings` file so
                   this child runs without the account's hooks, and the shell
                   and fetch tools disallowed so the page is the only source.
                   Cost and tokens come out of `--output-format json`; they
                   are never computed from rates.
  sonnet-plans-jev The intended shape: the same Sonnet session, isolated the
                   same way, with the installed release's `browse` server as
                   its only tool. Claude plans the route and hands `browse`
                   one or two explicit steps at a time; `browse` executes them
                   and says where it ended. It starts its own server per run,
                   so unlike the jev arm every run pays a cold start. The row
                   carries `browse_calls` and `browse_ms`, the summed `timing`
                   the calls report, so the time inside the tool can be told
                   apart from the time Claude spent planning.

No arm is ever asked to report a fact and none is believed about its own
success. Group A tasks name every hop and end "Stop when the X article is
open" - the scorer, not the arm, reads the fact off the final page:
`browse`'s own `text`/`extracted` for jev, and an independent urllib fetch of
`final_url` (wiki_tasks.fetch_page_text) for the two Sonnet arms, so every arm
is scored from the same kind of source. Group B is the old open-ended pair,
kept as a labelled contrast; it never checks a fact, only the final URL.

Rows land in bench/results-wiki-<timestamp>.jsonl.
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

from wiki_tasks import TASKS, check, fetch_page_text  # noqa: E402

ARMS = ("jev", "sonnet-playwright", "sonnet-plans-jev")
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


# --- arm: jev via one long-lived browse MCP server for the whole sweep --------


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


class JevSession:
    """One `browse` MCP server process, held open across the whole sweep.

    A fresh process per call (the previous benchmark's shape) throws away the
    warm worker, the warm text model and the warm browser_harness connection
    every single time, which is exactly the thing PR #11 added and this
    sweep exists to measure. One process, initialized once, called once per
    (task, rep) - the same shape a real MCP client uses.
    """

    def __init__(self, log_path):
        server = Path(os.environ.get("JEV_BROWSE_SERVER") or DEFAULT_BROWSE_SERVER)
        env = dict(os.environ, JEV_BROWSE_TIMEOUT=str(int(RUN_BUDGET_S)))
        self.proc = subprocess.Popen(
            [sys.executable, str(server)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=open(log_path, "a"),
            text=True,
            env=env,
            start_new_session=True,
        )
        self._next_id = 1
        self._first_call_done = False
        self._send({
            "jsonrpc": "2.0", "id": self._id(), "method": "initialize",
            "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                       "clientInfo": {"name": "wiki-bench", "version": "1"}},
        })
        reply = read_json_line(self.proc, self._next_id - 1, time.monotonic() + 30)
        if reply is None:
            raise RuntimeError("the browse server did not answer initialize")
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def _id(self):
        i = self._next_id
        self._next_id += 1
        return i

    def _send(self, payload):
        self.proc.stdin.write(json.dumps(payload) + "\n")
        self.proc.stdin.flush()

    def ask(self, task):
        row = {"arm": "jev", "task_id": task["id"], "group": task["group"]}
        row["cold_start"] = not self._first_call_done
        self._first_call_done = True
        started = time.perf_counter()
        call_id = self._id()
        self._send({
            "jsonrpc": "2.0", "id": call_id, "method": "tools/call",
            "params": {"name": "browse", "arguments": {
                "goal": task["goal"],
                "start_url": task["start_url"],
                "extract": task["extract"],
            }},
        })
        deadline = time.monotonic() + RUN_BUDGET_S + KILL_GRACE_S
        reply = read_json_line(self.proc, call_id, deadline)
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
            timing=payload.get("timing"),
            cost_usd=None, tokens=None,
        )
        return row

    def close(self):
        try:
            self.proc.stdin.close()
        except Exception:
            pass
        try:
            self.proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(os.getpgid(self.proc.pid), signal.SIGKILL)
            except ProcessLookupError:
                pass


# --- arm: sonnet driving Playwright MCP ---------------------------------------

SONNET_SUFFIX = " Use the Playwright browser tools. When you are done, say the final URL you ended on."
BLOCKED_TOOLS = ["Bash", "WebFetch", "WebSearch", "Read", "Write", "Edit",
                 "Glob", "Grep", "Agent", "Task", "NotebookEdit"]


def extract_final_url(text):
    """The last Wikipedia article URL mentioned, with balanced parens kept.

    The naive `[^\\)]+` character class used to exclude ')' from a URL
    outright, which truncated any article whose title has one, e.g.
    .../wiki/Hans_Meyer_(geographer) came back as
    .../wiki/Hans_Meyer_(geographer missing its close paren and never
    matched url_must_contain. This keeps ')' inside the URL and only trims
    a genuinely trailing one - the kind markdown or prose wraps a link in -
    by checking the URL's own paren balance.
    """
    urls = []
    for m in re.finditer(r"https?://[^\s\"'>`]+", text or ""):
        u = m.group(0).rstrip(".,;:")
        # Sonnet wraps its final-URL line in markdown bold/italics
        # ("**https://...**"), and `*` is a legal URL character, so it has
        # to be stripped explicitly rather than left to the character class.
        u = u.rstrip("*_")
        while u.endswith(")") and u.count("(") < u.count(")"):
            u = u[:-1]
        urls.append(u)
    articles = [u for u in urls if "wikipedia.org/wiki/" in u]
    if articles:
        return articles[-1]
    return urls[-1] if urls else None


def run_sonnet(task, mcp_config, settings_file, log_path):
    row = {"arm": "sonnet-playwright", "task_id": task["id"], "group": task["group"]}
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
    # The scorer fetches the final page itself; the fact never comes from the
    # model's own transcript, same as the jev arm reads it off browse's page.
    page_text = fetch_page_text(final_url) if final_url else ""
    passed, reason = check(task["id"], final_url, page_text)
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


# --- arm: sonnet planning the route and handing each step to `browse` ---------

# The intended shape: Claude plans, Jev executes. `browse` is fast at "click the
# link that says X" and cannot plan or know a fact; a Claude session can plan and
# cannot see the page. This arm gives each of them only the half it is good at.
PLANS_JEV_SUFFIX = " Use the browse tool. When you are done, say the final URL you ended on."
PLANS_JEV_SYSTEM = (
    "You have one tool: `browse`. It drives a real browser. It executes concrete on-screen "
    "steps quickly, but it cannot plan and it does not know any facts. You do the planning.\n"
    "- Work the route out yourself, then call `browse` with one or two explicit steps at a "
    "time. Never hand it the whole multi-hop task.\n"
    "- Write the goal as the literal clicks or keystrokes to perform, naming the exact link "
    "text to click.\n"
    "- Set `start_url` to the page you are on now. For the first call that is the page the "
    "task starts on.\n"
    "- The reply is JSON. Read `final_url` for where you now are and `text` for what the page "
    "says, and continue from there. Do not assume a call went where you meant it to.\n"
    "- Pass `extract` with a CSS selector when you need particular text off the page.\n"
    "- A reply with `status` of `blocked` means it could not find what you named. Reword the "
    "step or split it, rather than repeating it unchanged."
)


def parse_stream(stdout):
    """(events, browse_calls, browse_ms) out of one `--output-format stream-json` run.

    `browse_ms` is the sum of the `timing.total_ms` each `browse` reply carries: the time
    inside the tool, which the session's own wall time contains. The difference between the
    two is what Claude spent planning."""
    events, calls, browse_ms = [], 0, 0
    for line in (stdout or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        events.append(event)
        content = ((event.get("message") or {}).get("content")) or []
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use" and str(block.get("name", "")).endswith("browse"):
                calls += 1
            if block.get("type") == "tool_result":
                for part in block.get("content") or []:
                    if not isinstance(part, dict) or part.get("type") != "text":
                        continue
                    try:
                        payload = json.loads(part.get("text") or "")
                    except (json.JSONDecodeError, TypeError):
                        continue
                    if isinstance(payload, dict):
                        browse_ms += ((payload.get("timing") or {}).get("total_ms")) or 0
    return events, calls, browse_ms


def last_browse_url(events):
    """The `final_url` of the last `browse` reply, which is where the session actually ended."""
    url = None
    for event in events:
        content = ((event.get("message") or {}).get("content")) or []
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_result":
                continue
            for part in block.get("content") or []:
                if not isinstance(part, dict) or part.get("type") != "text":
                    continue
                try:
                    payload = json.loads(part.get("text") or "")
                except (json.JSONDecodeError, TypeError):
                    continue
                if isinstance(payload, dict) and payload.get("final_url"):
                    url = payload["final_url"]
    return url


def run_sonnet_plans_jev(task, mcp_config, settings_file, log_path):
    row = {"arm": "sonnet-plans-jev", "task_id": task["id"], "group": task["group"]}
    cmd = [
        "claude", "-p", "--model", "sonnet",
        "--output-format", "stream-json", "--verbose",
        "--permission-mode", "bypassPermissions",
        "--mcp-config", str(mcp_config), "--strict-mcp-config",
        "--settings", str(settings_file), "--setting-sources", "",
        "--append-system-prompt", PLANS_JEV_SYSTEM,
        "--disallowedTools", *BLOCKED_TOOLS,
    ]
    started = time.perf_counter()
    try:
        proc = subprocess.run(
            cmd, input=task["goal"] + PLANS_JEV_SUFFIX,
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
    events, calls, browse_ms = parse_stream(stdout)
    row["browse_calls"] = calls
    row["browse_ms"] = browse_ms
    if timed_out:
        row.update(passed=False, reason="timed out at %.0fs" % RUN_BUDGET_S,
                   steps=None, final_url=None, cost_usd=None, tokens=None)
        return row
    result = next((e for e in reversed(events) if e.get("type") == "result"), None)
    if result is None:
        row.update(passed=False, steps=None, final_url=None, cost_usd=None, tokens=None,
                   reason="no result event in the session stream (exit %s): %s"
                          % (returncode, (stdout or "")[-300:]))
        return row
    final_text = result.get("result") or ""
    # Where it ended is read off the last `browse` reply, not off the session's prose; the
    # stated URL is only the fallback when no call returned one.
    final_url = last_browse_url(events) or extract_final_url(final_text)
    page_text = fetch_page_text(final_url) if final_url else ""
    passed, reason = check(task["id"], final_url, page_text)
    usage = result.get("usage") or {}
    row.update(
        passed=passed, reason=reason, final_url=final_url,
        steps=result.get("num_turns"),
        cost_usd=result.get("total_cost_usd"),
        tokens={
            "input": usage.get("input_tokens"),
            "output": usage.get("output_tokens"),
            "cache_read": usage.get("cache_read_input_tokens"),
            "cache_creation": usage.get("cache_creation_input_tokens"),
        },
        duration_ms=result.get("duration_ms"),
        final_text=final_text[:1200],
        returncode=returncode,
    )
    return row


# --- the sweep ----------------------------------------------------------------


def summarise(rows):
    lines = []
    for group in ("A", "B"):
        group_rows = [r for r in rows if r.get("group") == group]
        if not group_rows:
            continue
        label = ("Group A (navigation, hops named)" if group == "A"
                  else "Group B (open-ended, not what jev is built for)")
        lines.append("### %s" % label)
        lines.append("")
        lines.append("| arm | pass rate | median s (passes) | p90 s (passes) | median cost USD |")
        lines.append("|---|---|---|---|---|")
        for arm in ARMS:
            mine = [r for r in group_rows if r["arm"] == arm]
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
        lines.append("| task | " + " | ".join(ARMS) + " |")
        lines.append("|---" * (len(ARMS) + 1) + "|")
        for task in TASKS:
            if task["group"] != group:
                continue
            cells = []
            for arm in ARMS:
                mine = [r for r in group_rows if r["arm"] == arm and r["task_id"] == task["id"]]
                cells.append("%d/%d" % (sum(1 for r in mine if r.get("passed")), len(mine)))
            lines.append("| %s | %s |" % (task["id"], " | ".join(cells)))
        lines.append("")
    plans = [r for r in rows if r["arm"] == "sonnet-plans-jev"]
    if plans:
        lines.append("### sonnet-plans-jev: where its wall time went")
        lines.append("")
        lines.append("| group | runs | median browse calls | median browse s | median claude s |")
        lines.append("|---|---|---|---|---|")
        for group in ("A", "B"):
            mine = [r for r in plans if r.get("group") == group]
            if not mine:
                continue
            calls = sorted(r.get("browse_calls") or 0 for r in mine)
            in_browse = sorted((r.get("browse_ms") or 0) / 1000.0 for r in mine)
            planning = sorted(
                (r["wall_s"] - (r.get("browse_ms") or 0) / 1000.0) for r in mine if r.get("wall_s")
            )
            lines.append(
                "| %s | %d | %.0f | %.1f | %.1f |"
                % (group, len(mine), statistics.median(calls), statistics.median(in_browse),
                   statistics.median(planning) if planning else 0.0)
            )
        lines.append("")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--reps", type=int, default=3)
    parser.add_argument("--tasks", default="", help="comma-separated task ids, default all")
    parser.add_argument("--arms", default=",".join(ARMS))
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
    # The third arm gets the installed release's own `browse` server and nothing else. It
    # starts one per run, so every run of this arm pays the cold start the jev arm pays once.
    browse_mcp_config = workdir / "mcp-browse.json"
    browse_mcp_config.write_text(json.dumps({"mcpServers": {"browse": {
        "command": sys.executable,
        "args": [str(Path(os.environ.get("JEV_BROWSE_SERVER") or DEFAULT_BROWSE_SERVER))],
        "env": {"JEV_BROWSE_TIMEOUT": str(int(RUN_BUDGET_S))},
    }}}))
    settings_file = workdir / "no-hooks.json"
    settings_file.write_text(json.dumps({"hooks": {}}))
    jev_log = workdir / "browse.log"
    sonnet_log = workdir / "sonnet.log"
    plans_log = workdir / "plans-jev.log"

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    results_path = BENCH_DIR / ("results-wiki-%s.jsonl" % stamp)
    rows = []

    jev_session = JevSession(jev_log) if "jev" in arms else None
    try:
        with open(results_path, "w") as fh:
            for task in wanted:
                for rep in range(1, args.reps + 1):
                    for arm in arms:
                        log("--- %s rep %d arm %s ---" % (task["id"], rep, arm))
                        if arm == "jev":
                            row = jev_session.ask(task)
                        elif arm == "sonnet-plans-jev":
                            row = run_sonnet_plans_jev(task, browse_mcp_config, settings_file, plans_log)
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
    finally:
        if jev_session is not None:
            jev_session.close()

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
