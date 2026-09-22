
import tests  # noqa: F401, I001 -- MUST be the first import. `python3 -m unittest
# discover -s tests` runs with start_dir == top_level_dir, so unittest treats
# `tests/` as a flat directory of top-level modules and never executes
# tests/__init__.py as a package init (name == '.' in TestLoader._find_tests).
# Importing it explicitly, here, first, is what actually runs its HOME/
# AIRLOCK_*-isolating fixture before any airlock.* module resolves a real path.

import os
import pathlib
import subprocess
import tempfile
import unittest
from unittest import mock

from tests import posix_only
from airlock import repo_path


def _git(*args, cwd):
    subprocess.run(
        ["git"] + list(args), cwd=str(cwd), check=True,
        capture_output=True, text=True,
        env=dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
                  GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t"),
    )


def _make_repo(path):
    path.mkdir(parents=True, exist_ok=True)
    _git("init", "-q", "-b", "main", cwd=path)
    (path / "README").write_text("hi\n")
    _git("add", "README", cwd=path)
    _git("commit", "-q", "-m", "init", cwd=path)
    return path


class TestIsGitCheckout(unittest.TestCase):
    def test_real_checkout_is_true(self):
        with tempfile.TemporaryDirectory() as home:
            repo = _make_repo(pathlib.Path(home) / "repo")
            self.assertTrue(repo_path.is_git_checkout(repo))

    def test_plain_directory_is_false(self):
        with tempfile.TemporaryDirectory() as home:
            plain = pathlib.Path(home) / "plain"
            plain.mkdir()
            self.assertFalse(repo_path.is_git_checkout(plain))

    def test_nonexistent_directory_is_false(self):
        self.assertFalse(repo_path.is_git_checkout("/nonexistent/path/xyz"))

    def test_linked_worktree_git_file_counts(self):
        with tempfile.TemporaryDirectory() as home:
            main_repo = _make_repo(pathlib.Path(home) / "main")
            worktree = pathlib.Path(home) / "wt"
            _git("worktree", "add", "-b", "auto-tune", str(worktree), "main", cwd=main_repo)
            self.assertTrue(repo_path.is_git_checkout(worktree))


class TestResolveRepoOrder(unittest.TestCase):
    """AIRLOCK_TUNE_REPO env -> repo.path pointer -> script_dir's own parent
    if a checkout -> None. Order matters: each step must win over the ones
    after it and lose to the ones before it."""

    def setUp(self):
        repo_path.reset_diagnostics()

    def test_env_override_wins_over_everything(self):
        with tempfile.TemporaryDirectory() as home:
            pointed = _make_repo(pathlib.Path(home) / "pointed")
            self_repo = _make_repo(pathlib.Path(home) / "self")
            config = pathlib.Path(home) / ".config" / "airlock"
            config.mkdir(parents=True)
            config.chmod(0o700)
            (config / "repo.path").write_text(str(pointed) + "\n")
            (config / "repo.path").chmod(0o600)
            with mock.patch.object(repo_path.paths, "config_file",
                                    lambda name: config / name), \
                 mock.patch.dict(os.environ, {"AIRLOCK_TUNE_REPO": "/explicit/repo"}):
                self.assertEqual(repo_path.resolve_repo(self_repo / "tuning"), "/explicit/repo")

    def test_env_override_is_honoured_even_if_not_a_checkout(self):
        """Mirrors keyfile's AIRLOCK_KEY_FILE: an explicit override is honoured
        as given, no trust checks -- the operator already chose it."""
        env = {k: v for k, v in os.environ.items() if k != "AIRLOCK_TUNE_REPO"}
        env["AIRLOCK_TUNE_REPO"] = "~/not-checked"
        with mock.patch.dict(os.environ, env, clear=True), \
             mock.patch.object(os.path, "expanduser", lambda p: p.replace("~", "/home/x", 1)):
            self.assertEqual(repo_path.resolve_repo("/anything/tuning"), "/home/x/not-checked")

    def test_pointer_wins_over_self_checkout(self):
        with tempfile.TemporaryDirectory() as home:
            pointed = _make_repo(pathlib.Path(home) / "pointed")
            self_repo = _make_repo(pathlib.Path(home) / "self")
            config = pathlib.Path(home) / ".config" / "airlock"
            config.mkdir(parents=True)
            config.chmod(0o700)
            (config / "repo.path").write_text(str(pointed) + "\n")
            (config / "repo.path").chmod(0o600)
            env = {k: v for k, v in os.environ.items() if k != "AIRLOCK_TUNE_REPO"}
            with mock.patch.object(repo_path.paths, "config_file",
                                    lambda name: config / name), \
                 mock.patch.dict(os.environ, env, clear=True):
                got = repo_path.resolve_repo(self_repo / "tuning")
        self.assertEqual(got, str(pointed))

    def test_falls_back_to_self_checkout_when_no_pointer(self):
        with tempfile.TemporaryDirectory() as home:
            self_repo = _make_repo(pathlib.Path(home) / "self")
            config = pathlib.Path(home) / ".config" / "airlock"  # no repo.path in it
            config.mkdir(parents=True)
            env = {k: v for k, v in os.environ.items() if k != "AIRLOCK_TUNE_REPO"}
            with mock.patch.object(repo_path.paths, "config_file",
                                    lambda name: config / name), \
                 mock.patch.dict(os.environ, env, clear=True):
                got = repo_path.resolve_repo(self_repo / "tuning")
        self.assertEqual(got, str(self_repo))

    def test_deployed_release_with_no_git_and_no_pointer_resolves_to_none(self):
        """The core bug this module fixes: a plain exported directory tree
        (no .git anywhere) with nothing recorded must give up cleanly, not
        raise and not guess."""
        with tempfile.TemporaryDirectory() as home:
            deployed = pathlib.Path(home) / "current"
            (deployed / "tuning").mkdir(parents=True)
            config = pathlib.Path(home) / ".config" / "airlock"
            config.mkdir(parents=True)
            env = {k: v for k, v in os.environ.items() if k != "AIRLOCK_TUNE_REPO"}
            with mock.patch.object(repo_path.paths, "config_file",
                                    lambda name: config / name), \
                 mock.patch.dict(os.environ, env, clear=True):
                got = repo_path.resolve_repo(deployed / "tuning")
        self.assertIsNone(got)


@posix_only("pointer trust here is a uid check and a chmod check, and Windows\n"
            "            has neither; the Windows half is TestPointerTrustOnWindows below")
class TestPointerTrust(unittest.TestCase):
    """Same rules as keyfile.path: the pointer is followed only when its own
    permissions say the owner wrote it, and the target is a real checkout."""

    def setUp(self):
        repo_path.reset_diagnostics()

    def _fixture(self, home, recorded=None, pointer_mode=0o600, dir_mode=0o700,
                 make_target=True, target_is_repo=True):
        config = pathlib.Path(home) / ".config" / "airlock"
        config.mkdir(parents=True)
        target = pathlib.Path(home) / "elsewhere" / "repo"
        if make_target:
            if target_is_repo:
                _make_repo(target)
            else:
                target.mkdir(parents=True)
        pointer = config / "repo.path"
        pointer.write_text((recorded if recorded is not None else str(target)) + "\n")
        pointer.chmod(pointer_mode)
        config.chmod(dir_mode)
        return config, target

    def _patched(self, config):
        return mock.patch.object(repo_path.paths, "config_file", lambda name: config / name)

    def _target(self, home, **kw):
        config, target = self._fixture(home, **kw)
        with self._patched(config):
            return repo_path.pointer_target(), target

    def test_good_pointer_is_followed(self):
        with tempfile.TemporaryDirectory() as home:
            got, target = self._target(home)
            self.assertEqual(got, str(target))
            self.assertEqual(repo_path.pointer_diagnostics(), ())

    def test_group_writable_pointer_is_ignored(self):
        with tempfile.TemporaryDirectory() as home:
            got, _ = self._target(home, pointer_mode=0o660)
            self.assertIsNone(got)
            self.assertTrue(any("group- or world-writable" in d
                                for d in repo_path.pointer_diagnostics()))

    def test_world_writable_directory_is_ignored(self):
        with tempfile.TemporaryDirectory() as home:
            got, _ = self._target(home, dir_mode=0o707)
            self.assertIsNone(got)
            self.assertTrue(any("pointer directory" in d
                                for d in repo_path.pointer_diagnostics()))

    def test_symlinked_pointer_is_ignored(self):
        with tempfile.TemporaryDirectory() as home:
            config = pathlib.Path(home) / ".config" / "airlock"
            config.mkdir(parents=True)
            real = pathlib.Path(home) / "elsewhere.path"
            real.write_text("/etc/passwd\n")
            (config / "repo.path").symlink_to(real)
            config.chmod(0o700)
            with self._patched(config):
                self.assertIsNone(repo_path.pointer_target())
            self.assertTrue(any("not a regular file" in d
                                for d in repo_path.pointer_diagnostics()))

    def test_relative_recorded_path_is_ignored(self):
        with tempfile.TemporaryDirectory() as home:
            got, _ = self._target(home, recorded="relative/repo")
            self.assertIsNone(got)
            self.assertTrue(any("not absolute" in d
                                for d in repo_path.pointer_diagnostics()))

    def test_missing_target_falls_back_cleanly(self):
        with tempfile.TemporaryDirectory() as home:
            got, _ = self._target(home, make_target=False)
            self.assertIsNone(got)

    def test_target_directory_without_git_is_rejected(self):
        with tempfile.TemporaryDirectory() as home:
            got, _ = self._target(home, target_is_repo=False)
            self.assertIsNone(got)
            self.assertTrue(any("not a git checkout" in d
                                for d in repo_path.pointer_diagnostics()))

    def test_commented_pointer_is_ignored(self):
        with tempfile.TemporaryDirectory() as home:
            got, _ = self._target(home, recorded="# nothing here")
            self.assertIsNone(got)

    def test_pointer_target_never_raises(self):
        with mock.patch.object(repo_path, "pointer_file_path", side_effect=OSError("boom")):
            self.assertIsNone(repo_path.pointer_target())


class TestPointerTrustOnWindows(unittest.TestCase):
    """The same treatment as keyfile.path, for the same reason: Windows has no
    uid and no meaningful st_mode, so those two checks are replaced by a
    diagnostic naming what was and was not checked, and the structural checks
    still run."""

    def setUp(self):
        repo_path.reset_diagnostics()

    def _fixture(self, home, recorded=None, target_is_repo=True):
        config = pathlib.Path(home) / "AppData" / "Roaming" / "airlock"
        config.mkdir(parents=True)
        target = pathlib.Path(home) / "code" / "jev-kit"
        if target_is_repo:
            _make_repo(target)
        else:
            target.mkdir(parents=True)
        pointer = config / "repo.path"
        pointer.write_text((recorded if recorded is not None else str(target)) + "\n")
        return config, target

    def test_the_pointer_is_followed_and_the_gap_is_recorded(self):
        with tempfile.TemporaryDirectory() as home:
            config, target = self._fixture(home)
            with mock.patch.object(repo_path.paths, "config_file",
                                   lambda name: config / name), \
                 mock.patch.dict(os.environ, {"USERPROFILE": home}):
                self.assertEqual(repo_path.pointer_target(windows=True), str(target))
            blob = " ".join(repo_path.pointer_diagnostics())
        self.assertIn("CHECKED", blob)
        self.assertIn("NOT CHECKED", blob)

    def test_a_group_writable_pointer_is_refused_on_posix_and_followed_on_windows(self):
        with tempfile.TemporaryDirectory() as home:
            config, target = self._fixture(home)
            (config / "repo.path").chmod(0o666)
            with mock.patch.object(repo_path.paths, "config_file",
                                   lambda name: config / name), \
                 mock.patch.dict(os.environ, {"USERPROFILE": home}):
                self.assertEqual(repo_path.pointer_target(windows=True), str(target))
                self.assertIsNone(repo_path.pointer_target(windows=False))

    def test_a_target_that_is_not_a_checkout_is_still_refused(self):
        with tempfile.TemporaryDirectory() as home:
            config, _target = self._fixture(home, target_is_repo=False)
            with mock.patch.object(repo_path.paths, "config_file",
                                   lambda name: config / name), \
                 mock.patch.dict(os.environ, {"USERPROFILE": home}):
                self.assertIsNone(repo_path.pointer_target(windows=True))
            self.assertTrue(any("not a git checkout" in d
                                for d in repo_path.pointer_diagnostics()))

    def test_a_relative_recorded_path_is_still_refused(self):
        with tempfile.TemporaryDirectory() as home:
            config, _target = self._fixture(home, recorded="code/jev-kit")
            with mock.patch.object(repo_path.paths, "config_file",
                                   lambda name: config / name), \
                 mock.patch.dict(os.environ, {"USERPROFILE": home}):
                self.assertIsNone(repo_path.pointer_target(windows=True))
            self.assertTrue(any("not absolute" in d
                                for d in repo_path.pointer_diagnostics()))


class TestRecordRepoPath(unittest.TestCase):
    @posix_only("0600 is a POSIX mode; on Windows chmod only toggles the "
                "read-only attribute and platform_compat.restrict_path "
                "deliberately skips it")
    def test_writes_path_only_mode_600(self):
        with tempfile.TemporaryDirectory() as home:
            config = pathlib.Path(home) / ".config" / "airlock"
            config.mkdir(parents=True, mode=0o700)
            repo = pathlib.Path(home) / "repo"
            self.assertTrue(repo_path.record_repo_path(config, repo))
            written = config / "repo.path"
            self.assertEqual(written.read_text().strip(), str(repo))
            self.assertEqual(written.stat().st_mode & 0o777, 0o600)

    def test_never_raises_on_bad_dir(self):
        self.assertFalse(repo_path.record_repo_path("/nonexistent/dir/xyz", "/repo"))

    def test_the_bytes_written_are_exactly_the_path_and_one_newline(self):
        """`newline=""` is what keeps this true on Windows, where the default
        text mode would turn the "\n" into "\r\n" and put a stray carriage
        return inside the path when it is read back."""
        with tempfile.TemporaryDirectory() as home:
            config = pathlib.Path(home) / ".config" / "airlock"
            config.mkdir(parents=True, mode=0o700)
            repo = pathlib.Path(home) / "repo"
            self.assertTrue(repo_path.record_repo_path(config, repo))
            raw = (config / "repo.path").read_bytes()
            self.assertEqual(raw, str(repo).encode() + b"\n")


if __name__ == "__main__":
    unittest.main()
