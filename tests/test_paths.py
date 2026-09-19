"""airlock/paths.py: the newest-name-preferred, older-names-accepted resolution.

The project has been renamed twice (jev-guard -> plumbline -> airlock), so
every directory resolves through three names. The point of these tests is the
one failure that would be expensive and silent: a machine that has been
running an earlier name in `enforce` mode gets renamed, finds no
~/.config/airlock, and quietly drops back to the default `shadow` because it
never looked at the older directory.
"""

import tests  # noqa: F401 -- MUST be the first import. `python3 -m unittest
# discover -s tests` runs with start_dir == top_level_dir, so unittest treats
# `tests/` as a flat directory of top-level modules and never executes
# tests/__init__.py as a package init (name == '.' in TestLoader._find_tests).
# Importing it explicitly, here, first, is what actually runs its HOME/
# AIRLOCK_*-isolating fixture before any airlock.* module resolves a real path.

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from airlock import paths


class PathsTestBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.home = Path(self._tmp.name)
        # expanduser("~") reads $HOME, so this is enough to relocate every
        # lookup in paths.py without patching the module itself.
        patcher = mock.patch.dict(os.environ, {"HOME": str(self.home)}, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        for var in ("AIRLOCK_CONFIG_DIR", "PLUMBLINE_CONFIG_DIR", "JEV_GUARD_CONFIG_DIR",
                    "AIRLOCK_STATE_DIR", "PLUMBLINE_STATE_DIR", "JEV_GUARD_STATE_DIR",
                    "AIRLOCK_HOME", "PLUMBLINE_HOME", "JEV_HOME"):
            os.environ.pop(var, None)


class TestEnvPreference(unittest.TestCase):
    def test_new_name_wins_over_old(self):
        with mock.patch.dict(os.environ, {"AIRLOCK_MODE": "enforce", "JEV_GUARD_MODE": "off"}):
            self.assertEqual(paths.env("AIRLOCK_MODE", "JEV_GUARD_MODE"), "enforce")

    def test_old_name_accepted_when_new_absent(self):
        env = {k: v for k, v in os.environ.items() if k != "AIRLOCK_MODE"}
        env["JEV_GUARD_MODE"] = "enforce"
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(paths.env("AIRLOCK_MODE", "JEV_GUARD_MODE"), "enforce")

    def test_middle_name_beats_oldest(self):
        env = {k: v for k, v in os.environ.items() if k != "AIRLOCK_MODE"}
        env["PLUMBLINE_MODE"] = "enforce"
        env["JEV_GUARD_MODE"] = "off"
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(paths.env(*("AIRLOCK_MODE", "PLUMBLINE_MODE", "JEV_GUARD_MODE")),
                             "enforce")

    def test_newest_name_beats_both_older(self):
        with mock.patch.dict(os.environ, {"AIRLOCK_MODE": "enforce",
                                          "PLUMBLINE_MODE": "off",
                                          "JEV_GUARD_MODE": "off"}):
            self.assertEqual(paths.env(*("AIRLOCK_MODE", "PLUMBLINE_MODE", "JEV_GUARD_MODE")),
                             "enforce")

    def test_empty_value_is_not_a_value(self):
        with mock.patch.dict(os.environ, {"AIRLOCK_MODE": "", "JEV_GUARD_MODE": "enforce"}):
            self.assertEqual(paths.env("AIRLOCK_MODE", "JEV_GUARD_MODE"), "enforce")

    def test_default_when_nothing_set(self):
        env = {k: v for k, v in os.environ.items()
               if k not in ("AIRLOCK_MODE", "JEV_GUARD_MODE")}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(paths.env("AIRLOCK_MODE", "JEV_GUARD_MODE", default="shadow"), "shadow")


class TestConfigDir(PathsTestBase):
    def test_neither_exists_uses_new_name(self):
        self.assertEqual(paths.config_dir(), self.home / ".config" / "airlock")

    def test_only_old_exists_uses_old(self):
        old = self.home / ".config" / "jev-guard"
        old.mkdir(parents=True)
        self.assertEqual(paths.config_dir(), old)

    def test_only_new_exists_uses_new(self):
        new = self.home / ".config" / "airlock"
        new.mkdir(parents=True)
        self.assertEqual(paths.config_dir(), new)

    def test_both_exist_prefers_new(self):
        (self.home / ".config" / "jev-guard").mkdir(parents=True)
        new = self.home / ".config" / "airlock"
        new.mkdir(parents=True)
        self.assertEqual(paths.config_dir(), new)

    def test_env_override_beats_both(self):
        (self.home / ".config" / "jev-guard").mkdir(parents=True)
        (self.home / ".config" / "airlock").mkdir(parents=True)
        with mock.patch.dict(os.environ, {"AIRLOCK_CONFIG_DIR": str(self.home / "elsewhere")}):
            self.assertEqual(paths.config_dir(), self.home / "elsewhere")

    def test_legacy_env_override_accepted(self):
        with mock.patch.dict(os.environ, {"JEV_GUARD_CONFIG_DIR": str(self.home / "legacy")}):
            self.assertEqual(paths.config_dir(), self.home / "legacy")


class TestStateDir(PathsTestBase):
    def test_neither_exists_uses_new_name(self):
        self.assertEqual(paths.state_dir(), self.home / ".local" / "state" / "airlock")

    def test_only_old_exists_uses_old(self):
        old = self.home / ".local" / "state" / "jev-guard"
        old.mkdir(parents=True)
        self.assertEqual(paths.state_dir(), old)

    def test_legacy_env_override_accepted(self):
        with mock.patch.dict(os.environ, {"JEV_GUARD_STATE_DIR": str(self.home / "s")}):
            self.assertEqual(paths.state_dir(), self.home / "s")


class TestInstallHome(PathsTestBase):
    def test_neither_exists_uses_new_name(self):
        self.assertEqual(paths.install_home(), self.home / ".local" / "share" / "airlock")

    def test_only_old_exists_uses_old(self):
        old = self.home / ".local" / "share" / "jev-guard"
        old.mkdir(parents=True)
        self.assertEqual(paths.install_home(), old)

    def test_legacy_jev_home_env_accepted(self):
        with mock.patch.dict(os.environ, {"JEV_HOME": str(self.home / "jh")}):
            self.assertEqual(paths.install_home(), self.home / "jh")


class TestModeSurvivesTheRename(PathsTestBase):
    """The whole reason the fallback exists: a live machine in enforce mode
    must not silently reset to shadow because the directory was renamed."""

    def test_enforce_in_the_old_config_dir_is_still_enforce(self):
        old = self.home / ".config" / "jev-guard"
        old.mkdir(parents=True)
        (old / "mode").write_text("enforce\n")

        # mode.py reads MODE_FILE, resolved at import time, so re-resolve it
        # the same way a freshly started hook process would.
        from airlock import mode as mode_mod
        with mock.patch.object(mode_mod, "MODE_FILE", str(paths.config_file("mode"))):
            env = {k: v for k, v in os.environ.items()
                   if k not in ("AIRLOCK_MODE", "JEV_GUARD_MODE")}
            with mock.patch.dict(os.environ, env, clear=True):
                os.environ["HOME"] = str(self.home)
                self.assertEqual(mode_mod.resolve_mode(), "enforce")

    def test_rules_json_in_the_old_config_dir_is_still_read(self):
        old = self.home / ".config" / "jev-guard"
        old.mkdir(parents=True)
        (old / "rules.json").write_text('{"R5-sudo": "warn"}')

        from airlock import rules as rules_mod
        overrides = rules_mod.load_action_overrides(str(paths.config_file("rules.json")))
        self.assertEqual(overrides, {"R5-sudo": "warn"})


class TestRuntimeSocket(PathsTestBase):
    def test_new_socket_name_by_default(self):
        runtime = self.home / "run"
        runtime.mkdir()
        with mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": str(runtime)}):
            self.assertEqual(paths.runtime_socket(), str(runtime / "airlock" / "airlock.sock"))

    def test_falls_back_to_a_live_legacy_socket(self):
        runtime = self.home / "run"
        (runtime / "jev").mkdir(parents=True)
        (runtime / "jev" / "jev.sock").write_text("")
        with mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": str(runtime)}):
            self.assertEqual(paths.runtime_socket(), str(runtime / "jev" / "jev.sock"))

    def test_new_socket_wins_when_both_exist(self):
        runtime = self.home / "run"
        (runtime / "jev").mkdir(parents=True)
        (runtime / "jev" / "jev.sock").write_text("")
        (runtime / "airlock").mkdir(parents=True)
        (runtime / "airlock" / "airlock.sock").write_text("")
        with mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": str(runtime)}):
            self.assertEqual(paths.runtime_socket(), str(runtime / "airlock" / "airlock.sock"))

    def test_daemon_always_binds_the_new_name(self):
        runtime = self.home / "run"
        (runtime / "jev").mkdir(parents=True)
        (runtime / "jev" / "jev.sock").write_text("")
        with mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": str(runtime)}):
            self.assertEqual(paths.runtime_socket_dir(), str(runtime / "airlock"))


class TestThreeNameFallback(PathsTestBase):
    """jev-guard -> plumbline -> airlock: a directory must resolve through
    every name this project has ever used, newest first."""

    CASES = (
        ("config_dir", (".config",)),
        ("state_dir", (".local", "state")),
        ("install_home", (".local", "share")),
    )

    def _parent(self, parts):
        return self.home.joinpath(*parts)

    def test_only_the_middle_name_exists(self):
        for fn, parts in self.CASES:
            with self.subTest(fn=fn):
                mid = self._parent(parts) / "plumbline"
                mid.mkdir(parents=True, exist_ok=True)
                self.assertEqual(getattr(paths, fn)(), mid)

    def test_middle_name_beats_the_oldest(self):
        for fn, parts in self.CASES:
            with self.subTest(fn=fn):
                parent = self._parent(parts)
                (parent / "jev-guard").mkdir(parents=True, exist_ok=True)
                mid = parent / "plumbline"
                mid.mkdir(parents=True, exist_ok=True)
                self.assertEqual(getattr(paths, fn)(), mid)

    def test_new_name_beats_both_older(self):
        for fn, parts in self.CASES:
            with self.subTest(fn=fn):
                parent = self._parent(parts)
                (parent / "jev-guard").mkdir(parents=True, exist_ok=True)
                (parent / "plumbline").mkdir(parents=True, exist_ok=True)
                new = parent / "airlock"
                new.mkdir(parents=True, exist_ok=True)
                self.assertEqual(getattr(paths, fn)(), new)

    def test_middle_env_override_accepted(self):
        for var, fn in (("PLUMBLINE_CONFIG_DIR", "config_dir"),
                        ("PLUMBLINE_STATE_DIR", "state_dir"),
                        ("PLUMBLINE_HOME", "install_home")):
            with self.subTest(var=var):
                with mock.patch.dict(os.environ, {var: str(self.home / "mid")}):
                    self.assertEqual(getattr(paths, fn)(), self.home / "mid")

    def test_the_live_layout_resolves_to_the_real_directory(self):
        """The live box: a real `plumbline` directory with a `jev-guard`
        symlink pointing at it. Both names must land on the same files, and
        the mode must still read `enforce`."""
        real = self.home / ".config" / "plumbline"
        real.mkdir(parents=True)
        (real / "mode").write_text("enforce\n")
        (self.home / ".config" / "jev-guard").symlink_to(real)

        self.assertEqual(paths.config_dir(), real)

        from airlock import mode as mode_mod
        with mock.patch.object(mode_mod, "MODE_FILE", str(paths.config_file("mode"))):
            env = {k: v for k, v in os.environ.items()
                   if k not in ("AIRLOCK_MODE", "PLUMBLINE_MODE", "JEV_GUARD_MODE")}
            with mock.patch.dict(os.environ, env, clear=True):
                os.environ["HOME"] = str(self.home)
                self.assertEqual(mode_mod.resolve_mode(), "enforce")


class TestMiddleSocketFallback(PathsTestBase):
    def test_falls_back_to_a_live_plumbline_socket(self):
        runtime = self.home / "run"
        (runtime / "plumbline").mkdir(parents=True)
        (runtime / "plumbline" / "plumbline.sock").write_text("")
        with mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": str(runtime)}):
            self.assertEqual(paths.runtime_socket(),
                             str(runtime / "plumbline" / "plumbline.sock"))

    def test_plumbline_socket_beats_the_jev_one(self):
        runtime = self.home / "run"
        (runtime / "jev").mkdir(parents=True)
        (runtime / "jev" / "jev.sock").write_text("")
        (runtime / "plumbline").mkdir(parents=True)
        (runtime / "plumbline" / "plumbline.sock").write_text("")
        with mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": str(runtime)}):
            self.assertEqual(paths.runtime_socket(),
                             str(runtime / "plumbline" / "plumbline.sock"))

    def test_airlock_socket_wins_over_every_older_one(self):
        runtime = self.home / "run"
        for d, f in (("jev", "jev.sock"), ("plumbline", "plumbline.sock"),
                     ("airlock", "airlock.sock")):
            (runtime / d).mkdir(parents=True)
            (runtime / d / f).write_text("")
        with mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": str(runtime)}):
            self.assertEqual(paths.runtime_socket(),
                             str(runtime / "airlock" / "airlock.sock"))


if __name__ == "__main__":
    unittest.main()
