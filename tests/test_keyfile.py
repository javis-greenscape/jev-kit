
import tests  # noqa: F401 -- MUST be the first import. `python3 -m unittest
# discover -s tests` runs with start_dir == top_level_dir, so unittest treats
# `tests/` as a flat directory of top-level modules and never executes
# tests/__init__.py as a package init (name == '.' in TestLoader._find_tests).
# Importing it explicitly, here, first, is what actually runs its HOME/
# AIRLOCK_*-isolating fixture before any airlock.* module resolves a real path.

import os
import pathlib
import tempfile
import unittest
from unittest import mock

from airlock import keyfile


class TestKeyfile(unittest.TestCase):
    def test_env_var_wins(self):
        with mock.patch.dict(os.environ, {"TYPESAFE_API_KEY": "from-env"}):
            self.assertEqual(keyfile.get_api_key(), "from-env")

    def test_falls_back_to_env_file(self):
        with tempfile.NamedTemporaryFile("w", suffix=".env", delete=False) as f:
            f.write("OTHER_VAR=ignored\n")
            f.write("TYPESAFE_API_KEY=from-file-secret\n")
            path = f.name
        try:
            with mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop("TYPESAFE_API_KEY", None)
                with mock.patch.object(keyfile, "ENV_FILE", path):
                    self.assertEqual(keyfile.get_api_key(), "from-file-secret")
        finally:
            os.remove(path)

    def test_quoted_value_in_env_file(self):
        with tempfile.NamedTemporaryFile("w", suffix=".env", delete=False) as f:
            f.write('TYPESAFE_API_KEY="quoted-secret"\n')
            path = f.name
        try:
            os.environ.pop("TYPESAFE_API_KEY", None)
            with mock.patch.object(keyfile, "ENV_FILE", path):
                self.assertEqual(keyfile.get_api_key(), "quoted-secret")
        finally:
            os.remove(path)

    def test_missing_file_returns_none(self):
        os.environ.pop("TYPESAFE_API_KEY", None)
        with mock.patch.object(keyfile, "ENV_FILE", "/nonexistent/path/env"):
            self.assertIsNone(keyfile.get_api_key())

    def test_no_key_line_returns_none(self):
        with tempfile.NamedTemporaryFile("w", suffix=".env", delete=False) as f:
            f.write("SOME_OTHER_KEY=value\n")
            path = f.name
        try:
            os.environ.pop("TYPESAFE_API_KEY", None)
            with mock.patch.object(keyfile, "ENV_FILE", path):
                self.assertIsNone(keyfile.get_api_key())
        finally:
            os.remove(path)


class TestDefaultEnvFile(unittest.TestCase):
    """The default key file is generic so a fresh machine needs no config at
    all. No other path is baked into the code: a machine that keeps its key
    elsewhere names that path in install/config.env, which reaches the code as
    AIRLOCK_LEGACY_KEY_FILES or as the pointer file the installer writes."""

    def _with_home(self, home):
        return mock.patch.object(os.path, "expanduser",
                                 lambda p: p.replace("~", home, 1))

    def test_generic_path_when_neither_exists(self):
        with tempfile.TemporaryDirectory() as home:
            with self._with_home(home):
                self.assertEqual(keyfile.default_env_file(),
                                 os.path.join(home, ".config/airlock/env"))

    def test_generic_path_wins_when_both_exist(self):
        with tempfile.TemporaryDirectory() as home:
            legacy = os.path.join(home, ".config/elsewhere/env")
            for rel in (".config/airlock", ".config/elsewhere"):
                os.makedirs(os.path.join(home, rel))
                open(os.path.join(home, rel, "env"), "w").close()
            with self._with_home(home), \
                    mock.patch.dict(os.environ,
                                    {"AIRLOCK_LEGACY_KEY_FILES": legacy}):
                self.assertEqual(keyfile.default_env_file(),
                                 os.path.join(home, ".config/airlock/env"))

    def test_configured_legacy_path_is_honoured_when_it_is_the_only_one(self):
        with tempfile.TemporaryDirectory() as home:
            legacy = os.path.join(home, ".config/elsewhere/env")
            os.makedirs(os.path.dirname(legacy))
            open(legacy, "w").close()
            with self._with_home(home), \
                    mock.patch.dict(os.environ,
                                    {"AIRLOCK_LEGACY_KEY_FILES": legacy}):
                self.assertEqual(keyfile.default_env_file(), legacy)

    def test_no_legacy_path_is_baked_in(self):
        """With nothing configured, an existing file elsewhere is ignored."""
        with tempfile.TemporaryDirectory() as home:
            other = os.path.join(home, ".config/elsewhere/env")
            os.makedirs(os.path.dirname(other))
            open(other, "w").close()
            env = {k: v for k, v in os.environ.items()
                   if k != "AIRLOCK_LEGACY_KEY_FILES"}
            with self._with_home(home), \
                    mock.patch.dict(os.environ, env, clear=True):
                self.assertEqual(keyfile.default_env_file(),
                                 os.path.join(home, ".config/airlock/env"))

    def test_pointer_file_is_used_when_the_default_is_absent(self):
        """The hook runs with a minimal environment, so the installer records
        the path in $AIRLOCK_CONFIG_DIR/keyfile.path and this reads it."""
        with tempfile.TemporaryDirectory() as home:
            recorded = os.path.join(home, ".config/elsewhere/env")
            os.makedirs(os.path.dirname(recorded))
            open(recorded, "w").close()
            config = pathlib.Path(home) / ".config" / "airlock"
            config.mkdir(parents=True)
            (config / "keyfile.path").write_text(recorded + "\n")
            with self._with_home(home), \
                    mock.patch.object(keyfile.paths, "config_file",
                                      lambda name: config / name):
                self.assertEqual(keyfile.default_env_file(), recorded)

    def test_it_never_raises(self):
        with mock.patch.object(os.path, "isfile", side_effect=OSError("boom")):
            self.assertTrue(keyfile.default_env_file())


if __name__ == "__main__":
    unittest.main()
