"""monitoring/kuma_push.py: the two push modes, with the HTTP call mocked.

`heartbeat` is what a SERVER wants: push every run and let the monitor's own
silence timeout catch a machine that has stopped. `explicit` is what a
WORKSTATION wants: state the failure, so a laptop that spends the weekend
switched off never alerts on its own silence.

The two things worth being strict about:

  1. `heartbeat` must be BYTE-FOR-BYTE what it always was. Every server
     already running this has a monitor built around that exact message, and
     an upgrade that quietly changes it is an upgrade that breaks alerting.
  2. `msg` goes over the network to a third-party service that logs it, so it
     must never carry a key or a path with a username in it, and it must be
     capped.
"""

import tests  # noqa: F401, I001 -- MUST be the first import, see tests/__init__.py

import io
import sys
import unittest
import urllib.parse
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "monitoring"))
import kuma_push

PUSH_URL = "https://status.example/api/push/TOKEN"

HEALTHY = {"status": "healthy", "daemon_ask": {"latency_ms": 310},
           "key_loadable": True, "last_hour": {"fail_open_rate": 0.0}}
DOWN = {"status": "down", "key_loadable": True,
        "daemon_ping": {"ok": False, "error": "connection refused"},
        "daemon_ask": {"ok": False, "error": "skipped: daemon ping failed"},
        "last_hour": {"fail_open_rate": 0.0}}
DEGRADED_NO_KEY = {"status": "degraded", "key_loadable": False,
                   "last_hour": {"fail_open_rate": 0.0}}


def params(url):
    return dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))


class PushRecorder:
    """Stands in for the real GET. The point of mocking here is not speed --
    it is that a unit suite must never depend on, or touch, a network."""

    def __init__(self):
        self.urls = []

    def __call__(self, url, timeout_s=None):
        self.urls.append(url)
        return True


class PushTestBase(unittest.TestCase):
    def setUp(self):
        self.recorder = PushRecorder()
        self._real_push = kuma_push.push
        kuma_push.push = self.recorder
        self.addCleanup(setattr, kuma_push, "push", self._real_push)

    def _main(self, result, env):
        import json
        return kuma_push.main(env=env, stdin=io.StringIO(json.dumps(result)))


class TestModeResolution(unittest.TestCase):
    def test_the_default_is_heartbeat(self):
        self.assertEqual(kuma_push.push_mode({}), "heartbeat")

    def test_explicit_is_selected_by_name(self):
        self.assertEqual(
            kuma_push.push_mode({"AIRLOCK_KUMA_PUSH_MODE": "explicit"}), "explicit")

    def test_case_and_whitespace_do_not_matter(self):
        self.assertEqual(
            kuma_push.push_mode({"AIRLOCK_KUMA_PUSH_MODE": "  EXPLICIT \n"}),
            "explicit")

    def test_an_unrecognised_value_falls_back_to_the_default(self):
        # A typo in a config file must not take the monitoring off a server
        # that was working yesterday.
        self.assertEqual(
            kuma_push.push_mode({"AIRLOCK_KUMA_PUSH_MODE": "expilcit"}), "heartbeat")

    def test_the_older_variable_name_still_works(self):
        self.assertEqual(
            kuma_push.push_mode({"GS_KUMA_AIRLOCK_PUSH_MODE": "explicit"}), "explicit")


class TestHeartbeatModeIsUnchanged(PushTestBase):
    def test_healthy_pushes_up_with_the_original_message(self):
        self._main(HEALTHY, {"AIRLOCK_KUMA_PUSH_URL": PUSH_URL})
        p = params(self.recorder.urls[0])
        self.assertEqual(p["status"], "up")
        self.assertEqual(p["msg"], "airlock: healthy")
        self.assertEqual(p["ping"], "310")

    def test_down_pushes_down_with_the_original_message(self):
        self._main(DOWN, {"AIRLOCK_KUMA_PUSH_URL": PUSH_URL})
        p = params(self.recorder.urls[0])
        self.assertEqual(p["status"], "down")
        self.assertEqual(p["msg"], "airlock: down")

    def test_heartbeat_never_names_the_failing_check(self):
        # That is explicit mode's job, and a server's monitor is built around
        # the short message.
        self._main(DOWN, {"AIRLOCK_KUMA_PUSH_URL": PUSH_URL,
                          "AIRLOCK_KUMA_PUSH_MODE": "heartbeat"})
        self.assertNotIn("connection refused", params(self.recorder.urls[0])["msg"])


class TestExplicitMode(PushTestBase):
    ENV = {"AIRLOCK_KUMA_PUSH_URL": PUSH_URL,
           "AIRLOCK_KUMA_PUSH_MODE": "explicit"}

    def test_healthy_still_pushes_up(self):
        self._main(HEALTHY, self.ENV)
        p = params(self.recorder.urls[0])
        self.assertEqual(p["status"], "up")
        self.assertEqual(p["msg"], "airlock: healthy")

    def test_a_fault_pushes_down_and_names_the_check(self):
        self._main(DOWN, self.ENV)
        p = params(self.recorder.urls[0])
        self.assertEqual(p["status"], "down")
        self.assertIn("daemon ping failed", p["msg"])
        self.assertIn("connection refused", p["msg"])

    def test_a_missing_key_is_named(self):
        self._main(DEGRADED_NO_KEY, self.ENV)
        self.assertIn("no API key resolves", params(self.recorder.urls[0])["msg"])

    def test_a_high_fail_open_rate_is_named(self):
        result = {"status": "degraded", "key_loadable": True,
                  "last_hour": {"fail_open_rate": 0.37}}
        self._main(result, self.ENV)
        self.assertIn("37%", params(self.recorder.urls[0])["msg"])

    def test_a_fault_with_no_named_check_still_says_something(self):
        self._main({"status": "degraded"}, self.ENV)
        self.assertIn("no failing check named",
                      params(self.recorder.urls[0])["msg"])

    def test_the_direct_https_probe_is_named_on_a_windows_shaped_row(self):
        result = {"status": "degraded", "key_loadable": True,
                  "daemon_supported": False,
                  "direct_ask": {"ok": False, "error": "TLS handshake timed out"}}
        self._main(result, self.ENV)
        self.assertIn("direct HTTPS judgement failed",
                      params(self.recorder.urls[0])["msg"])


class TestTheMessageIsSafeToSend(unittest.TestCase):
    def test_a_key_is_redacted(self):
        result = {"status": "down", "key_loadable": True,
                  "daemon_ask": {"ok": False,
                                 "error": "auth failed for apikey_abcdef123456"}}
        url = kuma_push.build_push_url(PUSH_URL, "down", None,
                                       mode="explicit", result=result)
        self.assertNotIn("apikey_abcdef123456", url)
        self.assertIn("REDACTED", params(url)["msg"])

    def test_a_home_directory_path_is_replaced(self):
        result = {"status": "down", "key_loadable": True,
                  "daemon_ask": {"ok": False,
                                 "error": "cannot read /home/someperson/.config/x"}}
        msg = params(kuma_push.build_push_url(
            PUSH_URL, "down", None, mode="explicit", result=result))["msg"]
        self.assertNotIn("someperson", msg)
        self.assertIn("<home>", msg)

    def test_a_windows_profile_path_is_replaced(self):
        result = {"status": "down", "key_loadable": True,
                  "daemon_ask": {"ok": False,
                                 "error": "cannot read C:\\Users\\Someone\\AppData"}}
        msg = params(kuma_push.build_push_url(
            PUSH_URL, "down", None, mode="explicit", result=result))["msg"]
        self.assertNotIn("Someone", msg)
        self.assertIn("<home>", msg)

    def test_the_message_is_capped(self):
        result = {"status": "down", "key_loadable": True,
                  "daemon_ask": {"ok": False, "error": "x" * 5000}}
        msg = params(kuma_push.build_push_url(
            PUSH_URL, "down", None, mode="explicit", result=result))["msg"]
        self.assertLessEqual(len(msg), kuma_push.MSG_MAX)

    def test_scrub_fails_toward_saying_less(self):
        # If the redactor itself is unavailable, the message must shrink to
        # something safe rather than going out unredacted.
        real = kuma_push._redact
        kuma_push._redact = lambda: (_ for _ in ()).throw(ImportError("gone"))
        try:
            self.assertEqual(kuma_push.scrub("apikey_secret"),
                             "airlock: a check failed (detail withheld)")
        finally:
            kuma_push._redact = real


class TestUrlShape(unittest.TestCase):
    def test_a_url_that_already_has_a_query_gets_an_ampersand(self):
        url = kuma_push.build_push_url(PUSH_URL + "?x=1", "healthy", None)
        self.assertIn("?x=1&status=up", url)

    def test_a_url_with_no_query_gets_a_question_mark(self):
        url = kuma_push.build_push_url(PUSH_URL, "healthy", None)
        self.assertIn("TOKEN?status=up", url)

    def test_ping_is_omitted_when_there_is_no_latency(self):
        self.assertNotIn("ping=", kuma_push.build_push_url(PUSH_URL, "down", None))


class TestNoUrlMeansNoPush(PushTestBase):
    def test_nothing_is_pushed_when_the_variable_is_unset(self):
        self.assertEqual(self._main(HEALTHY, {}), 0)
        self.assertEqual(self.recorder.urls, [])

    def test_a_malformed_health_line_still_pushes_down_rather_than_crashing(self):
        rc = kuma_push.main(env={"AIRLOCK_KUMA_PUSH_URL": PUSH_URL},
                            stdin=io.StringIO("not json at all"))
        self.assertEqual(rc, 0)
        self.assertEqual(params(self.recorder.urls[0])["status"], "down")

    def test_the_url_is_never_printed(self):
        # It is a capability token: anyone holding it can push a fake "up".
        import subprocess
        proc = subprocess.run(
            [sys.executable, str(REPO_ROOT / "monitoring" / "kuma_push.py")],
            input='{"status": "healthy"}', text=True, capture_output=True,
            timeout=30, env={"PATH": "/usr/bin:/bin"})
        self.assertNotIn("TOKEN", proc.stdout + proc.stderr)


if __name__ == "__main__":
    unittest.main()
