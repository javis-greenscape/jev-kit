#!/usr/bin/env python3
"""Measure the PreToolUse hook's added wall-clock time, per call, as the CLI
actually pays it: a real `python3 hooks/airlock.py` process with the payload
on stdin.

Not part of `unittest discover`. Run it directly:

    python3 tests/latency_hook.py [iterations]

Three payload shapes:
  1. no rule matches      -- the common case; cost is Python start-up only
  2. code-only rule (deny) -- R6 (xdg-open): matches, decides in code, emits.
     R6 is off by default on every platform, so the temp HOME below gets a
     rules.json pinning it on; this measures the deny PATH, not the policy.
  3. code-only rule (warn) -- R3 (bare pytest): matches, decides in code

HOME is pointed at a temp dir and AIRLOCK_MODE is pinned to enforce, so the
run neither reads this box's real mode file nor writes to the real shadow log,
and TYPESAFE_API_KEY is stripped so nothing can reach the network.
"""
import json
import os
import statistics
import subprocess
import sys
import tempfile
import time

HOOK = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "hooks", "airlock.py")

PAYLOADS = {
    "no_rule_match": {"session_id": "lat", "cwd": "/tmp", "tool_name": "Bash",
                      "tool_input": {"command": "git status --porcelain", "description": "status"}},
    "no_rule_match_other_tool": {"session_id": "lat", "cwd": "/tmp", "tool_name": "Write",
                                 "tool_input": {"file_path": "/tmp/x.py", "content": "x = 1"}},
    "code_only_deny_R6": {"session_id": "lat", "cwd": "/tmp", "tool_name": "Bash",
                          "tool_input": {"command": "xdg-open https://example.com"}},
    "code_only_warn_R3": {"session_id": "lat", "cwd": "/tmp", "tool_name": "Bash",
                          "tool_input": {"command": "pytest"}},
}


def run_one(payload, env):
    raw = json.dumps(payload)
    start = time.monotonic()
    subprocess.run([sys.executable, HOOK], input=raw.encode(), stdout=subprocess.PIPE,
                   stderr=subprocess.DEVNULL, env=env)
    return (time.monotonic() - start) * 1000.0


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 200
    with tempfile.TemporaryDirectory() as home:
        env = dict(os.environ)
        env["HOME"] = home
        env["AIRLOCK_MODE"] = "enforce"
        cfg = os.path.join(home, ".config", "airlock")
        os.makedirs(cfg, exist_ok=True)
        with open(os.path.join(cfg, "rules.json"), "w") as f:
            f.write('{"R6-gui-or-browser": "deny"}\n')
        env.pop("TYPESAFE_API_KEY", None)
        env.pop("AIRLOCK_DISABLE", None)

        print("hook: %s" % HOOK)
        print("iterations per shape: %d" % n)
        for name, payload in PAYLOADS.items():
            run_one(payload, env)  # warm the bytecode cache
            samples = sorted(run_one(payload, env) for _ in range(n))
            print("%-26s median=%6.1fms  p95=%6.1fms  min=%6.1fms  max=%6.1fms"
                  % (name, statistics.median(samples), samples[int(0.95 * len(samples)) - 1],
                     samples[0], samples[-1]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
