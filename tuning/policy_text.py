#!/usr/bin/env python3
"""The guard's REAL policy, rendered for the judge -- generated from the code.

Why this module exists
----------------------
The first unattended tuning run judged 20 shadow rows and called 17 of them
wrong (error_rate 0.85) against a guard that scores 95-98% on its labelled
eval. Part of the cause was a judge prompt that described a policy the guard
had stopped having: a hand-written paragraph that still said an over-tier was
simply a "deny" and that scope "only affects the would_deny policy
downstream". By then the tier guard had three live outcomes (block, warn,
silence), the search guard denied on exactly two code-computed combinations,
and below-bar silence had become correct behaviour rather than a miss.

A hand-kept copy of a policy drifts. So nothing here is hand-kept: every
threshold, rung, option name and outcome below is produced by *running* the
real policy functions in `airlock.policy` over the real option lists in
`airlock.questions` and the real ladder in `airlock.tiers`, and printing what
they actually return. Change a constant in `airlock/` and this text changes
with it on the next run; `tests/test_tune_judge_policy.py` fails if the two
ever come apart.

Nothing in here imports anything the guard does not already import, does any
I/O, or reads a log row. It is pure text generation over pure functions.
"""
import hashlib
import json

from airlock import policy, questions, tiers

try:  # pragma: no cover - only if the tree is broken
    from airlock.scope import _SCOPE_RANK as _SCOPE_RANK
except Exception:  # pragma: no cover
    _SCOPE_RANK = {"unknown": 0, "stdin": 1, "single_dir": 2, "single_repo": 2, "disk_wide": 3}

# A command that is search-like, is NOT already an indexed search, and does
# not itself decide anything. Used only as the third argument to
# evaluate_search while probing the policy grid.
_PROBE_COMMAND = "find / -name foo"

# Confidence/margin pairs used to probe both sides of the shared deny bar.
_ABOVE_BAR = (1.0, 1.0)
_BELOW_BAR = (policy.CONFIDENCE_THRESHOLD - 0.1, policy.MARGIN_THRESHOLD - 0.1)


def scope_names():
    """The scope values airlock/scope.py can produce, widest last."""
    return [name for name, _ in sorted(_SCOPE_RANK.items(), key=lambda kv: (kv[1], kv[0]))]


def task_kind_options():
    try:
        return list(questions.tier_questions()["task_kind"]["criteria"].keys())
    except Exception:  # pragma: no cover - only if questions.py is broken
        return list(policy.TASK_KIND_ADEQUATE_RUNG.keys()) + ["unclear"]


def search_intent_options():
    try:
        return list(questions.bash_questions()["search_intent"]["criteria"].keys())
    except Exception:  # pragma: no cover
        return ["filename_search", "code_structure_search", "literal_text_search",
                "not_a_search", "unclear"]


# --- probing the live tier policy -------------------------------------------


def _tier_outcome(task_kind, chosen_type, confidence, margin, prior_failed=1.0):
    """What the live policy DOES for one hypothetical Agent dispatch:
    "block", "rewrite", "warn" or "allow (silent)"."""
    verdict = policy.evaluate_tier(task_kind, confidence, prior_failed, chosen_type,
                                   task_kind_margin=margin)
    entry = policy.tier_entry_fields(verdict, task_kind, confidence, margin,
                                     prior_failed, chosen_type)
    surface = policy.tier_surface(entry, rewrite_on=False)
    # tier_entry_fields does not carry adequate_rung; the verdict does.
    merged = dict(entry)
    merged.setdefault("adequate_rung", verdict.get("adequate_rung"))
    merged["adequate_rung"] = verdict.get("adequate_rung")
    merged["under_tiered"] = verdict.get("under_tiered")
    return surface or "allow (silent)", merged


def min_blocking_rung_gap():
    """The smallest rung gap that the live policy actually BLOCKS on, found by
    asking it rather than by copying the `>= 2` out of enforce_deny_tier."""
    rungs = tiers.rung_names()
    for gap in range(1, len(rungs)):
        for task_kind, adequate in policy.TASK_KIND_ADEQUATE_RUNG.items():
            index = tiers.rung_index()
            if adequate not in index:
                continue
            chosen_idx = index[adequate] + gap
            if chosen_idx >= len(rungs):
                continue
            chosen = rungs[chosen_idx]
            if chosen == "fable":
                continue  # the fable rule would block for a different reason
            outcome, _ = _tier_outcome(task_kind, chosen, *_ABOVE_BAR)
            if outcome == "block":
                return gap
    return None


def min_warning_rung_gap():
    """Smallest rung gap that produces a warn (non-blocking) outcome."""
    rungs = tiers.rung_names()
    index = tiers.rung_index()
    for gap in range(1, len(rungs)):
        for task_kind, adequate in policy.TASK_KIND_ADEQUATE_RUNG.items():
            if adequate not in index:
                continue
            chosen_idx = index[adequate] + gap
            if chosen_idx >= len(rungs):
                continue
            chosen = rungs[chosen_idx]
            if chosen == "fable":
                continue
            outcome, _ = _tier_outcome(task_kind, chosen, *_ABOVE_BAR)
            if outcome == "warn":
                return gap
    return None


def below_bar_outcomes():
    """The distinct outcomes the tier policy produces BELOW the shared bar,
    with a stated prior failure so the fable rule stays out of the way."""
    out = set()
    for task_kind in task_kind_options():
        for chosen in tiers.rung_names():
            outcome, _ = _tier_outcome(task_kind, chosen, *_BELOW_BAR)
            out.add(outcome)
    return out


def tier_grid():
    """One row per (task_kind, chosen rung): what the guard really does."""
    rows = []
    rungs = tiers.rung_names()
    for task_kind in task_kind_options():
        for chosen in rungs:
            outcome, entry = _tier_outcome(task_kind, chosen, *_ABOVE_BAR)
            rows.append({
                "task_kind": task_kind,
                "chosen": chosen,
                "adequate": entry.get("adequate_rung"),
                "rung_diff": entry.get("rung_diff"),
                "outcome": outcome,
                "under_tiered": bool(entry.get("under_tiered")),
            })
    return rows


def search_deny_combinations():
    """Every (scope, search_intent, graph-present) triple the live search
    policy actually denies on, above the bar. Everything absent allows."""
    out = []
    for scope in scope_names():
        for intent in search_intent_options():
            for graph in (False, True):
                verdict = policy.evaluate_search(
                    scope, intent, _ABOVE_BAR[0], _PROBE_COMMAND, graph,
                    margin=_ABOVE_BAR[1],
                )
                if verdict.get("would_deny"):
                    out.append({"scope": scope, "search_intent": intent,
                                "graph_present": graph})
    return out


# --- the rendered text ------------------------------------------------------


def _fmt(value):
    if isinstance(value, float):
        text = ("%.4f" % value).rstrip("0").rstrip(".")
        return text or "0"
    return str(value)


def tier_policy_text():
    rungs = tiers.rung_names()
    block_gap = min_blocking_rung_gap()
    warn_gap = min_warning_rung_gap()
    adequate_lines = "\n".join(
        "    %s -> %s" % (kind, policy.TASK_KIND_ADEQUATE_RUNG[kind])
        for kind in task_kind_options()
        if kind in policy.TASK_KIND_ADEQUATE_RUNG
    )
    unclear = [k for k in task_kind_options() if k not in policy.TASK_KIND_ADEQUATE_RUNG]
    grid_lines = "\n".join(
        "    task_kind=%s chosen=%s (adequate=%s, gap=%s) -> %s"
        % (r["task_kind"], r["chosen"], r["adequate"], r["rung_diff"], r["outcome"])
        for r in tier_grid()
    )
    return """GUARD: tier_guard (the Agent tool). Jev answers task_kind; the ACTION is
decided in code from that answer.

  Ladder, cheapest first: %(rungs)s
  Cheapest adequate rung per task_kind:
%(adequate)s
  task_kind values with no adequate rung at all (never a basis for any
  action, whatever the confidence): %(unclear)s

  Shared deny bar, applied before any action: confidence >= %(conf)s AND
  margin (top probability minus runner-up) >= %(margin)s. Below either
  number the guard stays SILENT and allows the call. That silence is
  CORRECT behaviour, not a miss.

  Three live outcomes, and nothing else:
    block           a gap of %(block_gap)s rungs or more above adequate, above the bar;
                    OR chosen == "fable" with no stated prior failed attempt
                    (that one ignores the margin, the answer carries none).
    warn            a gap of exactly %(warn_gap)s rung above adequate, above the bar. The
                    call is ALLOWED and runs unchanged; the model is told
                    what would have been adequate.
    allow (silent)  everything else, including every below-bar case and every
                    under-tiered call. UNDER-TIERING IS NEVER A DENY: choosing
                    something cheaper than adequate is logged and allowed.

  The full grid the code actually produces, above the bar, with a prior
  failed attempt stated (so the fable rule is not what is being shown):
%(grid)s

  The SAME grid at confidence %(low_conf)s / margin %(low_margin)s (below the bar) is
  "allow (silent)" in every single cell except the fable-without-stated-
  prior-failure block, which does not consult the bar at all. Verified by
  running the policy: %(low_outcomes)s.
""" % {
        "rungs": ", ".join(rungs),
        "adequate": adequate_lines,
        "unclear": ", ".join(unclear) or "(none)",
        "conf": _fmt(policy.CONFIDENCE_THRESHOLD),
        "margin": _fmt(policy.MARGIN_THRESHOLD),
        "block_gap": _fmt(block_gap),
        "warn_gap": _fmt(warn_gap),
        "grid": grid_lines,
        "low_conf": _fmt(_BELOW_BAR[0]),
        "low_margin": _fmt(_BELOW_BAR[1]),
        "low_outcomes": ", ".join(sorted(below_bar_outcomes())) or "(none)",
    }


def search_policy_text():
    combos = search_deny_combinations()
    if combos:
        combo_lines = "\n".join(
            "    scope=%s search_intent=%s graphify-graph-present=%s"
            % (c["scope"], c["search_intent"], c["graph_present"])
            for c in combos
        )
    else:  # pragma: no cover - only if every deny branch is removed
        combo_lines = "    (none -- this guard currently cannot deny at all)"
    return """GUARD: tool_choice_guard (Bash search commands). Jev answers search_intent
ONLY. Scope is computed in code by airlock/scope.py and is given to you as
ground truth on the row: treat it as a fact, never re-derive or argue with it.

  Scope values: %(scopes)s
  search_intent options: %(intents)s

  Same shared bar: confidence >= %(conf)s AND margin >= %(margin)s. Below it
  the guard allows silently, and that is CORRECT.

  Above the bar, the code denies on exactly these combinations and no others:
%(combos)s

  Every combination NOT in that list ALLOWS, whatever Jev answered. A command
  that already uses the indexed search tool (plocate/locate on Linux, es.exe
  on Windows) allows too, whatever the intent.

  There is no "warn" for this guard: it denies with a suggested replacement
  command, or it is silent.
""" % {
        "scopes": ", ".join(scope_names()),
        "intents": ", ".join(search_intent_options()),
        "conf": _fmt(policy.CONFIDENCE_THRESHOLD),
        "margin": _fmt(policy.MARGIN_THRESHOLD),
        "combos": combo_lines,
    }


SHARED_PREAMBLE = """How to read a row, and what is NOT a mistake
-------------------------------------------

Fail-open is the design. Every guard allows when it cannot decide, when the
answer is below the bar, when a budget expires, or when anything errors. None
of those is a wrong decision and none of them is a missed deny.

The `action` field on a row is the RULE's configured action (what this rule
would do if it fired), not what happened to this call. What happened is
`would_deny` / `enforced` / `warned` / `skipped`. A row reading
action="deny", would_deny=false, enforced=false was ALLOWED. Judge what
happened, never the `action` string.

`skipped` says Jev was never asked at all. "no_deny_possible" means the code
already knew, from scope/program/rung alone, that no answer could reach a
deny, so the call was skipped to save latency -- that is a designed
optimisation, and such a row contains no Jev answer to be right or wrong
about. Answer "cannot_tell" for it.
"""


def policy_text():
    """The whole per-guard policy block that goes into the judge prompt."""
    return "\n".join([
        "THE GUARD'S REAL POLICY (generated from the running code, not prose)",
        "===================================================================",
        "",
        SHARED_PREAMBLE,
        "",
        tier_policy_text(),
        "",
        search_policy_text(),
    ]).strip()


# --- drift detection --------------------------------------------------------
#
# policy_text() is generated, so it cannot drift on its own. What CAN drift is
# the surrounding hand-written prose in tune.py's prompt (and in SHARED_PREAMBLE
# above) once somebody changes a policy constant. The fingerprint below is over
# the constants only; tests/test_tune_judge_policy.py pins it, so changing a
# threshold, a rung, an option or an outcome fails that test and forces whoever
# changed it to re-read the prompt before re-pinning.

def policy_constants():
    """Everything the prompt asserts about the guard, as plain data."""
    return {
        "confidence_threshold": policy.CONFIDENCE_THRESHOLD,
        "margin_threshold": policy.MARGIN_THRESHOLD,
        "rungs": tiers.rung_names(),
        "adequate_rung": dict(sorted(policy.TASK_KIND_ADEQUATE_RUNG.items())),
        "task_kind_options": sorted(task_kind_options()),
        "search_intent_options": sorted(search_intent_options()),
        "scopes": sorted(scope_names()),
        "min_blocking_rung_gap": min_blocking_rung_gap(),
        "min_warning_rung_gap": min_warning_rung_gap(),
        "tier_grid": tier_grid(),
        "search_deny_combinations": search_deny_combinations(),
    }


def policy_fingerprint():
    blob = json.dumps(policy_constants(), sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


# Pinned by tests/test_tune_judge_policy.py. Regenerate with
#   python3 -c "from tuning import policy_text as p; print(p.policy_fingerprint())"
# and ONLY after checking that the prompt still describes what the code does.
POLICY_FINGERPRINT = "8d26c479f028cb01"


if __name__ == "__main__":  # pragma: no cover - a human aid
    print(policy_text())
    print()
    print("fingerprint: %s" % policy_fingerprint())
