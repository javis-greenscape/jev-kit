#!/usr/bin/env python3
"""One-off measurement: how long does the TypeSafe server keep an idle
connection open? Starts the daemon against a private XDG_RUNTIME_DIR, makes
one call to warm a connection, then waits 30s/60s/120s between successive
calls and records whether the daemon reports reused_connection=True or had
to reconnect after each idle window.

NOT part of any test suite -- prints output, exits. Takes ~3.5 minutes.

Usage:
    set -a; . ~/.config/jev-kit/env; set +a
    python3 tests/live_daemon_idle.py
"""
import os
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from airlock import client, keyfile, questions


def main():
    if not keyfile.get_api_key():
        print("No TYPESAFE_API_KEY found. Aborting.")
        return 1

    tmpdir = tempfile.mkdtemp(prefix="airlock-live-idle-")
    env = dict(os.environ)
    env["XDG_RUNTIME_DIR"] = tmpdir
    # Pool size 1: with the default pool of 2, sequential single-caller
    # requests round-robin across both slots, so consecutive calls in this
    # script don't actually hit the same connection and the idle timing gets
    # confounded with round-robin alternation. Pin to 1 slot to measure the
    # server's idle-close behaviour cleanly.
    env["AIRLOCK_DAEMON_POOL"] = "1"
    proc = subprocess.Popen(
        [sys.executable, "-m", "airlock.daemon"],
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    sock_path = os.path.join(tmpdir, "jev", "jev.sock")
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and not os.path.exists(sock_path):
        time.sleep(0.05)
    os.environ["XDG_RUNTIME_DIR"] = tmpdir

    body = {
        "state": questions.tier_state("workerS", "", "idle reuse measurement", "trivial prompt"),
        "model": client.MODEL,
        "questions": questions.tier_questions(),
    }

    print("warming connection...")
    via_daemon = client._ask_via_daemon(body, 5)
    print("warmup call: reused_connection=%s" % (via_daemon[2] if via_daemon else "FAILED"))

    for wait_s in (30, 60, 120):
        print("waiting %ds idle..." % wait_s)
        time.sleep(wait_s)
        via_daemon = client._ask_via_daemon(body, 5)
        if via_daemon is None:
            print("after %ds idle: call FAILED (fell back to None)" % wait_s)
            continue
        _response, latency_ms, reused = via_daemon
        print("after %ds idle: reused_connection=%s latency_ms=%s" % (wait_s, reused, latency_ms))

    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()

    return 0


if __name__ == "__main__":
    sys.exit(main())
