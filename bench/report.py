"""Markdown results table writer for bench/run.py. n is small (2-3 per arm
per task) -- report median and range, never a significance claim.
"""
import statistics
from collections import defaultdict


def _median_range(values):
    values = [v for v in values if v is not None]
    if not values:
        return "n/a"
    if len(values) == 1:
        return "%.3g" % values[0]
    return "%.3g (%.3g-%.3g)" % (statistics.median(values), min(values), max(values))


def _tokens_by_model(rows):
    totals = defaultdict(lambda: {"input": 0, "output": 0})
    for r in rows:
        mu = r.get("modelUsage") or {}
        for model, u in mu.items():
            if not isinstance(u, dict):
                continue
            totals[model]["input"] += u.get("inputTokens", u.get("input_tokens", 0)) or 0
            totals[model]["output"] += u.get("outputTokens", u.get("output_tokens", 0)) or 0
    return totals


def _what_changed(task_id, jev_rows, none_rows):
    jev_correct = sum(1 for r in jev_rows if r.get("correct"))
    none_correct = sum(1 for r in none_rows if r.get("correct"))
    jev_denies = sum(r.get("guard_denies", 0) for r in jev_rows)
    jev_cost = [r.get("total_cost_usd") for r in jev_rows if r.get("session_ok")]
    none_cost = [r.get("total_cost_usd") for r in none_rows if r.get("session_ok")]
    jev_wall = [r.get("wall_s") for r in jev_rows if r.get("session_ok")]
    none_wall = [r.get("wall_s") for r in none_rows if r.get("session_ok")]

    bits = []
    bits.append(
        "correctness %d/%d (jev) vs %d/%d (none)."
        % (jev_correct, len(jev_rows), none_correct, len(none_rows))
    )
    if jev_denies:
        bits.append("the guard actually denied %d call(s) in the jev arm." % jev_denies)
    else:
        bits.append("the guard never fired a deny in this task (nothing to block, or it allowed everything).")
    if jev_cost and none_cost:
        jm, nm = statistics.median(jev_cost), statistics.median(none_cost)
        if jm > nm * 1.1:
            bits.append("jev arm cost more (median $%.4f vs $%.4f) -- likely retry/override overhead." % (jm, nm))
        elif nm > jm * 1.1:
            bits.append("jev arm cost less (median $%.4f vs $%.4f)." % (jm, nm))
        else:
            bits.append("cost was about the same either way (median $%.4f vs $%.4f)." % (jm, nm))
    if jev_wall and none_wall:
        jw, nw = statistics.median(jev_wall), statistics.median(none_wall)
        if jw > nw * 1.1:
            bits.append("jev arm was slower (median %.1fs vs %.1fs)." % (jw, nw))
        elif nw > jw * 1.1:
            bits.append("jev arm was faster (median %.1fs vs %.1fs, noise at this n)." % (jw, nw))
        else:
            bits.append("wall time was about the same (median %.1fs vs %.1fs)." % (jw, nw))
    return " ".join(bits)


def write_markdown(results, tasks, path):
    by_task = defaultdict(list)
    for r in results:
        by_task[r["task_id"]].append(r)

    lines = []
    lines.append("# airlock enforce-mode A/B bench")
    lines.append("")
    lines.append("n is small per arm (2-3) -- medians and ranges only, no significance claims.")
    lines.append("")

    for task in tasks:
        tid = task["id"]
        rows = by_task.get(tid, [])
        jev_rows = [r for r in rows if r["arm"] == "jev"]
        none_rows = [r for r in rows if r["arm"] == "none"]

        lines.append("## %s: %s" % (tid, task.get("prompt") or task.get("prompt_template")))
        lines.append("")
        lines.append("| arm | n | wall time (s) | turns | total cost ($) | correct | denies | overrides |")
        lines.append("|---|---|---|---|---|---|---|---|")
        for arm, arm_rows in (("jev", jev_rows), ("none", none_rows)):
            ok_rows = [r for r in arm_rows if r.get("session_ok")]
            wall = _median_range([r.get("wall_s") for r in ok_rows])
            turns = _median_range([r.get("num_turns") for r in ok_rows])
            cost = _median_range([r.get("total_cost_usd") for r in ok_rows])
            correct_n = sum(1 for r in ok_rows if r.get("correct"))
            denies = sum(r.get("guard_denies", 0) for r in arm_rows)
            overrides = sum(r.get("guard_overrides", 0) for r in arm_rows)
            lines.append(
                "| %s | %d | %s | %s | %s | %d/%d | %d | %d |"
                % (arm, len(arm_rows), wall, turns, cost, correct_n, len(ok_rows), denies, overrides)
            )
        lines.append("")

        tokens = _tokens_by_model(rows)
        if tokens:
            lines.append("Tokens by model (all trials, both arms combined):")
            lines.append("")
            lines.append("| model | input | output |")
            lines.append("|---|---|---|")
            for model, t in sorted(tokens.items()):
                lines.append("| %s | %d | %d |" % (model, t["input"], t["output"]))
            lines.append("")

        lines.append("**What the guard changed:** %s" % _what_changed(tid, jev_rows, none_rows))
        lines.append("")

    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")
