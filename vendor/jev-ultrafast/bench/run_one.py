"""Run exactly one (arm, goal, rep) benchmark trial in an isolated subprocess.

Invoked by bench/run_bench.py, never by hand for the real sweep. Prints exactly one JSON line to
stdout: the result record that goes straight into bench/results-<timestamp>.jsonl. Everything
else (warnings, the standing-child logger) goes to stderr so it never corrupts that one line.

Isolation rationale: a hard 120s-per-run budget is enforced here with SIGALRM (covers a hang
anywhere in the loop: browser, TypeSafe, or a standing Claude child), and the whole process runs
under its own process group so bench/run_bench.py can hard-kill everything this run spawned
(including a standing decision/text-model `claude -p` child) if the alarm and internal timeouts
somehow do not resolve it. See SPIKE-NOTES.md, "Jev versus Claude as decision-maker".
"""

import argparse
import json
import os
import signal
import sys
import time
import traceback
from urllib.parse import unquote

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

WIKI_MAIN_PAGE = "https://en.wikipedia.org/wiki/Main_Page"
RUN_BUDGET_S = 120


def verify(goal_id, url, title):
    """Independent success check from the final URL/title. Never trust the agent's own DONE."""
    u = unquote(url or "").lower()
    t = (title or "").lower()
    if goal_id == "G1":
        ok = "incompleteness" in u or "incompleteness" in t
        reason = None if ok else f"final page is not the Gödel incompleteness theorems article: {url!r}"
        return ok, reason
    if goal_id == "G2":
        ok = "createaccount" in u.replace("_", "").replace(" ", "") or "create account" in t
        reason = None if ok else f"final page is not Wikipedia's Create account page: {url!r}"
        return ok, reason
    if goal_id == "G3":
        ok = "/wiki/talk:" in u and "building" in u and "automation" in u
        reason = None if ok else f"final page is not Talk:Building automation (or similar): {url!r}"
        return ok, reason
    raise ValueError(f"unknown goal id {goal_id!r}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--goal-id", required=True, choices=["G1", "G2", "G3"])
    parser.add_argument("--goal", required=True)
    parser.add_argument("--url", default=WIKI_MAIN_PAGE)
    parser.add_argument("--arm", required=True, help="Short arm label for the output row, e.g. J/H/S")
    parser.add_argument("--provider", required=True, choices=["jev", "claude-haiku", "claude-sonnet"])
    parser.add_argument("--rep", type=int, required=True)
    args = parser.parse_args()

    os.environ["DECISION_PROVIDER"] = args.provider
    os.environ.setdefault("TEXT_MODEL_PROVIDER", "claude-standing")
    os.environ.setdefault("MAX_THINKING_TOKENS", "0")

    def _on_alarm(_signum, _frame):
        raise TimeoutError(f"run exceeded the {RUN_BUDGET_S}s per-run budget")

    signal.signal(signal.SIGALRM, _on_alarm)
    signal.alarm(RUN_BUDGET_S)

    from jev_ultrafast import Agent, decision_claude
    from jev_ultrafast.text_model_claude_standing import shutdown as shutdown_text
    from jev_ultrafast.text_model_claude_standing import warm as warm_text

    result = {
        "arm": args.arm,
        "goal_id": args.goal_id,
        "goal": args.goal,
        "provider": args.provider,
        "rep": args.rep,
        "success": False,
        "status": None,
        "wall_s": None,
        "final_url": None,
        "final_title": None,
        "num_decisions": 0,
        "num_actions": 0,
        "decisions": [],
        "text_calls": [],
        "failure_reason": None,
    }
    started = time.perf_counter()
    state = None
    try:
        warm_text()
        if args.provider != "jev":
            decision_claude.warm(args.provider)
        with Agent(args.url, args.goal) as agent:
            for state in agent.run():
                pass
        result["status"] = state["status"]
        result["final_url"] = state["page"]["url"]
        result["final_title"] = state["page"]["title"]
        result["num_actions"] = len(state["history"])
        result["decisions"] = [
            {
                "operation": d.get("operation"),
                "target": d.get("target"),
                "choice": d.get("choice"),
                "confidence": d.get("confidence"),
                "latency_ms": d.get("latency_ms"),
                "usage": d.get("usage", {}),
                "model": d.get("model"),
            }
            for d in state["decisions"]
        ]
        result["num_decisions"] = len(result["decisions"])
        result["text_calls"] = [
            {
                "field": t.get("field"),
                "model": t.get("model"),
                "latency_ms": t.get("latency_ms"),
                "usage": t.get("usage", {}),
            }
            for t in state["text_calls"]
        ]
        ok, reason = verify(args.goal_id, result["final_url"], result["final_title"])
        if not ok:
            result["failure_reason"] = reason
        elif state["status"] != "done":
            result["failure_reason"] = f"agent status was {state['status']!r}, not done"
        result["success"] = bool(ok and state["status"] == "done")
    except Exception as exc:  # noqa: BLE001 - every failure mode must become a result row, never a crash
        result["failure_reason"] = f"{type(exc).__name__}: {exc}"
        print(traceback.format_exc(limit=8), file=sys.stderr)
        if state is not None:
            result["status"] = state.get("status")
            result["final_url"] = state.get("page", {}).get("url")
            result["final_title"] = state.get("page", {}).get("title")
    finally:
        signal.alarm(0)
        result["wall_s"] = round(time.perf_counter() - started, 3)
        try:
            decision_claude.shutdown()
        except Exception:
            pass
        try:
            shutdown_text()
        except Exception:
            pass

    print(json.dumps(result))


if __name__ == "__main__":
    main()
