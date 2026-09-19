"""python3 -m airlock.eval -- run every labelled case in eval/cases.jsonl
through the REAL Jev call (via client.ask, which prefers the warm daemon) and
report accuracy per guard, a confusion table, false-deny/missed-deny counts,
mean latency, and total tokens.

Runs cases in parallel (max 4 concurrent Jev calls). Exit code is always 0 --
this is a report, not a test suite; the numbers are the output. Pass --json
to also write eval/last_result.json.
"""
import argparse
import json
import statistics
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import client, policy, questions, rules as rules_mod, scope as scope_mod

REPO_ROOT = Path(__file__).resolve().parent.parent
CASES_FILE = REPO_ROOT / "eval" / "cases.jsonl"
RESULT_FILE = REPO_ROOT / "eval" / "last_result.json"

MAX_WORKERS = 4


def load_cases(path=None):
    path = Path(path) if path else CASES_FILE
    cases = []
    if not path.exists():
        return cases
    with open(path, "r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                cases.append(json.loads(line))
            except Exception:
                continue
    return cases


def _judge_tier(payload):
    ti = payload.get("tool_input") or {}
    subagent_type = str(ti.get("subagent_type") or "")
    model_override = str(ti.get("model") or "")
    description = str(ti.get("description") or "")
    prompt = str(ti.get("prompt") or "")

    state = questions.tier_state(subagent_type, model_override, description, prompt)
    qs = questions.tier_questions()
    response, latency_ms = client.ask({"state": state, "model": client.MODEL, "questions": qs})

    answers = response.get("answers") or {}
    task_kind_answer = answers.get("task_kind") or {}
    task_kind = task_kind_answer.get("choice") or "unclear"
    confidence = task_kind_answer.get("confidence", 0.0)
    margin = policy.compute_margin(task_kind_answer.get("probabilities"))
    prior_failed = (answers.get("states_prior_failed_attempts") or {}).get("noul", 0.0)

    verdict = policy.evaluate_tier(
        task_kind=task_kind,
        task_kind_confidence=confidence,
        task_kind_margin=margin,
        states_prior_failed_attempts=prior_failed,
        chosen_type=subagent_type,
    )
    usage = response.get("usage") or {}
    tokens = (usage.get("input_tokens") or 0) + (usage.get("output_tokens") or 0)
    return {
        "predicted_label": task_kind,
        "predicted_would_deny": verdict["would_deny"],
        "confidence": confidence,
        "margin": margin,
        "latency_ms": latency_ms,
        "tokens": tokens,
    }


def _judge_bash(payload):
    ti = payload.get("tool_input") or {}
    command = str(ti.get("command") or "")
    description = str(ti.get("description") or "")
    cwd = payload.get("cwd") or ""

    scope_result = scope_mod.classify_command(command, cwd)
    has_graph = scope_mod.root_has_graphify_graph(scope_result)

    state = questions.bash_state(command, description, cwd, scope_result["scope"], has_graph)
    qs = questions.bash_questions()
    response, latency_ms = client.ask({"state": state, "model": client.MODEL, "questions": qs})

    answers = response.get("answers") or {}
    intent_answer = answers.get("search_intent") or {}
    search_intent = intent_answer.get("choice") or "unclear"
    confidence = intent_answer.get("confidence", 0.0)
    margin = policy.compute_margin(intent_answer.get("probabilities"))

    verdict = policy.evaluate_search(
        scope=scope_result["scope"],
        search_intent=search_intent,
        confidence=confidence,
        command=command,
        root_has_graphify_graph=has_graph,
        margin=margin,
    )
    usage = response.get("usage") or {}
    tokens = (usage.get("input_tokens") or 0) + (usage.get("output_tokens") or 0)
    return {
        "predicted_label": search_intent,
        "predicted_would_deny": verdict["would_deny"],
        "confidence": confidence,
        "margin": margin,
        "latency_ms": latency_ms,
        "tokens": tokens,
        "scope": scope_result["scope"],
    }


def _ask_jev(rule, ctx, match):
    state, qs = rule.questions(ctx, match)
    response, _latency = client.ask({"state": state, "model": client.MODEL, "questions": qs})
    return (response or {}).get("answers") or {}


def _judge_rules(payload, rule_id):
    """Run the rules table over one payload (real Jev for any rule whose code
    pre-filter says the fuzzy half is in doubt) and report what `rule_id` did."""
    tool_name = payload.get("tool_name") or ""
    # A case may ship a tiny transcript fixture for the questions that read
    # the user's own recent words. Relative paths resolve against the repo, so
    # nothing in eval/cases.jsonl is tied to a particular machine.
    payload = dict(payload)
    tp = payload.get("transcript_path")
    if tp and not Path(tp).is_absolute():
        payload["transcript_path"] = str(REPO_ROOT / tp)
    ctx = rules_mod.build_ctx(payload, tool_name)
    rows = rules_mod.dry_run(ctx, ask=_ask_jev, overrides={})
    mine = [r for r in rows if r["rule_id"] == rule_id]
    other = sorted({r["rule_id"] for r in rows if r["rule_id"] != rule_id and r.get("fires")})
    row = mine[0] if mine else None
    # A rule whose Jev call failed is an ERROR, not a quiet "did not fire" --
    # reporting it as a wrong answer hides a broken question set behind a
    # plausible-looking accuracy number.
    if row and row.get("error"):
        raise RuntimeError(row["error"])
    return {
        "predicted_label": (row or {}).get("action") if row and row.get("fires") else "no_fire",
        "predicted_would_deny": bool(row and row.get("fires") and row.get("action") == "deny"),
        "predicted_fires": bool(row and row.get("fires")),
        "predicted_action": (row or {}).get("action"),
        "asked": bool(row and row.get("asked")),
        "confidence": (row or {}).get("confidence"),
        "margin": (row or {}).get("margin"),
        "latency_ms": 0,
        "tokens": 0,
        "also_fired": other,
    }


def run_case(case):
    guard = case.get("guard")
    payload = case.get("payload") or {}
    expected = case.get("expected") or {}

    try:
        if guard == "tier_guard":
            judged = _judge_tier(payload)
            expected_label = expected.get("task_kind")
        elif guard == "tool_choice_guard":
            judged = _judge_bash(payload)
            expected_label = expected.get("search_intent")
        elif guard == "rules":
            rule_id = expected.get("rule_id") or case.get("rule_id")
            judged = _judge_rules(payload, rule_id)
            expected_label = expected.get("action") if expected.get("fires") else "no_fire"
        else:
            return {"id": case.get("id"), "guard": guard, "error": "unknown guard"}
    except Exception as exc:
        return {"id": case.get("id"), "guard": guard, "error": str(exc)}

    if guard == "rules":
        expected_deny = bool(expected.get("fires") and expected.get("action") == "deny")
    else:
        expected_deny = bool(expected.get("would_deny"))
    result = {
        "id": case.get("id"),
        "guard": ("rules:%s" % (expected.get("rule_id") or case.get("rule_id"))) if guard == "rules" else guard,
        "expected_label": expected_label,
        "predicted_label": judged["predicted_label"],
        "label_correct": expected_label is None or expected_label == judged["predicted_label"],
        "expected_would_deny": expected_deny,
        "predicted_would_deny": judged["predicted_would_deny"],
        "deny_correct": expected_deny == judged["predicted_would_deny"],
        "confidence": judged.get("confidence"),
        "margin": judged.get("margin"),
        "latency_ms": judged.get("latency_ms"),
        "tokens": judged.get("tokens", 0),
    }
    return result


def run_eval(cases, max_workers=MAX_WORKERS):
    results = []
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        for result in pool.map(run_case, cases):
            results.append(result)
    return results


def summarize(results):
    by_guard = {}
    for r in results:
        guard = r.get("guard") or "unknown"
        stats = by_guard.setdefault(guard, {
            "total": 0, "errors": 0, "label_correct": 0,
            "false_deny": 0, "missed_deny": 0,
            "confusion": {}, "latencies": [], "tokens": 0,
        })
        stats["total"] += 1
        if "error" in r:
            stats["errors"] += 1
            continue
        if r["label_correct"]:
            stats["label_correct"] += 1
        key = "%s->%s" % (r["expected_label"], r["predicted_label"])
        stats["confusion"][key] = stats["confusion"].get(key, 0) + 1
        if r["predicted_would_deny"] and not r["expected_would_deny"]:
            stats["false_deny"] += 1
        if r["expected_would_deny"] and not r["predicted_would_deny"]:
            stats["missed_deny"] += 1
        if isinstance(r.get("latency_ms"), (int, float)):
            stats["latencies"].append(r["latency_ms"])
        stats["tokens"] += r.get("tokens", 0)

    summary = {}
    for guard, stats in by_guard.items():
        judged = stats["total"] - stats["errors"]
        accuracy = (stats["label_correct"] / judged) if judged else 0.0
        mean_latency = statistics.mean(stats["latencies"]) if stats["latencies"] else 0.0
        summary[guard] = {
            "total": stats["total"],
            "judged": judged,
            "errors": stats["errors"],
            "accuracy": accuracy,
            "false_deny": stats["false_deny"],
            "missed_deny": stats["missed_deny"],
            "confusion": stats["confusion"],
            "mean_latency_ms": mean_latency,
            "total_tokens": stats["tokens"],
        }
    return summary


def print_summary(summary):
    print("airlock eval results")
    print("cases file: %s" % CASES_FILE)
    print()
    for guard, s in sorted(summary.items()):
        print("== %s ==" % guard)
        print("  total=%d judged=%d errors=%d" % (s["total"], s["judged"], s["errors"]))
        print("  accuracy=%.1f%%" % (s["accuracy"] * 100.0))
        print("  false_deny=%d missed_deny=%d" % (s["false_deny"], s["missed_deny"]))
        print("  mean_latency_ms=%.0f total_tokens=%d" % (s["mean_latency_ms"], s["total_tokens"]))
        print("  confusion (expected->predicted : count):")
        for key, count in sorted(s["confusion"].items(), key=lambda kv: -kv[1]):
            print("    %-40s %d" % (key, count))
        print()


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true", help="also write eval/last_result.json")
    parser.add_argument("--cases", default=None, help="override cases file path")
    parser.add_argument("--max-workers", type=int, default=MAX_WORKERS)
    args = parser.parse_args(argv)

    cases = load_cases(args.cases)
    if not cases:
        print("No cases found at %s" % (args.cases or CASES_FILE))
        return 0

    results = run_eval(cases, max_workers=args.max_workers)
    summary = summarize(results)
    print_summary(summary)

    if args.json:
        RESULT_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(RESULT_FILE, "w") as f:
            json.dump({"summary": summary, "results": results}, f, indent=2, default=str)
        print("wrote %s" % RESULT_FILE)

    return 0


if __name__ == "__main__":
    sys.exit(main())
