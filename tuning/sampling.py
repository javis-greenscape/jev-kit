#!/usr/bin/env python3
"""Which shadow rows go to the judge, and what the resulting rate means.

The first unattended run took "the newest 20 new rows" and reported
error_rate 0.85 against them. Reconstructing that batch from the live log
showed what the number was really measuring: 18 of the 20 were
tool_choice_guard rows and 2 were plain rule rows, none of them was a
tier_guard row, and **17 of the 20 carried no Jev answer at all** -- 14 were
`skipped: no_deny_possible` (the code already knew no answer could reach a
deny, so Jev was never called), two were rule rows whose question is not in
the judge's rubric, and one was an override with no input summary. Asked "was
Jev's answer right" about rows in which Jev never answered, and given no way
to say otherwise, the judge said no. 17 of 20 is 0.85 exactly.

So two things are fixed here:

1. **Only judgeable rows are sampled.** A row is judgeable when it carries a
   Jev answer for a question the rubric still has. Everything else is skipped
   and COUNTED by reason, so a run reports how much of its corpus it had to
   drop and why -- including rows written by an older release whose question
   ids have since been renamed (`search_kind` predates `search_intent`; 34 of
   them are in the live log).

2. **The sample is documented and split.** Half the budget goes to the newest
   judgeable rows (the recency slice: what the guard is doing right now,
   which is what a tuning loop wants to react to) and half to a uniformly
   random draw over every judgeable row the run can see (the honest estimate:
   this one, and only this one, is an unbiased measure of overall accuracy).
   Each half reports its own rate, and neither is ever described as "the
   error rate" without naming the rule that produced it.
"""
import random

# Question ids the judge's rubric currently covers, mapped to the guard they
# belong to. A row is judgeable only if its `answers` carries one of these.
RUBRIC_QUESTIONS = {
    "task_kind": "tier_guard",
    "search_intent": "tool_choice_guard",
}

# Question ids written by older releases and since renamed. Rows carrying
# only these are skipped as `legacy_question`, never judged against a rubric
# that did not exist when they were written.
RETIRED_QUESTIONS = {"search_kind"}

# Fraction of the row budget spent on the newest rows; the rest is uniform.
RECENT_SHARE = 0.5

SKIP_NO_JEV_ANSWER = "no_jev_answer"
SKIP_LEGACY_QUESTION = "legacy_question"
SKIP_NO_RUBRIC = "no_rubric_for_question"
SKIP_MISSING_INPUT = "no_input_summary"

SAMPLE_RULE_RECENT = "recent"
SAMPLE_RULE_RANDOM = "uniform_random"

# Named once, quoted in the log and the README, so a rate is never reported
# without the rule that produced it.
SAMPLE_RULE_DESCRIPTIONS = {
    SAMPLE_RULE_RECENT: (
        "the newest judgeable rows since the last run (recency-biased: NOT an "
        "estimate of overall accuracy)"
    ),
    SAMPLE_RULE_RANDOM: (
        "a uniformly random draw over every judgeable row since the last run "
        "(an unbiased estimate of overall accuracy)"
    ),
}


def row_questions(row):
    answers = row.get("answers")
    if not isinstance(answers, dict):
        return set()
    return set(answers.keys())


def skip_reason(row):
    """Why this row cannot be judged, or None if it can be.

    Order matters: a row with no answers at all is reported as such even when
    its guard has no rubric, because "Jev was never asked" is the fact a human
    reading the run log needs first."""
    questions = row_questions(row)
    if not questions:
        return SKIP_NO_JEV_ANSWER
    if questions & set(RUBRIC_QUESTIONS):
        if not (row.get("input_summary") or {}):
            return SKIP_MISSING_INPUT
        return None
    if questions & RETIRED_QUESTIONS:
        return SKIP_LEGACY_QUESTION
    return SKIP_NO_RUBRIC


def judgeable(row):
    return skip_reason(row) is None


def partition(rows):
    """Split rows into (judgeable, {skip_reason: count})."""
    keep = []
    skipped = {}
    for row in rows:
        reason = skip_reason(row)
        if reason is None:
            keep.append(row)
        else:
            skipped[reason] = skipped.get(reason, 0) + 1
    return keep, skipped


def select(rows, budget, seed=None):
    """Choose up to `budget` rows to judge.

    Returns (selected_rows, report). Every selected row carries an added
    "_sample_rule" key naming which half drew it, so a verdict can be scored
    against the right denominator. The key is stripped before the row is sent
    to the judge (see tune.py) -- it is bookkeeping, not evidence.
    """
    keep, skipped = partition(rows)
    keep_sorted = sorted(keep, key=lambda r: str(r.get("ts", "")), reverse=True)

    if seed is None:
        seed = random.randrange(2 ** 32)
    rng = random.Random(seed)

    recent_budget = min(len(keep_sorted), max(1, int(round(budget * RECENT_SHARE))) if budget else 0)
    recent = keep_sorted[:recent_budget]
    recent_ids = {id(r) for r in recent}

    remaining = [r for r in keep_sorted if id(r) not in recent_ids]
    random_budget = max(0, budget - len(recent))
    if random_budget and remaining:
        random_rows = rng.sample(remaining, min(random_budget, len(remaining)))
    else:
        random_rows = []

    selected = []
    for row in recent:
        row = dict(row)
        row["_sample_rule"] = SAMPLE_RULE_RECENT
        selected.append(row)
    for row in random_rows:
        row = dict(row)
        row["_sample_rule"] = SAMPLE_RULE_RANDOM
        selected.append(row)

    report = {
        "considered": len(rows),
        "judgeable": len(keep),
        "skipped_by_reason": skipped,
        "skipped_total": sum(skipped.values()),
        "budget": budget,
        "sampled": len(selected),
        "sampled_recent": len(recent),
        "sampled_random": len(random_rows),
        "seed": seed,
        "rules": dict(SAMPLE_RULE_DESCRIPTIONS),
    }
    return selected, report


def rates(verdicts):
    """Error rates per sample rule plus an overall-of-sampled rate.

    `verdicts` is a list of dicts carrying "sample_rule" and "wrong" (bool) and
    "cannot_tell" (bool). A cannot_tell is never counted as wrong and never
    counted in a denominator: it is a row the judge declined, and folding it
    into either side is how a rate stops meaning anything.
    """
    buckets = {}
    for verdict in verdicts:
        if verdict.get("cannot_tell"):
            bucket = buckets.setdefault(verdict.get("sample_rule") or "unknown",
                                        {"judged": 0, "wrong": 0, "cannot_tell": 0})
            bucket["cannot_tell"] += 1
            continue
        bucket = buckets.setdefault(verdict.get("sample_rule") or "unknown",
                                    {"judged": 0, "wrong": 0, "cannot_tell": 0})
        bucket["judged"] += 1
        if verdict.get("wrong"):
            bucket["wrong"] += 1

    out = {}
    total_judged = total_wrong = total_cannot = 0
    for rule, bucket in buckets.items():
        total_judged += bucket["judged"]
        total_wrong += bucket["wrong"]
        total_cannot += bucket["cannot_tell"]
        out[rule] = {
            "judged": bucket["judged"],
            "wrong": bucket["wrong"],
            "cannot_tell": bucket["cannot_tell"],
            "error_rate": (bucket["wrong"] / bucket["judged"]) if bucket["judged"] else 0.0,
            "rule": SAMPLE_RULE_DESCRIPTIONS.get(rule, rule),
        }
    out["_all_sampled"] = {
        "judged": total_judged,
        "wrong": total_wrong,
        "cannot_tell": total_cannot,
        "error_rate": (total_wrong / total_judged) if total_judged else 0.0,
        "rule": "every row this run sampled, both halves together",
    }
    return out


def rate_sentence(report, rates_by_rule):
    """The one line a human reads. Never states a bare error rate."""
    parts = []
    for rule in (SAMPLE_RULE_RANDOM, SAMPLE_RULE_RECENT):
        bucket = rates_by_rule.get(rule)
        if not bucket or not bucket["judged"]:
            continue
        parts.append("%.0f%% wrong of the %d rows sampled by %s"
                     % (100 * bucket["error_rate"], bucket["judged"],
                        SAMPLE_RULE_DESCRIPTIONS.get(rule, rule)))
    skipped = report.get("skipped_by_reason") or {}
    skip_text = ", ".join("%s=%d" % (k, v) for k, v in sorted(skipped.items())) or "none"
    return "%s; skipped as not judgeable: %s (of %d rows considered, %d were judgeable)" % (
        "; ".join(parts) or "nothing judged",
        skip_text, report.get("considered", 0), report.get("judgeable", 0),
    )
