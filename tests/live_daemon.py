#!/usr/bin/env python3
"""Live smoke test for the airlock daemon -- makes REAL calls to TypeSafe.

NOT part of `python3 -m unittest discover -s tests`. Starts the daemon as a
subprocess against a private, temporary XDG_RUNTIME_DIR (so it never touches
a real deployed instance), makes 5 sequential calls through client.ask(),
prints each call's latency and reused_connection flag, then kills the daemon
and shows that a 6th call still succeeds -- falling back to the direct HTTPS
path.

Usage:
    set -a; . ~/.config/airlock/env; set +a
    python3 tests/live_daemon.py
"""
import os
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from airlock import client, keyfile, questions  # noqa: E402


def main():
    api_key = keyfile.get_api_key()
    if not api_key:
        print("No TYPESAFE_API_KEY found (load ~/.config/airlock/env first). Aborting.")
        return 1

    tmpdir = tempfile.mkdtemp(prefix="airlock-live-daemon-")
    env = dict(os.environ)
    env["XDG_RUNTIME_DIR"] = tmpdir

    proc = subprocess.Popen(
        [sys.executable, "-m", "airlock.daemon"],
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )

    sock_path = os.path.join(tmpdir, "jev", "jev.sock")
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and not os.path.exists(sock_path):
        time.sleep(0.05)
    if not os.path.exists(sock_path):
        print("Daemon socket never appeared at %s" % sock_path)
        proc.terminate()
        return 1

    os.environ["XDG_RUNTIME_DIR"] = tmpdir

    body = {
        "state": questions.tier_state("workerS", "", "live daemon smoke test", "trivial prompt"),
        "model": client.MODEL,
        "questions": questions.tier_questions(),
    }

    print("=== 5 sequential calls through client.ask() (daemon up) ===")
    for i in range(5):
        wall_start = time.monotonic()
        # Use the internal daemon path directly so we can report the
        # reused_connection flag too (ask()'s public 2-tuple return matches
        # call_jev's shape and drops it).
        via_daemon = client._ask_via_daemon(body, 5)
        wall_ms = int((time.monotonic() - wall_start) * 1000)
        if via_daemon is None:
            print("call %d: FELL BACK (daemon path returned None) -- unexpected while daemon is up" % (i + 1))
            continue
        result, latency_ms, reused = via_daemon
        print("call %d: upstream_latency_ms=%s wall_clock_ms=%s reused_connection=%s"
              % (i + 1, latency_ms, wall_ms, reused))

    print()
    print("=== killing the daemon, 6th call must still succeed via fallback ===")
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)

    # give the OS a moment to actually remove/release the socket
    time.sleep(0.3)

    start = time.monotonic()
    result, latency_ms = client.ask(body, timeout_s=5)
    elapsed_ms = int((time.monotonic() - start) * 1000)
    print("call 6 (daemon dead): ok, latency_ms=%s wall_clock_ms=%s" % (latency_ms, elapsed_ms))
    print("answers keys: %s" % list((result.get("answers") or {}).keys()))

    return 0


if __name__ == "__main__":
    sys.exit(main())
