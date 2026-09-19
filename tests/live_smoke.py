#!/usr/bin/env python3
"""Live smoke test for airlock -- makes REAL calls to TypeSafe.

NOT part of `python3 -m unittest discover -s tests` (that suite mocks the
HTTP call). Run this directly, with the API key loaded, to see real Jev
answers, would_deny verdicts and latency for four representative payloads:

  1. Bash:  `find / -name "*.xlsm"`               -> disk-wide filename search
  2. Bash:  `grep -rn "def main" .`                -> code-structure search,
                                                       with a fake graphify cwd
  3. Agent: a plain lookup dispatched to `fable`   -> tier mismatch
  4. Agent: a scoped feature dispatched to `workerS` -> correctly tiered

Usage:
    set -a; . ~/.config/airlock/env; set +a
    python3 tests/live_smoke.py
"""
import json
import os
import shutil
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from airlock import keyfile, policy, questions  # noqa: E402
from airlock import client as jclient  # noqa: E402


def _run_bash(label, command, cwd, fake_graphify=False):
    print("=== %s ===" % label)
    print("command: %s" % command)

    tmp_graph_dir = None
    if fake_graphify:
        tmp_graph_dir = tempfile.mkdtemp(prefix="airlock-smoke-graph-")
        os.makedirs(os.path.join(tmp_graph_dir, "graphify-out"), exist_ok=True)
        with open(os.path.join(tmp_graph_dir, "graphify-out", "graph.json"), "w") as f:
            f.write("{}")
        cwd = tmp_graph_dir

    try:
        if not policy.bash_is_search_like(command):
            print("prefilter: NOT search-like -- no API call would be made")
            return

        api_key = keyfile.get_api_key()
        if not api_key:
            print("NO API KEY FOUND -- cannot run live smoke test")
            sys.exit(1)

        is_git = policy.cwd_is_git_repo(cwd)
        has_graph = policy.cwd_has_graphify_graph(cwd)
        state = questions.bash_state(command, "", cwd, is_git, has_graph)
        qs = questions.bash_questions()

        start = time.monotonic()
        result, latency_ms = jclient.call_jev(api_key, state, qs)
        wall_ms = (time.monotonic() - start) * 1000

        answers = result.get("answers") or {}
        search_kind_answer = answers.get("search_kind") or {}
        verdict = policy.evaluate_search(
            search_kind=search_kind_answer.get("choice"),
            confidence=search_kind_answer.get("confidence", 0.0),
            command=command,
            cwd_has_graphify_graph_flag=has_graph,
        )

        print("cwd_has_graphify_graph: %s" % has_graph)
        print("answers: %s" % json.dumps(answers, indent=2))
        print("would_deny: %s  suggestion: %s" % (verdict["would_deny"], verdict["suggestion"]))
        print("latency_ms (client-measured): %d  wall_ms: %.0f" % (latency_ms, wall_ms))
        print()
    finally:
        if tmp_graph_dir:
            shutil.rmtree(tmp_graph_dir, ignore_errors=True)


def _run_agent(label, subagent_type, description, prompt):
    print("=== %s ===" % label)
    print("subagent_type: %s" % subagent_type)

    api_key = keyfile.get_api_key()
    if not api_key:
        print("NO API KEY FOUND -- cannot run live smoke test")
        sys.exit(1)

    state = questions.tier_state(subagent_type, "", description, prompt)
    qs = questions.tier_questions()

    start = time.monotonic()
    result, latency_ms = jclient.call_jev(api_key, state, qs)
    wall_ms = (time.monotonic() - start) * 1000

    answers = result.get("answers") or {}
    task_kind_answer = answers.get("task_kind") or {}
    prior_failed = (answers.get("states_prior_failed_attempts") or {}).get("noul", 0.0)
    verdict = policy.evaluate_tier(
        task_kind=task_kind_answer.get("choice"),
        task_kind_confidence=task_kind_answer.get("confidence", 0.0),
        states_prior_failed_attempts=prior_failed,
        chosen_type=subagent_type,
    )

    print("answers: %s" % json.dumps(answers, indent=2))
    print("would_deny: %s  suggested_agent: %s" % (verdict["would_deny"], verdict["suggested_agent"]))
    print("latency_ms (client-measured): %d  wall_ms: %.0f" % (latency_ms, wall_ms))
    print()


def main():
    if not keyfile.get_api_key():
        print("TYPESAFE_API_KEY not found in env or ~/.config/airlock/env; aborting live smoke test.")
        sys.exit(1)

    _run_bash(
        "Bash: disk-wide filename search",
        'find / -name "*.xlsm"',
        cwd="/",
    )

    _run_bash(
        "Bash: code-structure search with fake graphify cwd",
        'grep -rn "def main" .',
        cwd=None,
        fake_graphify=True,
    )

    _run_agent(
        "Agent: plain lookup dispatched to fable (expect would_deny)",
        subagent_type="fable",
        description="Find where the retry logic lives",
        prompt="Where in this codebase is the HTTP retry/backoff logic defined? Just tell me the file and line.",
    )

    _run_agent(
        "Agent: scoped feature dispatched to workerS (expect no deny)",
        subagent_type="workerS",
        description="Add input validation to the signup form",
        prompt=(
            "In src/forms/signup.ts, add validation for the email and password "
            "fields per the spec in docs/signup-validation.md: email must match "
            "RFC 5322 basics, password must be 12+ chars with one digit. Add "
            "unit tests in tests/forms/signup.test.ts covering both fields. "
            "Verify with `npm test -- signup`."
        ),
    )


if __name__ == "__main__":
    main()
