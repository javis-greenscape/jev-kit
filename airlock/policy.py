"""Policy tables: turn Jev's answers into a shadow would_deny verdict.

Pure functions, no network, no I/O beyond a cheap upward filesystem walk to
detect a git repo / graphify graph. This is what tests/test_policy.py exercises
directly, independent of the HTTP call.
"""
import os
import re
import shlex
from pathlib import Path

from . import tiers

CONFIDENCE_THRESHOLD = 0.8
MARGIN_THRESHOLD = 0.4


def compute_margin(probabilities):
    """Margin between the top probability and the runner-up, from a Choice
    answer's `probabilities` dict. Returns None if there aren't at least two
    options to compare (a deny can never require a margin that doesn't
    exist)."""
    if not probabilities:
        return None
    values = sorted(probabilities.values(), reverse=True)
    if len(values) < 2:
        return None
    return values[0] - values[1]


def meets_deny_bar(confidence, margin):
    """Shared deny gate for every guard: high confidence is not enough on its
    own -- the top option must also clearly beat the runner-up."""
    if confidence is None or confidence < CONFIDENCE_THRESHOLD:
        return False
    if margin is None or margin < MARGIN_THRESHOLD:
        return False
    return True

# --- Agent tier guard --------------------------------------------------------
#
# The ladder itself lives in airlock/tiers.py, which is the ONE place that
# knows which agent type names this machine uses and can be overridden per
# machine by ~/.config/airlock/tiers.json. The names below are the built-in
# default, re-exported so existing callers and tests keep working.

RUNG_ORDER = [rung[0] for rung in tiers.DEFAULT_LADDER]
RUNG_INDEX = {name: i for i, name in enumerate(RUNG_ORDER)}

AGENT_TYPE_RUNG = {
    name: rung[0] for rung in tiers.DEFAULT_LADDER for name in rung
}

# subagent_type prefixes treated as Opus-level "director" rung.
DIRECTOR_PREFIXES = tiers.DIRECTOR_PREFIXES

# Minimum adequate rung per task_kind. "unclear" is deliberately absent: it is
# never a basis for would_deny.
TASK_KIND_ADEQUATE_RUNG = {
    "lookup": "scout-find",
    "mechanical_edit": "scout",
    "scoped_implementation": "workerS",
    "judgement": "workerO",
    "hard_problem": "fable",
}


def rung_for_agent_type(subagent_type):
    return tiers.rung_for_agent_type(subagent_type)


def evaluate_tier(task_kind, task_kind_confidence, states_prior_failed_attempts, chosen_type, task_kind_margin=None):
    """Return the tier-guard verdict for one Agent tool call.

    would_deny: chosen rung is strictly higher than the adequate rung for the
    judged task_kind, AND task_kind clears the shared deny bar (confidence
    >= 0.8 AND margin >= 0.4 over the runner-up), AND task_kind != unclear.
    Also true when chosen_type == "fable" and states_prior_failed_attempts < 0.5
    (fable dispatched without stating a failed prior attempt) -- that rule
    uses a Noul answer, which carries no margin, so it is unaffected by the
    margin gate.

    under_tiered: chosen rung is strictly lower than adequate -- logged, never
    a deny.
    """
    chosen_rung = rung_for_agent_type(chosen_type)
    adequate_rung = TASK_KIND_ADEQUATE_RUNG.get(task_kind)
    index = tiers.rung_index()
    if adequate_rung is not None and (chosen_rung not in index or adequate_rung not in index):
        # A machine ladder that does not name this rung at all: no comparison
        # is possible, so nothing is over- or under-tiered. Fail open.
        adequate_rung = None

    would_deny = False
    under_tiered = False
    rung_diff = None

    if adequate_rung is not None:
        chosen_idx = index[chosen_rung]
        adequate_idx = index[adequate_rung]
        rung_diff = chosen_idx - adequate_idx
        if (
            chosen_idx > adequate_idx
            and task_kind != "unclear"
            and meets_deny_bar(task_kind_confidence, task_kind_margin)
        ):
            would_deny = True
        elif chosen_idx < adequate_idx:
            under_tiered = True

    if chosen_type == "fable" and (states_prior_failed_attempts or 0.0) < 0.5:
        would_deny = True

    return {
        "chosen_rung": chosen_rung,
        "adequate_rung": adequate_rung,
        "would_deny": would_deny,
        "under_tiered": under_tiered,
        "suggested_agent": adequate_rung,
        "rung_diff": rung_diff,
    }


def tier_entry_fields(verdict, task_kind, task_kind_confidence, margin,
                      prior_failed, chosen_type):
    """The decision-relevant half of a tier log row, in ONE place.

    `enforce_deny_tier`, `tier_rewrite_target` and `tier_surface` all read an
    entry dict rather than a verdict, so anything that wants to know what a
    judgement WOULD do -- the guard, and airlock/eval.py -- has to assemble the
    same dict. Assembling it twice is how the eval came to score a different
    thing from the hook, so it is assembled here and nowhere else.

    The caller adds whatever presentation and bookkeeping it needs on top
    (timestamps, `detail`, shadow sampling).
    """
    return {
        "would_deny": verdict["would_deny"],
        "suggestion": verdict.get("suggested_agent"),
        "under_tiered": verdict["under_tiered"],
        "margin": margin,
        "rung_diff": verdict.get("rung_diff"),
        "chosen_type": chosen_type,
        "task_kind": task_kind,
        "task_kind_confidence": task_kind_confidence,
        "prior_failed": prior_failed,
    }


# --- Enforce-mode deny gates (Part A, item 5) --------------------------------
#
# Shadow's would_deny stays the wider, always-logged signal above. Enforce
# mode only ever blocks a strict SUBSET of that: Bash blocks exactly when
# would_deny already fires (same confidence+margin bar, nothing new to add).
# Agent blocks only for fable-without-stated-prior-failure, or a rung gap of
# TWO OR MORE -- a one-rung gap is logged (would_deny) but never enforced,
# and under-tiering is never a basis for a deny.


def enforce_deny_search(search_entry):
    """Bash enforce-mode deny gate: identical to would_deny -- the shared
    confidence(>=0.8)/margin(>=0.4) bar already gates would_deny, so there is
    no additional condition to apply here."""
    return bool((search_entry or {}).get("would_deny"))


def enforce_deny_tier(tier_entry):
    """Agent enforce-mode deny gate: fable dispatched without a stated prior
    failed attempt, OR the chosen rung is at least two rungs above the
    adequate rung for the judged task_kind (same deny bar as would_deny)."""
    entry = tier_entry or {}
    if entry.get("chosen_type") == "fable" and (entry.get("prior_failed") or 0.0) < 0.5:
        return True
    rung_diff = entry.get("rung_diff")
    task_kind = entry.get("task_kind")
    if (
        rung_diff is not None
        and rung_diff >= 2
        and task_kind != "unclear"
        and meets_deny_bar(entry.get("task_kind_confidence"), entry.get("margin"))
    ):
        return True
    return False


# --- Agent tier guard: what to SURFACE when it does not block ----------------
#
# Before this, a one-rung overshoot was recorded (`would_deny: true`,
# `enforced: false`) and the model never heard a word about it: 20 of 27
# judged Agent calls on the live log were flagged over-tiered and not one of
# them was surfaced, which is how another session concluded that nothing
# intercepts the Agent tool at all. A guard nobody can see teaches nobody
# anything.
#
# Two non-blocking outcomes now exist:
#
#   warn     the default. The one-rung case that already clears the shared
#            deny bar (confidence >= 0.8, margin >= 0.4) comes back as
#            PreToolUse additionalContext saying what was chosen, what Jev
#            judged adequate, and which subagent_type to use instead. The
#            call is ALLOWED and runs unchanged. Below the bar: silence.
#
#   rewrite  opt-in, off by default (see rewrite_enabled). A stricter bar
#            (confidence >= 0.9, margin >= 0.5) and the hook edits
#            subagent_type to the adequate rung instead of only advising.
#
# Neither ever fires upward, and neither touches the fable-without-stated-
# prior-failure case, which stays a block.

REWRITE_CONFIDENCE_THRESHOLD = 0.9
REWRITE_MARGIN_THRESHOLD = 0.5
REWRITE_FLAG_FILE = "tier-rewrite"
REWRITE_ENV = "AIRLOCK_TIER_REWRITE"


def rewrite_enabled():
    """True only when the machine has explicitly opted in: env
    AIRLOCK_TIER_REWRITE=1, or ~/.config/airlock/tier-rewrite existing.
    Never raises -- any error means OFF."""
    try:
        if os.environ.get(REWRITE_ENV) == "1":
            return True
    except Exception:
        return False
    try:
        from . import paths
        return paths.config_file(REWRITE_FLAG_FILE).exists()
    except Exception:
        return False


def meets_rewrite_bar(confidence, margin):
    if confidence is None or confidence < REWRITE_CONFIDENCE_THRESHOLD:
        return False
    if margin is None or margin < REWRITE_MARGIN_THRESHOLD:
        return False
    return True


def tier_rewrite_target(tier_entry):
    """The subagent_type a rewrite would use, or None when this entry must not
    be rewritten. Refuses, in order:

      - fable dispatched WITH a stated prior failed attempt (the human has
        given the reason the ladder asks for; a downgrade would ignore it),
      - an entry that was not actually over-tiered (rung_diff < 1), an
        `unclear` task_kind, or one below the stricter rewrite bar,
      - a target rung with no dispatchable name on this machine's ladder, a
        target that is not in the ladder verbatim, or a target that is not
        strictly CHEAPER than what was chosen (never rewrite upward).
    """
    entry = tier_entry or {}
    chosen_type = entry.get("chosen_type") or ""
    if chosen_type == "fable" and (entry.get("prior_failed") or 0.0) >= 0.5:
        return None
    rung_diff = entry.get("rung_diff")
    if rung_diff is None or rung_diff < 1:
        return None
    if entry.get("task_kind") in (None, "unclear"):
        return None
    if not meets_rewrite_bar(entry.get("task_kind_confidence"), entry.get("margin")):
        return None

    target_rung = entry.get("suggestion")
    if not target_rung:
        return None
    target = tiers.dispatch_name_for_rung(target_rung)
    if not target or not tiers.is_known_agent_type(target):
        return None

    index = tiers.rung_index()
    chosen_rung = tiers.rung_for_agent_type(chosen_type)
    if chosen_rung not in index or target_rung not in index:
        return None
    if index[target_rung] >= index[chosen_rung]:
        return None
    return target


def tier_surface(tier_entry, rewrite_on=None):
    """What this Agent judgement should DO, beyond the log row.

    Returns "block", "rewrite", "warn" or None (stay silent). "block" keeps
    the existing enforce behaviour exactly; rewrite takes precedence over it
    only when rewrite mode is on and the entry qualifies, because editing the
    dispatch down a rung lets the work proceed where a block would not.
    """
    entry = tier_entry or {}
    if entry.get("error"):
        return None
    on = rewrite_enabled() if rewrite_on is None else bool(rewrite_on)
    if on and tier_rewrite_target(entry):
        return "rewrite"
    if enforce_deny_tier(entry):
        return "block"
    if entry.get("would_deny"):
        # One rung over, already past the shared deny bar: too small to block,
        # too common to keep hiding.
        return "warn"
    return None


# --- Bash tool-choice guard --------------------------------------------------

SEARCH_PROGRAMS = {"find", "fd", "fdfind", "grep", "egrep", "rg", "ag", "ack", "locate", "plocate", "tree", "du"}
_SEGMENT_SPLIT = re.compile(r"[;&|]+")
_SKIP_PREFIX_TOKENS = {"sudo", "nice", "time", "env"}


def bash_is_search_like(command):
    """Cheap code pre-filter: does this Bash command contain a search-like
    program as a command word in any of its segments? Only when this is true
    do we spend an API call on the tool-choice guard."""
    if not command:
        return False
    for segment in _SEGMENT_SPLIT.split(command):
        segment = segment.strip()
        if not segment:
            continue
        try:
            tokens = shlex.split(segment)
        except ValueError:
            tokens = segment.split()
        if not tokens:
            continue
        idx = 0
        while idx < len(tokens) and (
            "=" in tokens[idx] and not tokens[idx].startswith("-")
            or tokens[idx] in _SKIP_PREFIX_TOKENS
        ):
            idx += 1
        if idx >= len(tokens):
            continue
        prog = Path(tokens[idx]).name
        if prog in SEARCH_PROGRAMS:
            return True
        if prog == "ls" and any(t.startswith("-") and "R" in t for t in tokens[idx + 1:]):
            return True
    return False


def _find_upward(cwd, relative):
    if not cwd:
        return None
    try:
        p = Path(cwd).expanduser().resolve()
    except Exception:
        return None
    for parent in [p] + list(p.parents):
        try:
            if (parent / relative).exists():
                return parent
        except Exception:
            continue
    return None


def cwd_is_git_repo(cwd):
    return _find_upward(cwd, ".git") is not None


def cwd_has_graphify_graph(cwd):
    return _find_upward(cwd, "graphify-out/graph.json") is not None


PLOCATE_SUGGESTION = "plocate -d ~/.cache/plocate/home.db -i '<pattern>'"
GRAPHIFY_SUGGESTION = "graphify query"

_LOCATE_RE = re.compile(r"(?<![A-Za-z0-9_])(plocate|locate)(?![A-Za-z0-9_])")


def command_already_uses_locate(command):
    return bool(_LOCATE_RE.search(command or ""))


# --- scope x search_intent policy table -------------------------------------
#
# scope (disk_wide / single_repo / single_dir / stdin / unknown) is a fact
# computed in code by airlock/scope.py, never asked of Jev. search_intent
# (filename_search / code_structure_search / literal_text_search /
# not_a_search / unclear) is the one fuzzy question Jev still answers. This
# table is the only place the two combine into a verdict.


def evaluate_search(scope, search_intent, confidence, command, root_has_graphify_graph, margin=None):
    """Return the tool-choice-guard verdict for one Bash search command.

    would_deny (plocate suggestion): scope == disk_wide AND
    search_intent == filename_search AND the command doesn't already use
    plocate/locate, gated on the shared confidence+margin deny bar.

    would_deny (graphify suggestion): search_intent == code_structure_search
    AND the root already has a graphify graph, gated the same way.

    Everything else -- including every case where scope is single_repo,
    single_dir, stdin or unknown -- allows.
    """
    would_deny = False
    suggestion = None

    if meets_deny_bar(confidence, margin):
        if (
            scope == "disk_wide"
            and search_intent == "filename_search"
            and not command_already_uses_locate(command)
        ):
            would_deny = True
            suggestion = PLOCATE_SUGGESTION
        elif search_intent == "code_structure_search" and root_has_graphify_graph:
            would_deny = True
            suggestion = GRAPHIFY_SUGGESTION

    return {"would_deny": would_deny, "suggestion": suggestion}


# --- Skip table: only spend a Jev call when a deny is possible --------------
#
# The A/B bench (30 sessions) showed zero denies -- agents already pick the
# right tool almost every time, so most judgements are 100% wasted latency
# (~300ms each) for no chance of ever changing the outcome. Both deny
# policies above are gated on code-computed facts (scope, program, rung)
# BEFORE Jev is ever asked, so it is possible to know in advance, from those
# same facts, whether a deny is even reachable -- and skip the call entirely
# when it isn't. A random sample of skipped calls is still judged (never
# denying) so the tuning loop keeps seeing ordinary, ambient traffic instead
# of a corpus consisting only of already-flagged cases.

GREP_LIKE_PROGRAMS = {"grep", "egrep", "fgrep", "rg", "ag", "ack"}
SKIP_LOCATE_FAMILY = {"locate", "plocate"}
DEFAULT_SAMPLE_RATE = 0.05
SAMPLE_RATE_ENV = "AIRLOCK_SAMPLE_RATE"
SAMPLE_RATE_ENV_LEGACY = ("PLUMBLINE_SAMPLE_RATE", "JEV_GUARD_SAMPLE_RATE")


def deny_possible_bash(scope, program, root_has_graphify_graph):
    """True iff a Bash search-command judgement could possibly end in a deny,
    mirroring evaluate_search's own two deny branches:

    (a) scope is disk_wide and the program isn't already locate/plocate
        (the only way to reach the plocate-suggestion deny), or
    (b) the program is in the grep family (grep/egrep/fgrep/rg/ag/ack) AND
        the search root already has a graphify graph (the only way to reach
        the graphify-suggestion deny).

    Everything else -- single_repo/single_dir/stdin/unknown scope with no
    graph, or an already-locate command -- can never deny regardless of what
    Jev answers, so it is safe to skip the call."""
    if scope == "disk_wide" and program not in SKIP_LOCATE_FAMILY:
        return True
    if program in GREP_LIKE_PROGRAMS and root_has_graphify_graph:
        return True
    return False


def deny_possible_agent(subagent_type):
    """True iff an Agent dispatch could possibly be denied. The tier guard
    can only deny when the chosen rung is strictly above the adequate rung
    for the judged task_kind (or fable without a stated prior failure).
    scout-find is the cheapest rung in RUNG_ORDER, adequate for every
    task_kind in TASK_KIND_ADEQUATE_RUNG (including the cheapest,
    "lookup"), so nothing can ever be judged as needing something cheaper
    still -- a scout-find dispatch can never be over-tiered, and it is never
    "fable", so the stated-failure rule can't fire either. Everything else
    (scout and up) keeps at least one reachable deny path, so it still gets
    judged."""
    return rung_for_agent_type(subagent_type) != tiers.rung_names()[0]


def sample_rate():
    """AIRLOCK_SAMPLE_RATE, default 0.05. Never raises -- a bad value falls
    back to the default rather than breaking the skip decision."""
    try:
        from . import paths
        return float(paths.env(SAMPLE_RATE_ENV, *SAMPLE_RATE_ENV_LEGACY,
                              default=str(DEFAULT_SAMPLE_RATE)))
    except Exception:
        return DEFAULT_SAMPLE_RATE
