#!/usr/bin/env python3
"""Push one airlock health result to an Uptime-Kuma-style push monitor.

Invoked by monitoring/run_health_check.sh with the health JSON line on stdin
and AIRLOCK_KUMA_PUSH_URL already loaded into the environment (the same
redacted way airlock/keyfile.py loads TYPESAFE_API_KEY -- from
~/.config/jev-kit/env, never shelled out, never put on a command line).

Any service that accepts a GET carrying `status` (`up`/`down`) and `msg`
works here; Uptime Kuma is simply the one this was built against, and the
variable keeps its name because renaming it would break every machine that
already has one.

Silently does nothing if the env var isn't set -- a push monitor is optional
and has to be created by a person (see monitoring/README.md). Never prints
the push URL: it is a capability token (anyone who has it can push a fake
"up"), not just an address, so it gets the same treatment as a secret even
though it isn't one of the redact.py patterns.

THE TWO MODES, AND WHICH MACHINE WANTS WHICH
============================================

`AIRLOCK_KUMA_PUSH_MODE`, read from the same key/config file:

`heartbeat` (the DEFAULT, and unchanged from the original behaviour)
    Push on every health run. The monitor's own heartbeat interval is what
    catches a machine that has stopped pushing at all. Right for a SERVER,
    which is supposed to be up: silence there is a real fault and alerting
    on it is the point.

`explicit`
    Push `status=up` when healthy, and `status=down&msg=<which check failed>`
    the moment a fault is found -- the failure is stated rather than inferred
    from silence. Right for a WORKSTATION, where silence overwhelmingly means
    the machine is switched off. Give that monitor a very long heartbeat
    interval (days), so a laptop that spends a weekend closed never trips it.

THE LIMIT, STATED HONESTLY
==========================

Explicit mode cannot report a health timer that has itself died. Nothing
pushes, and with a multi-day heartbeat interval nothing notices -- which is
exactly the trade that stops a powered-off laptop alerting. The case is
covered instead by `hooks/airlock_session_check.py`, which tells the person
at session start when the health check has gone stale. The two are
complementary and neither replaces the other.

WHAT MAY GO IN `msg`
====================

A short description of the failing check, and nothing else. It goes over the
network to a service that logs it, so it is put through `airlock/redact.py`
first, has anything that looks like a home directory replaced with a
placeholder, and is capped. A key never reaches it; a path with a username
in it never reaches it.
"""
import json
import os
import re
import socket
import sys
import urllib.parse
import urllib.request

TIMEOUT_S = 5

MODE_HEARTBEAT = "heartbeat"
MODE_EXPLICIT = "explicit"
DEFAULT_MODE = MODE_HEARTBEAT
MODE_ENV = ("AIRLOCK_KUMA_PUSH_MODE", "GS_KUMA_AIRLOCK_PUSH_MODE")
URL_ENV = ("AIRLOCK_KUMA_PUSH_URL", "GS_KUMA_AIRLOCK_PUSH_URL", "GS_KUMA_JEV_PUSH_URL")

#: A push monitor's message field is a status line, not a log line.
MSG_MAX = 200

# A home directory carries a username, and a username is a person. These run
# BEFORE the length cap, so a truncated message cannot end up with half a
# username in it.
_HOME_PATTERNS = (
    re.compile(r"(?i)/(?:home|Users)/[^/\s\"']+"),
    re.compile(r"(?i)[A-Z]:\\+Users\\+[^\\\s\"']+"),
    re.compile(r"(?i)\\\\Users\\\\[^\\\s\"']+"),
)


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


def push_mode(env=None):
    """`heartbeat` (default) or `explicit`. An unrecognised value is the
    DEFAULT, never an error: a typo in a config file must not take the
    monitoring off a server that was working yesterday."""
    env = os.environ if env is None else env
    for name in MODE_ENV:
        value = (env.get(name) or "").strip().lower()
        if value in (MODE_HEARTBEAT, MODE_EXPLICIT):
            return value
    return DEFAULT_MODE


def _redact():
    """airlock.redact.redact, imported lazily and once.

    monitoring/ is a sibling of the package rather than inside it, so the repo
    root has to go on sys.path -- done here rather than at module scope so
    importing this file for a test never mutates sys.path as a side effect.
    """
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if root not in sys.path:
        sys.path.insert(0, root)
    from airlock.redact import redact
    return redact


def scrub(text):
    """Redact, de-identify, then cap -- in that order.

    Order matters. Capping first could leave the tail of a token or half a
    username in the message, and neither is something to put in a URL that
    goes to a third-party service.
    """
    if not text:
        return ""
    try:
        out = _redact()(str(text))
    except Exception:
        # Fail toward saying LESS, never toward shipping an unredacted string.
        return "airlock: a check failed (detail withheld)"
    for pattern in _HOME_PATTERNS:
        out = pattern.sub("<home>", out)
    out = " ".join(out.split())
    if len(out) > MSG_MAX:
        out = out[:MSG_MAX - 1].rstrip() + "…"
    return out


def failure_detail(result):
    """WHICH check failed, from the health row's own fields.

    Nothing is re-derived: `airlock.health` already decided, and a second
    opinion computed here could disagree with the JSON sitting in
    health.jsonl about the same run.
    """
    if not isinstance(result, dict):
        return "no health result"
    parts = []
    for key, label in (("daemon_ping", "daemon ping"),
                       ("daemon_ask", "daemon judgement"),
                       ("direct_ask", "direct HTTPS judgement")):
        check = result.get(key)
        if isinstance(check, dict) and check.get("ok") is False:
            reason = str(check.get("error") or "").strip()
            parts.append("%s failed%s" % (label, (": %s" % reason) if reason else ""))
    if result.get("key_loadable") is False:
        parts.append("no API key resolves")
    last_hour = result.get("last_hour")
    if isinstance(last_hour, dict):
        rate = last_hour.get("fail_open_rate")
        if isinstance(rate, (int, float)) and rate > 0.20:
            parts.append("fail-open rate %d%% in the last hour" % round(rate * 100))
    if not parts:
        parts.append("status %s, no failing check named in the row"
                     % (result.get("status") or "unknown"))
    return "; ".join(parts)


def build_push_url(base_url, status_word, ping_ms, mode=DEFAULT_MODE, result=None):
    """The full GET URL.

    In `heartbeat` mode the message is the one-word status, exactly as it has
    always been -- a server's monitor must not start seeing a different msg
    after an upgrade. In `explicit` mode a failure carries WHICH check failed,
    scrubbed and capped, because that is the whole reason to choose it.
    """
    kuma_status = "up" if status_word == "healthy" else "down"
    msg = "airlock: %s" % status_word
    if mode == MODE_EXPLICIT and kuma_status == "down":
        msg = scrub("airlock %s: %s" % (status_word, failure_detail(result)))
    params = {"status": kuma_status, "msg": msg}
    if isinstance(ping_ms, (int, float)):
        params["ping"] = str(int(ping_ms))
    sep = "&" if urllib.parse.urlsplit(base_url).query else "?"
    return base_url + sep + urllib.parse.urlencode(params)


def push(url, timeout_s=TIMEOUT_S):
    """GET the URL over IPv4. Returns True on a 2xx, False otherwise, and
    never raises: a monitoring push failing must never look like an airlock
    failure or change the caller's exit code."""
    orig_create_connection = socket.create_connection
    socket.create_connection = _create_connection_ipv4
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout_s):
            pass
        return True
    except Exception:
        return False
    finally:
        socket.create_connection = orig_create_connection


def main(argv=None, env=None, stdin=None):
    env = os.environ if env is None else env
    # The new name first, then the ones the monitor may originally have been
    # created under. Renaming the project must not silently stop the push.
    url = None
    for name in URL_ENV:
        url = url or env.get(name)
    if not url:
        return 0

    try:
        result = json.loads((stdin or sys.stdin).read())
    except Exception:
        result = {}

    status_word = result.get("status", "down") if isinstance(result, dict) else "down"
    ping_ms = (result.get("daemon_ask") or {}).get("latency_ms") if isinstance(result, dict) else None
    full_url = build_push_url(url, status_word, ping_ms,
                              mode=push_mode(env), result=result)
    push(full_url)
    return 0


if __name__ == "__main__":
    sys.exit(main())
