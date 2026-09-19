"""The backward-compatibility shims left behind by two renames:
jev-guard -> plumbline -> airlock.

Both older names survive as shims -- the `plumbline` and `jev_guard` packages,
and the `hooks/plumbline.py` and `hooks/jev_guard.py` hook entry points. A
machine mid-cutover has a settings.json naming an old hook path and habits
naming an old package. All of them must keep working, and -- more important --
all of them must be the SAME objects as the new names, not a second copy that
can drift.
"""

import tests  # noqa: F401 -- MUST be the first import. `python3 -m unittest
# discover -s tests` runs with start_dir == top_level_dir, so unittest treats
# `tests/` as a flat directory of top-level modules and never executes
# tests/__init__.py as a package init (name == '.' in TestLoader._find_tests).
# Importing it explicitly, here, first, is what actually runs its HOME/
# AIRLOCK_*-isolating fixture before any airlock.* module resolves a real path.

import json
import os
import subprocess
import sys
import tempfile
import unittest

from tests import posix_only
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
NEW_HOOK = REPO_ROOT / "hooks" / "airlock.py"
MID_HOOK = REPO_ROOT / "hooks" / "plumbline.py"
OLD_HOOK = REPO_ROOT / "hooks" / "jev_guard.py"

DENY_PAYLOAD = {
    "session_id": "compat-shim-test",
    "cwd": "/tmp",
    "tool_name": "Bash",
    "tool_input": {"command": "xdg-open https://example.com"},
}


# These tests exist to prove that giving a subprocess ONLY a custom $HOME is
# enough for every config/state/key path to resolve underneath it (that's
# the whole point of test_kill_switch_file_is_honoured_under_every_config_dir
# below). tests/__init__.py pins AIRLOCK_CONFIG_DIR/STATE_DIR/HOME for THIS
# process so the suite never reads the real machine's config -- but that same
# pin, inherited by dict(os.environ), would just as effectively hide a
# subprocess's own `home` argument behind the parent's fixture directory.
# Every override var is stripped here so the subprocess falls back to the
# $HOME it was actually given, the same as a real machine with no override set.
_PATH_OVERRIDE_VARS = (
    "AIRLOCK_CONFIG_DIR", "PLUMBLINE_CONFIG_DIR", "JEV_GUARD_CONFIG_DIR",
    "AIRLOCK_STATE_DIR", "PLUMBLINE_STATE_DIR", "JEV_GUARD_STATE_DIR",
    "AIRLOCK_HOME", "PLUMBLINE_HOME", "JEV_HOME",
    "AIRLOCK_KEY_FILE", "PLUMBLINE_KEY_FILE", "JEV_GUARD_KEY_FILE",
)


def _run_hook(path, payload, home, session_id, extra_env=None):
    env = dict(os.environ)
    for var in _PATH_OVERRIDE_VARS:
        env.pop(var, None)
    env["HOME"] = home
    env["AIRLOCK_MODE"] = "enforce"
    # Loop protection would allow an identical repeat, so every call in these
    # tests gets its own session id.
    env.pop("AIRLOCK_DISABLE", None)
    env.pop("PLUMBLINE_DISABLE", None)
    env.pop("JEV_GUARD_DISABLE", None)
    if extra_env:
        env.update(extra_env)
    data = dict(payload)
    data["session_id"] = session_id
    proc = subprocess.run(
        [sys.executable, str(path)],
        input=json.dumps(data),
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )
    return proc


class TestPackageShims(unittest.TestCase):
    def test_submodules_are_the_same_objects(self):
        import airlock.enforce
        import airlock.rules
        import jev_guard.enforce  # noqa: F401
        import jev_guard.rules  # noqa: F401
        import plumbline.enforce  # noqa: F401
        import plumbline.rules  # noqa: F401

        self.assertIs(sys.modules["jev_guard.rules"], airlock.rules)
        self.assertIs(sys.modules["plumbline.rules"], airlock.rules)
        self.assertIs(sys.modules["jev_guard.enforce"], airlock.enforce)
        self.assertIs(sys.modules["plumbline.enforce"], airlock.enforce)

    def test_from_import_works_under_both_old_names(self):
        from airlock import policy as new_policy
        from jev_guard import policy as oldest_policy
        from plumbline import policy as mid_policy

        self.assertIs(oldest_policy, new_policy)
        self.assertIs(mid_policy, new_policy)

    def test_there_is_no_second_rules_table(self):
        import airlock.rules
        import jev_guard.rules
        import plumbline.rules

        self.assertIs(jev_guard.rules.RULES, airlock.rules.RULES)
        self.assertIs(plumbline.rules.RULES, airlock.rules.RULES)

    def test_every_shimmed_submodule_exists_in_airlock(self):
        import airlock
        import plumbline

        missing = []
        for name in plumbline.SUBMODULES:
            if not (Path(airlock.__file__).parent / ("%s.py" % name)).exists():
                missing.append(name)
        self.assertEqual(missing, [])


@posix_only("runs the hook through a POSIX shell with a throwaway HOME;\n            the Windows equivalent is install/windows_doctor.py")
class TestHookShims(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.home = self._tmp.name

    def test_old_hook_paths_still_deny(self):
        for label, hook in (("plumbline", MID_HOOK), ("jev_guard", OLD_HOOK)):
            with self.subTest(hook=label):
                proc = _run_hook(hook, DENY_PAYLOAD, self.home, "shim-%s" % label)
                self.assertEqual(proc.returncode, 0)
                out = json.loads(proc.stdout)["hookSpecificOutput"]
                self.assertEqual(out["permissionDecision"], "deny")

    def test_all_three_hook_paths_give_the_same_decision(self):
        new = _run_hook(NEW_HOOK, DENY_PAYLOAD, self.home, "shim-a")
        mid = _run_hook(MID_HOOK, DENY_PAYLOAD, self.home, "shim-b")
        old = _run_hook(OLD_HOOK, DENY_PAYLOAD, self.home, "shim-c")
        self.assertEqual(json.loads(new.stdout), json.loads(mid.stdout))
        self.assertEqual(json.loads(new.stdout), json.loads(old.stdout))

    def test_legacy_mode_env_vars_are_honoured(self):
        for var in ("PLUMBLINE_MODE", "JEV_GUARD_MODE"):
            with self.subTest(var=var):
                proc = _run_hook(NEW_HOOK, DENY_PAYLOAD, self.home, "shim-mode-%s" % var,
                                 extra_env={"AIRLOCK_MODE": "", var: "enforce"})
                out = json.loads(proc.stdout)["hookSpecificOutput"]
                self.assertEqual(out["permissionDecision"], "deny")

    def test_legacy_kill_switch_env_vars_are_honoured(self):
        for var in ("PLUMBLINE_DISABLE", "JEV_GUARD_DISABLE"):
            with self.subTest(var=var):
                proc = _run_hook(NEW_HOOK, DENY_PAYLOAD, self.home, "shim-kill-%s" % var,
                                 extra_env={var: "1"})
                self.assertEqual(proc.stdout, "")

    def test_kill_switch_file_is_honoured_under_every_config_dir(self):
        for app in ("airlock", "plumbline", "jev-guard"):
            with self.subTest(app=app), tempfile.TemporaryDirectory() as home:
                cfg = Path(home) / ".config" / app
                cfg.mkdir(parents=True)
                (cfg / "disabled").write_text("")
                proc = _run_hook(NEW_HOOK, DENY_PAYLOAD, home, "shim-kill-file-%s" % app)
                self.assertEqual(proc.stdout, "")

    def test_every_override_stamp_is_honoured(self):
        for stamp in ("airlock-ok", "plumbline-ok", "jev-ok"):
            with self.subTest(stamp=stamp):
                payload = json.loads(json.dumps(DENY_PAYLOAD))
                payload["tool_input"]["description"] = (
                    "checking the deny path [%s: deliberate test]" % stamp)
                proc = _run_hook(NEW_HOOK, payload, self.home, "shim-stamp-%s" % stamp)
                self.assertEqual(proc.stdout, "")


class MigrationTestBase(unittest.TestCase):
    SCRIPT = REPO_ROOT / "install" / "migrate-to-airlock.sh"

    def _run(self, home, *args, script=None):
        env = dict(os.environ)
        for var in _PATH_OVERRIDE_VARS:
            env.pop(var, None)
        env["HOME"] = home
        return subprocess.run(
            ["bash", str(script or self.SCRIPT), *args],
            capture_output=True, text=True, env=env, timeout=60,
        )

    def _live_layout(self, home):
        """Exactly what the live machine looks like: real `plumbline`
        directories with `jev-guard` symlinks pointing at them, mode=enforce,
        a rules.json and a shadow log."""
        home = Path(home)
        cfg = home / ".config" / "plumbline"
        cfg.mkdir(parents=True)
        (cfg / "mode").write_text("enforce\n")
        (cfg / "rules.json").write_text('{"R5-sudo": "warn"}')
        (home / ".config" / "jev-guard").symlink_to(cfg)

        state = home / ".local" / "state" / "plumbline"
        state.mkdir(parents=True)
        (state / "shadow.jsonl").write_text('{"a":1}\n')
        (home / ".local" / "state" / "jev-guard").symlink_to(state)

        share = home / ".local" / "share" / "plumbline"
        share.mkdir(parents=True)
        (share / "current").write_text("release-1\n")
        (home / ".local" / "share" / "jev-guard").symlink_to(share)
        return home


@posix_only("install/migrate-to-airlock.sh is bash, the layout it migrates is\n            the XDG one, and the fixture builds symlinks -- which an\n            unprivileged Windows user cannot create at all (WinError 1314)")
class TestMigrationScript(MigrationTestBase):
    def test_live_layout_migrates_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as home:
            self._live_layout(home)
            home = Path(home)

            first = self._run(str(home))
            self.assertEqual(first.returncode, 0, first.stdout + first.stderr)

            cfg = home / ".config" / "airlock"
            self.assertTrue(cfg.is_dir() and not cfg.is_symlink())
            self.assertEqual((cfg / "mode").read_text(), "enforce\n")
            self.assertEqual(
                (home / ".local" / "state" / "airlock" / "shadow.jsonl").read_text(),
                '{"a":1}\n')
            self.assertEqual(
                (home / ".local" / "share" / "airlock" / "current").read_text(),
                "release-1\n")

            # Both old names left behind as symlinks pointing at the new one.
            for parent, name in ((".config", "config"), (".local/state", "state"),
                                 (".local/share", "share")):
                base = home.joinpath(*parent.split("/"))
                for old in ("plumbline", "jev-guard"):
                    link = base / old
                    self.assertTrue(link.is_symlink(), "%s/%s not a symlink" % (name, old))
                    self.assertEqual(link.resolve(), (base / "airlock").resolve())

            second = self._run(str(home))
            self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
            self.assertEqual((cfg / "mode").read_text(), "enforce\n")

    def test_mode_resolves_to_enforce_before_and_after(self):
        """The expensive silent failure this whole fallback exists to stop."""
        from airlock import mode as mode_mod
        from airlock import paths
        from unittest import mock

        with tempfile.TemporaryDirectory() as home:
            self._live_layout(home)
            env = {k: v for k, v in os.environ.items()
                   if k not in ("AIRLOCK_MODE", "PLUMBLINE_MODE", "JEV_GUARD_MODE",
                                "AIRLOCK_CONFIG_DIR", "PLUMBLINE_CONFIG_DIR",
                                "JEV_GUARD_CONFIG_DIR")}
            env["HOME"] = home
            with mock.patch.dict(os.environ, env, clear=True):
                with mock.patch.object(mode_mod, "MODE_FILE", None):
                    def resolved():
                        mode_mod.MODE_FILE = str(paths.config_file("mode"))
                        return mode_mod.resolve_mode()

                    self.assertEqual(resolved(), "enforce")  # before
                    proc = self._run(home)
                    self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
                    self.assertEqual(resolved(), "enforce")  # after

    def test_jev_guard_only_layout_migrates_straight_to_airlock(self):
        with tempfile.TemporaryDirectory() as home:
            old_cfg = Path(home) / ".config" / "jev-guard"
            old_cfg.mkdir(parents=True)
            (old_cfg / "mode").write_text("enforce\n")

            proc = self._run(home)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertEqual(
                (Path(home) / ".config" / "airlock" / "mode").read_text(), "enforce\n")
            self.assertTrue((Path(home) / ".config" / "jev-guard").is_symlink())

    def test_two_real_directories_are_never_merged(self):
        with tempfile.TemporaryDirectory() as home:
            mid = Path(home) / ".config" / "plumbline"
            mid.mkdir(parents=True)
            (mid / "mode").write_text("enforce\n")
            new = Path(home) / ".config" / "airlock"
            new.mkdir(parents=True)
            (new / "mode").write_text("shadow\n")

            proc = self._run(home)
            # Nothing merged, nothing overwritten: both survive untouched.
            self.assertEqual((new / "mode").read_text(), "shadow\n")
            self.assertEqual((mid / "mode").read_text(), "enforce\n")
            self.assertTrue(mid.is_dir() and not mid.is_symlink())
            self.assertIn("REAL directory", proc.stdout)

    def test_dry_run_moves_nothing(self):
        with tempfile.TemporaryDirectory() as home:
            self._live_layout(home)
            proc = self._run(home, "--dry-run")
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertTrue((Path(home) / ".config" / "plumbline" / "mode").exists())
            self.assertFalse((Path(home) / ".config" / "airlock").exists())

    def test_nothing_to_migrate_is_success(self):
        with tempfile.TemporaryDirectory() as home:
            proc = self._run(home)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_tune_worktree_gets_repair_advice(self):
        with tempfile.TemporaryDirectory() as home:
            self._live_layout(home)
            (Path(home) / ".local" / "state" / "plumbline" / "tune-worktree").mkdir()
            proc = self._run(home)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertIn("worktree repair", proc.stdout)

    def test_old_script_name_still_works(self):
        with tempfile.TemporaryDirectory() as home:
            self._live_layout(home)
            proc = self._run(home, script=REPO_ROOT / "install" / "migrate-from-jev-guard.sh")
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertEqual(
                (Path(home) / ".config" / "airlock" / "mode").read_text(), "enforce\n")


if __name__ == "__main__":
    unittest.main()
