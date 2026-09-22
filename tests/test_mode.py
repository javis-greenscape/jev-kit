
import tests  # noqa: F401, I001 -- MUST be the first import. `python3 -m unittest
# discover -s tests` runs with start_dir == top_level_dir, so unittest treats
# `tests/` as a flat directory of top-level modules and never executes
# tests/__init__.py as a package init (name == '.' in TestLoader._find_tests).
# Importing it explicitly, here, first, is what actually runs its HOME/
# AIRLOCK_*-isolating fixture before any airlock.* module resolves a real path.

import os
import tempfile
import unittest
from unittest import mock

from airlock import mode


class TestResolveMode(unittest.TestCase):
    def test_env_var_wins(self):
        with mock.patch.dict(os.environ, {"AIRLOCK_MODE": "enforce"}):
            self.assertEqual(mode.resolve_mode(), "enforce")

    def test_env_var_off(self):
        with mock.patch.dict(os.environ, {"AIRLOCK_MODE": "off"}):
            self.assertEqual(mode.resolve_mode(), "off")

    def test_invalid_env_var_falls_through_to_file(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "mode")
            with open(path, "w") as f:
                f.write("enforce\n")
            with mock.patch.dict(os.environ, {"AIRLOCK_MODE": "bogus"}), \
                 mock.patch.object(mode, "MODE_FILE", path):
                self.assertEqual(mode.resolve_mode(), "enforce")

    def test_file_used_when_no_env_var(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("AIRLOCK_MODE", None)
            with tempfile.TemporaryDirectory() as d:
                path = os.path.join(d, "mode")
                with open(path, "w") as f:
                    f.write("enforce  # trailing comment ignored by split()\n")
                with mock.patch.object(mode, "MODE_FILE", path):
                    self.assertEqual(mode.resolve_mode(), "enforce")

    def test_defaults_to_shadow_when_nothing_set(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("AIRLOCK_MODE", None)
            with mock.patch.object(mode, "MODE_FILE", "/nonexistent/path/mode"):
                self.assertEqual(mode.resolve_mode(), "shadow")

    def test_invalid_file_word_falls_back_to_shadow(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("AIRLOCK_MODE", None)
            with tempfile.TemporaryDirectory() as d:
                path = os.path.join(d, "mode")
                with open(path, "w") as f:
                    f.write("bogus\n")
                with mock.patch.object(mode, "MODE_FILE", path):
                    self.assertEqual(mode.resolve_mode(), "shadow")


if __name__ == "__main__":
    unittest.main()
