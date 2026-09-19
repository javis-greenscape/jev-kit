#!/usr/bin/env python3
"""Push one airlock health result to an Uptime Kuma push monitor.

Invoked by monitoring/run_health_check.sh with the health JSON line on
stdin and AIRLOCK_KUMA_PUSH_URL already loaded into the environment (the
same redacted way airlock/keyfile.py loads TYPESAFE_API_KEY -- from
~/.config/airlock/env, never shelled out, never put on a command line).

Silently does nothing if the env var isn't set -- Kuma is optional and the
monitor itself has to be created by a person (see monitoring/README.md).
Never prints the push URL: it is a capability token (anyone who has it can
push a fake "up"), not just an address, so it gets the same treatment as a
secret even though it isn't one of the redact.py patterns.
"""
import json
import os
import socket
import sys
import urllib.parse
import urllib.request

TIMEOUT_S = 5


def _create_connection_ipv4(address, timeout=socket._GLOBAL_DEFAULT_TIMEOUT, source_address=None):
    """Like socket.create_connection, but restricted to AF_INET -- same
    IPv4-only rule airlock/daemon.py applies to its TypeSafe connections."""
    host, port = address
    err = None
    for res in socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_STREAM):
        af, socktype, proto, _canonname, sa = res
        sock = None
        try:
            sock = socket.socket(af, socktype, proto)
            if timeout is not socket._GLOBAL_DEFAULT_TIMEOUT:
                sock.settimeout(timeout)
            if source_address:
                sock.bind(source_address)
            sock.connect(sa)
            return sock
        except OSError as exc:
            err = exc
            if sock is not None:
                sock.close()
    if err is not None:
        raise err
    raise OSError("getaddrinfo returned an empty list for %r" % (address,))


def build_push_url(base_url, status_word, ping_ms):
    kuma_status = "up" if status_word == "healthy" else "down"
    params = {"status": kuma_status, "msg": "airlock: %s" % status_word}
    if isinstance(ping_ms, (int, float)):
        params["ping"] = str(int(ping_ms))
    sep = "&" if urllib.parse.urlsplit(base_url).query else "?"
    return base_url + sep + urllib.parse.urlencode(params)


def main():
    # The new name first, then the one the monitor was originally created
    # under. Renaming the project must not silently stop the push.
    # Newest name first, then the two older prefixes, the same order every
    # other rename in this project uses -- a machine still setting an old
    # name keeps working untouched.
    url = (os.environ.get("AIRLOCK_KUMA_PUSH_URL")
           or os.environ.get("GS_KUMA_AIRLOCK_PUSH_URL")
           or os.environ.get("GS_KUMA_JEV_PUSH_URL"))
    if not url:
        return 0

    try:
        result = json.loads(sys.stdin.read())
    except Exception:
        result = {}

    status_word = result.get("status", "down")
    ping_ms = (result.get("daemon_ask") or {}).get("latency_ms")
    full_url = build_push_url(url, status_word, ping_ms)

    orig_create_connection = socket.create_connection
    socket.create_connection = _create_connection_ipv4
    try:
        req = urllib.request.Request(full_url, method="GET")
        with urllib.request.urlopen(req, timeout=TIMEOUT_S):
            pass
    except Exception:
        # A monitoring push failing must never look like a airlock failure
        # (and must never raise past this script into the caller's exit code).
        pass
    finally:
        socket.create_connection = orig_create_connection
    return 0


if __name__ == "__main__":
    sys.exit(main())
