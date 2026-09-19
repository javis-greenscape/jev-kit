#!/usr/bin/env python3
"""python3 eval/ablation.py -- does `task_kind` move when only the
`subagent_type` changes?

jev-behavior-study's first finding (see docs/CREDITS.md) is that
a surface cue in the state can dominate the judgement: holding everything else
fixed and describing a journey as "a five-minute walk" took the model from
20/20 correct to 0/20. Our tier guard puts `subagent_type` into the state
alongside the prompt, so it has exactly the same shape of risk -- and the case
it would break is the one the guard exists for, a hard task sent to a cheap
agent.

The question's own `focus` field already says "judge the actual work being
asked for, not the subagent_type chosen". Finding (4) of the same study is that
generic instructions of that kind did not work. So: measure it.

Each group in eval/cases.jsonl tagged `ablation.varies == "subagent_type"`
holds one description and one prompt EXACTLY fixed and varies only the chosen
agent type across the whole ladder. If task_kind is a property of the prompt,
every row in a group must get the same answer.

Makes one real Jev call per case. Exit code is always 0: this is a measurement,
not a test.
"""
import argparse
import json
import statistics
import sys
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from airlock import client, policy, questions  # noqa: E402
from airlock.eval import load_cases  # noqa: E402

RESULT_FILE = REPO_ROOT / "eval" / "ablation_result.json"
MAX_WORKERS = 4


def judge(case, send_subagent_type=True):
    """One tier-guard judgement.

    `send_subagent_type=False` is the remedy the report names: stop putting the
    chosen type in the state at all, since policy.py already knows it in code
    and only ever needs it AFTER task_kind has been decided.
    """
    ti = case["payload"]["tool_input"]
    state = questions.tier_state(
        ti.get("subagent_type") if send_subagent_type else "",
        ti.get("model") or "",
        ti.get("description") or "",
        ti.get("prompt") or "",
    )
    response, latency_ms = client.ask(
        {"state": state, "model": client.MODEL, "questions": questions.tier_questions()}
    )
    answer = (response.get("answers") or {}).get("task_kind") or {}
    return {
        "id": case["id"],
        "group": case["ablation"]["group"],
        "subagent_type": ti.get("subagent_type"),
        "expected": (case.get("expected") or {}).get("task_kind"),
        "task_kind": answer.get("choice") or "unclear",
        "confidence": answer.get("confidence"),
        "margin": policy.compute_margin(answer.get("probabilities")),
        "latency_ms": latency_ms,
    }


def run(cases, send_subagent_type, max_workers=MAX_WORKERS):
    def one(case):
        try:
            return judge(case, send_subagent_type)
        except Exception as exc:
            return {"id": case["id"], "group": case["ablation"]["group"],
                    "subagent_type": (case["payload"]["tool_input"]).get("subagent_type"),
                    "error": str(exc)[:200]}

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        return list(pool.map(one, cases))


def analyse(rows):
    by_group = defaultdict(list)
    for row in rows:
        by_group[row["group"]].append(row)

    groups = {}
    unstable = 0
    for group, group_rows in sorted(by_group.items()):
        ok = [r for r in group_rows if "error" not in r]
        kinds = Counter(r["task_kind"] for r in ok)
        stable = len(kinds) <= 1
        if not stable:
            unstable += 1
        expected = ok[0]["expected"] if ok else None
        groups[group] = {
            "expected": expected,
            "n": len(ok),
            "errors": len(group_rows) - len(ok),
            "stable": stable,
            "distinct_answers": len(kinds),
            "answers": {r["subagent_type"]: r["task_kind"] for r in ok},
            "counts": dict(kinds),
            "correct": sum(1 for r in ok if r["task_kind"] == expected),
            "mean_confidence": (statistics.mean([r["confidence"] for r in ok
                                                 if isinstance(r.get("confidence"), (int, float))])
                                if ok else None),
        }

    judged = [r for r in rows if "error" not in r]
    return {
        "groups": groups,
        "total_cases": len(rows),
        "judged": len(judged),
        "errors": len(rows) - len(judged),
        "unstable_groups": unstable,
        "stable": unstable == 0,
        "accuracy": (sum(1 for r in judged if r["task_kind"] == r["expected"]) / len(judged)
                     if judged else 0.0),
    }


def print_summary(label, summary):
    print("== %s" % label)
    print("   judged %d, errors %d, label accuracy %.1f%%"
          % (summary["judged"], summary["errors"], summary["accuracy"] * 100.0))
    for group, g in sorted(summary["groups"].items()):
        flag = "STABLE  " if g["stable"] else "UNSTABLE"
        print("   %s %-10s expected=%-22s answers=%s"
              % (flag, group, g["expected"], json.dumps(g["counts"])))
        if not g["stable"]:
            for agent_type, kind in g["answers"].items():
                mark = " " if kind == g["expected"] else "*"
                print("            %s %-12s -> %s" % (mark, agent_type, kind))
    print("   %d of %d groups moved with subagent_type alone."
          % (summary["unstable_groups"], len(summary["groups"])))
    print()


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", default=None)
    parser.add_argument("--max-workers", type=int, default=MAX_WORKERS)
    parser.add_argument("--no-rerun", action="store_true",
                        help="skip the without-subagent_type arm even if the first arm is unstable")
    args = parser.parse_args(argv)

    cases = [c for c in load_cases(args.cases)
             if (c.get("ablation") or {}).get("varies") == "subagent_type"]
    if not cases:
        print("no subagent_type ablation cases found")
        return 0

    print("subagent_type ablation: %d cases, %d groups"
          % (len(cases), len({c["ablation"]["group"] for c in cases})))
    print("Each group holds description and prompt EXACTLY fixed and varies only")
    print("subagent_type. A group is STABLE if every row got the same task_kind.")
    print()

    baseline_rows = run(cases, send_subagent_type=True, max_workers=args.max_workers)
    baseline = analyse(baseline_rows)
    print_summary("arm A: subagent_type IS sent in the Jev state (shipping behaviour)", baseline)

    out = {"baseline": baseline, "baseline_rows": baseline_rows}

    if baseline["stable"]:
        print("VERDICT: task_kind did NOT change with subagent_type alone.")
        print("No change made: the state keeps subagent_type.")
    elif args.no_rerun:
        print("VERDICT: task_kind DID change with subagent_type alone, in %d group(s)."
              % baseline["unstable_groups"])
        print("--no-rerun given, so the remedy arm was not measured.")
    else:
        print("VERDICT: task_kind DID change with subagent_type alone, in %d group(s)."
              % baseline["unstable_groups"])
        print("Re-running with subagent_type removed from the state. policy.py already")
        print("knows the chosen type in code and only needs it after task_kind is decided,")
        print("so nothing downstream loses information.")
        print()
        remedy_rows = run(cases, send_subagent_type=False, max_workers=args.max_workers)
        remedy = analyse(remedy_rows)
        print_summary("arm B: subagent_type REMOVED from the Jev state", remedy)
        out["remedy"] = remedy
        out["remedy_rows"] = remedy_rows
        if remedy["stable"]:
            print("The remedy works: removing the cue made every group stable.")
        else:
            print("The remedy did NOT fully stabilise it (%d group(s) still move)."
                  % remedy["unstable_groups"])

    RESULT_FILE.write_text(json.dumps(out, indent=2, default=str))
    print()
    print("wrote %s" % RESULT_FILE)
    return 0


if __name__ == "__main__":
    sys.exit(main())
