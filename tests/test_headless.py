"""R6 is off by default everywhere, and a headless machine turns it on.

Two things are proved here, neither needing a machine of the relevant kind:
the detection function (every input injected), and the one-entry merge into
rules.json (including the case that matters most -- a user's explicit R6 value
is never overwritten).
"""

import tests  # noqa: F401 -- MUST be the first import; see test_compat_shims.py

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from airlock import headless, rules

REPO_ROOT = Path(__file__).resolve().parent.parent

LINUX_HEADLESS = {}  # no DISPLAY, no WAYLAND_DISPLAY, no WSL markers


def detect(env=None, system="Linux", proc_version="Linux version 6.8.0", loginctl=lambda: None):
    return headless.detect_headless(env=dict(env or {}), system=system,
                                    proc_version=proc_version, loginctl=loginctl)


class TestR6DefaultIsOff(unittest.TestCase):
    def test_the_shipped_default_is_off(self):
        self.assertEqual(rules.RULES_BY_ID["R6-gui-or-browser"].action, "off")

    def test_the_headless_action_constant_matches_what_the_rule_accepts(self):
        self.assertIn(headless.R6_HEADLESS_ACTION, rules.VALID_ACTIONS)
        self.assertIn(headless.R6_RULE_ID, rules.RULES_BY_ID)


class TestHeadlessDetection(unittest.TestCase):
    """Conservative: every uncertainty resolves to "this box has a desktop"."""

    def test_a_bare_linux_server_is_headless(self):
        hl, why = detect(LINUX_HEADLESS)
        self.assertTrue(hl, why)

    def test_display_means_desktop(self):
        for var in ("DISPLAY", "WAYLAND_DISPLAY"):
            hl, why = detect({var: ":0"})
            self.assertFalse(hl, why)
            self.assertIn(var, why)

    def test_an_empty_display_is_not_a_display(self):
        hl, _ = detect({"DISPLAY": "", "WAYLAND_DISPLAY": "   "})
        self.assertTrue(hl)

    def test_xdg_session_type_means_desktop(self):
        for value in ("x11", "wayland", "Wayland"):
            hl, why = detect({"XDG_SESSION_TYPE": value})
            self.assertFalse(hl, why)
        hl, _ = detect({"XDG_SESSION_TYPE": "tty"})
        self.assertTrue(hl)

    def test_wsl_is_never_headless(self):
        hl, why = detect({"WSL_DISTRO_NAME": "Ubuntu"})
        self.assertFalse(hl)
        self.assertIn("WSL", why)
        hl, why = detect(LINUX_HEADLESS,
                         proc_version="Linux version 5.15.0-microsoft-standard-WSL2")
        self.assertFalse(hl)
        self.assertIn("WSL", why)

    def test_non_linux_is_never_headless(self):
        # None is not in this list on purpose: None means "ask
        # platform.system()", not "unknown platform".
        for system in ("Windows", "Darwin", "FreeBSD", ""):
            hl, why = detect(LINUX_HEADLESS, system=system)
            self.assertFalse(hl, system)
            self.assertIn("not Linux", why)

    def test_loginctl_reporting_a_seat_means_desktop(self):
        hl, why = detect(LINUX_HEADLESS,
                         loginctl=lambda: "   3 1000 jonathan seat0 tty2\n")
        self.assertFalse(hl)
        self.assertIn("loginctl", why)

    def test_loginctl_reporting_a_graphical_session_means_desktop(self):
        def show(_sid):
            return "Type=wayland\nClass=user\nSeat=\n"
        self.assertTrue(headless.loginctl_says_desktop("7 1000 jonathan\n", show=show))

    def test_loginctl_with_only_remote_ssh_sessions_stays_headless(self):
        def show(_sid):
            return "Type=tty\nClass=user\nSeat=\n"
        self.assertFalse(headless.loginctl_says_desktop("12 1000 jonathan\n", show=show))

    def test_no_loginctl_at_all_is_not_evidence_of_a_desktop(self):
        hl, why = detect(LINUX_HEADLESS, loginctl=lambda: None)
        self.assertTrue(hl)
        self.assertIn("loginctl unavailable", why)

    def test_a_loginctl_that_explodes_does_not_break_detection(self):
        def boom():
            raise OSError("no dbus")
        hl, why = detect(LINUX_HEADLESS, loginctl=boom)
        self.assertTrue(hl, why)

    def test_empty_loginctl_output_is_no_evidence_either_way(self):
        self.assertFalse(headless.loginctl_says_desktop(""))
        self.assertFalse(headless.loginctl_says_desktop(None))

    def test_it_never_raises_whatever_it_is_given(self):
        hl, why = headless.detect_headless(env={"DISPLAY": object()}, system="Linux",
                                           proc_version="", loginctl=lambda: None)
        self.assertFalse(hl)
        self.assertIn("detection failed", why)


class TestMergeIntoRulesJson(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = os.path.join(self._tmp.name, "config", "rules.json")

    def _read(self):
        with open(self.path) as f:
            return json.load(f)

    def test_it_creates_the_file_when_there_is_none(self):
        status, detail = headless.merge_rule(self.path)
        self.assertEqual(status, "written")
        self.assertEqual(detail, "")  # nothing to back up
        self.assertEqual(self._read(), {"R6-gui-or-browser": "deny"})

    def test_the_new_file_and_the_live_rules_table_agree(self):
        headless.merge_rule(self.path)
        self.assertEqual(rules.load_action_overrides(self.path),
                         {"R6-gui-or-browser": "deny"})

    def test_it_merges_and_keeps_every_other_entry(self):
        os.makedirs(os.path.dirname(self.path))
        with open(self.path, "w") as f:
            json.dump({"R3-whole-test-suite": "off", "R7-destructive": "log"}, f)
        status, backup = headless.merge_rule(self.path)
        self.assertEqual(status, "written")
        self.assertEqual(self._read(), {"R3-whole-test-suite": "off",
                                        "R7-destructive": "log",
                                        "R6-gui-or-browser": "deny"})
        # and the original is still on disk, untouched
        self.assertTrue(os.path.exists(backup), backup)
        with open(backup) as f:
            self.assertNotIn("R6", f.read())

    def test_an_explicit_value_always_wins_including_off(self):
        os.makedirs(os.path.dirname(self.path))
        for existing in ("off", "warn", "deny", "log"):
            with self.subTest(existing=existing):
                with open(self.path, "w") as f:
                    json.dump({"R6-gui-or-browser": existing}, f)
                with open(self.path) as f:
                    before = f.read()
                status, detail = headless.merge_rule(self.path)
                self.assertEqual(status, "already")
                self.assertIn(existing, detail)
                with open(self.path) as f:
                    self.assertEqual(f.read(), before)

    def test_the_nested_rules_shape_is_preserved(self):
        os.makedirs(os.path.dirname(self.path))
        with open(self.path, "w") as f:
            json.dump({"rules": {"R5-sudo": "log"}, "note": "hand written"}, f)
        self.assertEqual(headless.merge_rule(self.path)[0], "written")
        self.assertEqual(self._read(), {"rules": {"R5-sudo": "log",
                                                  "R6-gui-or-browser": "deny"},
                                        "note": "hand written"})

    def test_an_explicit_value_in_the_nested_shape_also_wins(self):
        os.makedirs(os.path.dirname(self.path))
        with open(self.path, "w") as f:
            json.dump({"rules": {"R6-gui-or-browser": "off"}}, f)
        self.assertEqual(headless.merge_rule(self.path)[0], "already")

    def test_a_file_that_is_not_json_is_never_rewritten(self):
        os.makedirs(os.path.dirname(self.path))
        with open(self.path, "w") as f:
            f.write("this is not json\n")
        status, detail = headless.merge_rule(self.path)
        self.assertEqual(status, "error")
        self.assertIn("not valid JSON", detail)
        with open(self.path) as f:
            self.assertEqual(f.read(), "this is not json\n")

    def test_a_json_non_object_is_never_rewritten(self):
        os.makedirs(os.path.dirname(self.path))
        with open(self.path, "w") as f:
            f.write("[1, 2, 3]")
        self.assertEqual(headless.merge_rule(self.path)[0], "error")

    def test_an_empty_file_is_treated_as_an_empty_object(self):
        os.makedirs(os.path.dirname(self.path))
        open(self.path, "w").close()
        self.assertEqual(headless.merge_rule(self.path)[0], "written")
        self.assertEqual(self._read(), {"R6-gui-or-browser": "deny"})

    def test_merging_twice_is_idempotent(self):
        self.assertEqual(headless.merge_rule(self.path)[0], "written")
        self.assertEqual(headless.merge_rule(self.path)[0], "already")


class TestTheHookActuallyDeniesOnceMerged(unittest.TestCase):
    """End to end: merge the entry, run the real hook, get a deny."""

    def test_off_then_merge_then_deny(self):
        with tempfile.TemporaryDirectory() as home:
            payload = json.dumps({"session_id": "headless-e2e", "cwd": "/tmp",
                                  "tool_name": "Bash",
                                  "tool_input": {"command": "xdg-open https://example.com"}})
            env = dict(os.environ)
            for var in ("AIRLOCK_CONFIG_DIR", "PLUMBLINE_CONFIG_DIR",
                        "JEV_GUARD_CONFIG_DIR", "AIRLOCK_STATE_DIR",
                        "PLUMBLINE_STATE_DIR", "JEV_GUARD_STATE_DIR",
                        "AIRLOCK_HOME", "PLUMBLINE_HOME", "JEV_HOME",
                        "AIRLOCK_DISABLE", "PLUMBLINE_DISABLE", "JEV_GUARD_DISABLE"):
                env.pop(var, None)
            env["HOME"] = home
            env["AIRLOCK_MODE"] = "enforce"
            hook = str(REPO_ROOT / "hooks" / "airlock.py")

            first = subprocess.run([sys.executable, hook], input=payload,
                                   capture_output=True, text=True, env=env, timeout=30)
            self.assertEqual(first.stdout, "",
                             "R6 is off by default: the hook must say nothing")

            status, _ = headless.merge_rule(os.path.join(home, ".config", "airlock",
                                                         "rules.json"))
            self.assertEqual(status, "written")

            second = subprocess.run([sys.executable, hook], input=payload,
                                    capture_output=True, text=True, env=env, timeout=30)
            out = json.loads(second.stdout)["hookSpecificOutput"]
            self.assertEqual(out["permissionDecision"], "deny")
            self.assertIn("Playwright", out["permissionDecisionReason"])


class TestTheModuleCli(unittest.TestCase):
    """install/install.sh drives `python3 -m airlock.headless`."""

    def _run(self, *args):
        return subprocess.run([sys.executable, "-m", "airlock.headless"] + list(args),
                              cwd=str(REPO_ROOT), capture_output=True, text=True,
                              timeout=30)

    def test_detect_exits_0_on_a_headless_box_and_1_on_a_desktop(self):
        env_headless = dict(os.environ)
        env_headless.pop("DISPLAY", None)
        env_headless.pop("WAYLAND_DISPLAY", None)
        proc = subprocess.run([sys.executable, "-m", "airlock.headless", "detect"],
                              cwd=str(REPO_ROOT), capture_output=True, text=True,
                              env=env_headless, timeout=30)
        self.assertIn(proc.returncode, (0, 1))
        self.assertTrue(proc.stdout.strip(), "detect must print its reason")

        env_desktop = dict(env_headless)
        env_desktop["DISPLAY"] = ":0"
        proc = subprocess.run([sys.executable, "-m", "airlock.headless", "detect"],
                              cwd=str(REPO_ROOT), capture_output=True, text=True,
                              env=env_desktop, timeout=30)
        self.assertEqual(proc.returncode, 1)
        # Which reason wins depends on the host: a desktop Linux box says
        # DISPLAY, a WSL host says WSL first. Either is a correct "not headless".
        self.assertTrue("DISPLAY" in proc.stdout or "WSL" in proc.stdout, proc.stdout)

    def test_merge_prints_status_and_detail_tab_separated(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "rules.json")
            proc = self._run("merge", path)
            self.assertEqual(proc.returncode, 0)
            self.assertEqual(proc.stdout.split("\t")[0], "written")
            proc = self._run("merge", path)
            self.assertEqual(proc.stdout.split("\t")[0], "already")

    def test_an_unknown_subcommand_is_a_usage_error(self):
        self.assertEqual(self._run("wat").returncode, 2)


if __name__ == "__main__":
    unittest.main()
