"""install/_wire.py: wiring a settings.json's airlock PreToolUse hook.

Two things this file exists to prove:

  1. REPOINTING an existing hook command never drops the pinned interpreter.
     The bug that shipped once: the pattern replaced the WHOLE command
     string, so `"/usr/bin/python3 <old path>"` became `"<new path>"` and a
     machine that deliberately names /usr/bin/python3 rather than relying on
     the shebang and the file's mode bit got a broken hook.

  2. ADDING a missing hook actually adds it. A FIRST install has nothing to
     repoint -- there is no airlock hook command in settings.json yet -- and
     the old script's response to that ("no airlock.py hook command found,
     skipping") left a fresh machine unwired despite `--apply`. `--apply`
     must add the PreToolUse entry (and, opted in per flag, the belay Stop
     hook and the function-hooks env var), idempotently, always backing up
     an existing file first, and never touching an unrelated key or hook.
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
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
WIRE = REPO_ROOT / "install" / "_wire.py"

NEW_HOOK = "/opt/airlock/current/hooks/airlock.py"
PYTHON3 = "/usr/bin/python3"
NEW_HOOK_COMMAND = "%s %s" % (PYTHON3, NEW_HOOK)


def _run(path, apply_=False, belay=False, function_hooks=False,
         belay_wrapper="/nonexistent/airlock-belay-run"):
    env = dict(os.environ)
    env["NEW_HOOK"] = NEW_HOOK
    env["NEW_HOOK_COMMAND"] = NEW_HOOK_COMMAND
    env["APPLY"] = "1" if apply_ else "0"
    env["BELAY"] = "1" if belay else "0"
    env["BELAY_WRAPPER"] = belay_wrapper
    env["FUNCTION_HOOKS"] = "1" if function_hooks else "0"
    return subprocess.run([sys.executable, str(WIRE), str(path)],
                          capture_output=True, text=True, env=env, timeout=30)


def _settings(command):
    return json.dumps({"hooks": {"PreToolUse": [
        {"matcher": "*", "hooks": [{"type": "command", "command": command, "timeout": 5}]}]}})


class WireTestBase(unittest.TestCase):
    def _file(self, text):
        tmp = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        tmp.write(text)
        tmp.close()
        self.addCleanup(os.unlink, tmp.name)
        return tmp.name

    def _missing_path(self):
        tmp = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        tmp.close()
        os.unlink(tmp.name)
        self.addCleanup(lambda: os.path.exists(tmp.name) and os.unlink(tmp.name))
        return tmp.name

    def _data(self, path):
        return json.loads(Path(path).read_text())

    def _command(self, path):
        data = self._data(path)
        return data["hooks"]["PreToolUse"][0]["hooks"][0]["command"]

    def _backups(self, path):
        return list(Path(path).parent.glob(Path(path).name + ".bak.*"))


class TestInterpreterPrefix(WireTestBase):
    def test_a_pinned_interpreter_survives(self):
        path = self._file(_settings("/usr/bin/python3 /old/checkout/hooks/airlock.py"))
        _run(path, apply_=True)
        self.assertEqual(self._command(path), "/usr/bin/python3 " + NEW_HOOK)

    def test_a_bare_path_stays_bare(self):
        path = self._file(_settings("/old/checkout/hooks/airlock.py"))
        _run(path, apply_=True)
        self.assertEqual(self._command(path), NEW_HOOK)

    def test_every_old_hook_name_is_matched_too(self):
        for old in ("plumbline", "jev_guard"):
            with self.subTest(old=old):
                path = self._file(_settings(
                    "/usr/bin/python3 /old/checkout/hooks/%s.py" % old))
                _run(path, apply_=True)
                self.assertEqual(self._command(path), "/usr/bin/python3 " + NEW_HOOK)


class TestSafety(WireTestBase):
    def test_print_changes_nothing(self):
        text = _settings("/usr/bin/python3 /old/hooks/airlock.py")
        path = self._file(text)
        proc = _run(path, apply_=False)
        self.assertEqual(Path(path).read_text(), text)
        self.assertIn("would repoint", proc.stdout)

    def test_apply_backs_the_file_up_first(self):
        path = self._file(_settings("/usr/bin/python3 /old/hooks/airlock.py"))
        _run(path, apply_=True)
        backups = self._backups(path)
        self.addCleanup(lambda: [b.unlink() for b in backups])
        self.assertEqual(len(backups), 1)
        self.assertIn("/old/hooks/airlock.py", backups[0].read_text())

    def test_it_never_writes_invalid_json(self):
        path = self._file(_settings("/usr/bin/python3 /old/hooks/airlock.py"))
        _run(path, apply_=True)
        json.loads(Path(path).read_text())

    def test_an_already_wired_file_is_left_alone(self):
        text = _settings("/usr/bin/python3 " + NEW_HOOK)
        path = self._file(text)
        proc = _run(path, apply_=True)
        self.assertEqual(Path(path).read_text(), text)
        self.assertIn("already wired", proc.stdout)

    def test_other_hooks_in_the_file_are_untouched(self):
        data = {"hooks": {"PreToolUse": [
            {"matcher": "*", "hooks": [
                {"type": "command", "command": "/usr/bin/python3 /old/hooks/airlock.py"}]},
            {"matcher": "Bash", "hooks": [
                {"type": "command", "command": "/usr/bin/python3 /home/x/.claude/other-guard.py"}]},
        ]}}
        path = self._file(json.dumps(data))
        _run(path, apply_=True)
        out = json.loads(Path(path).read_text())
        self.assertEqual(out["hooks"]["PreToolUse"][1]["hooks"][0]["command"],
                         "/usr/bin/python3 /home/x/.claude/other-guard.py")


class TestInvalidJson(WireTestBase):
    def test_invalid_json_is_refused_with_no_write(self):
        text = "{not valid json"
        path = self._file(text)
        proc = _run(path, apply_=True)
        self.assertEqual(Path(path).read_text(), text)
        self.assertIn("not valid JSON", proc.stderr)

    def test_invalid_json_is_refused_in_print_mode_too(self):
        text = "{not valid json"
        path = self._file(text)
        proc = _run(path, apply_=False)
        self.assertEqual(Path(path).read_text(), text)
        self.assertIn("not valid JSON", proc.stderr)


class TestMissingFile(WireTestBase):
    """Bug: a FIRST install has no settings.json at all yet. --apply must
    create one with just the hook entries this run adds, not skip it."""

    def test_print_reports_what_would_be_created_and_writes_nothing(self):
        path = self._missing_path()
        proc = _run(path, apply_=False)
        self.assertFalse(os.path.exists(path))
        self.assertIn("would create", proc.stdout)
        self.assertIn(NEW_HOOK_COMMAND, proc.stdout)

    def test_apply_creates_it_with_just_the_pretooluse_entry(self):
        path = self._missing_path()
        proc = _run(path, apply_=True)
        self.assertTrue(os.path.exists(path))
        self.assertIn("created", proc.stdout)
        data = self._data(path)
        self.assertEqual(data, {"hooks": {"PreToolUse": [
            {"matcher": "*", "hooks": [
                {"type": "command", "command": NEW_HOOK_COMMAND, "timeout": 5}]}]}})

    def test_apply_is_idempotent_on_a_freshly_created_file(self):
        path = self._missing_path()
        _run(path, apply_=True)
        first = Path(path).read_text()
        proc = _run(path, apply_=True)
        self.assertEqual(Path(path).read_text(), first)
        self.assertIn("already wired", proc.stdout)


class TestAddMissingHook(WireTestBase):
    """Bug: a settings.json that HAS hooks, just not airlock's, was left
    completely unwired ("no airlock.py hook command found, skipping")."""

    def test_a_file_with_no_airlock_hook_gets_one_added(self):
        text = json.dumps({"model": "sonnet"})
        path = self._file(text)
        proc = _run(path, apply_=True)
        self.assertIn("added PreToolUse", proc.stdout)
        data = self._data(path)
        self.assertEqual(data["model"], "sonnet")
        self.assertEqual(
            data["hooks"]["PreToolUse"][0]["hooks"][0]["command"], NEW_HOOK_COMMAND)

    def test_print_previews_the_add_and_writes_nothing(self):
        text = json.dumps({"model": "sonnet"})
        path = self._file(text)
        proc = _run(path, apply_=False)
        self.assertEqual(Path(path).read_text(), text)
        self.assertIn("added PreToolUse", proc.stdout)

    def test_backs_up_before_adding(self):
        text = json.dumps({"model": "sonnet"})
        path = self._file(text)
        _run(path, apply_=True)
        backups = self._backups(path)
        self.addCleanup(lambda: [b.unlink() for b in backups])
        self.assertEqual(len(backups), 1)
        self.assertEqual(json.loads(backups[0].read_text()), {"model": "sonnet"})

    def test_an_existing_unrelated_hook_survives_the_add(self):
        data = {"hooks": {"PreToolUse": [
            {"matcher": "Bash", "hooks": [
                {"type": "command", "command": "/home/x/.claude/other-guard.py"}]},
        ]}}
        path = self._file(json.dumps(data))
        _run(path, apply_=True)
        out = self._data(path)
        self.assertEqual(len(out["hooks"]["PreToolUse"]), 2)
        matchers = {e["matcher"] for e in out["hooks"]["PreToolUse"]}
        self.assertEqual(matchers, {"Bash", "*"})
        bash_entry = next(e for e in out["hooks"]["PreToolUse"] if e["matcher"] == "Bash")
        self.assertEqual(bash_entry["hooks"][0]["command"], "/home/x/.claude/other-guard.py")

    def test_an_existing_matcher_star_pretooluse_block_is_reused_not_duplicated(self):
        data = {"hooks": {"PreToolUse": [
            {"matcher": "*", "hooks": [
                {"type": "command", "command": "/home/x/.claude/other-star-guard.py"}]},
        ]}}
        path = self._file(json.dumps(data))
        _run(path, apply_=True)
        out = self._data(path)
        self.assertEqual(len(out["hooks"]["PreToolUse"]), 1)
        commands = [h["command"] for h in out["hooks"]["PreToolUse"][0]["hooks"]]
        self.assertEqual(
            set(commands), {"/home/x/.claude/other-star-guard.py", NEW_HOOK_COMMAND})

    def test_adding_is_idempotent(self):
        text = json.dumps({"model": "sonnet"})
        path = self._file(text)
        _run(path, apply_=True)
        first = Path(path).read_text()
        proc = _run(path, apply_=True)
        self.assertEqual(Path(path).read_text(), first)
        self.assertTrue("nothing to add or repoint" in proc.stdout or "already wired" in proc.stdout)


class TestBelayFlag(WireTestBase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.wrapper = os.path.join(self._tmp.name, "airlock-belay-run")
        with open(self.wrapper, "w") as f:
            f.write("#!/usr/bin/env bash\necho stub\n")
        os.chmod(self.wrapper, 0o755)

    def test_belay_stop_hook_is_added_when_the_wrapper_exists(self):
        text = json.dumps({"model": "sonnet"})
        path = self._file(text)
        proc = _run(path, apply_=True, belay=True, belay_wrapper=self.wrapper)
        self.assertIn("added Stop (belay)", proc.stdout)
        out = self._data(path)
        stop = out["hooks"]["Stop"][0]
        self.assertEqual(stop["matcher"], "*")
        self.assertEqual(stop["hooks"][0]["command"], self.wrapper)
        self.assertEqual(stop["hooks"][0]["timeout"], 25)

    def test_belay_is_skipped_without_the_wrapper(self):
        text = json.dumps({"model": "sonnet"})
        path = self._file(text)
        proc = _run(path, apply_=True, belay=True,
                     belay_wrapper="/nonexistent/airlock-belay-run")
        out = self._data(path)
        self.assertNotIn("Stop", out.get("hooks", {}))
        self.assertIn("skipping Stop hook", proc.stdout)

    def test_belay_add_is_idempotent(self):
        text = json.dumps({"model": "sonnet"})
        path = self._file(text)
        _run(path, apply_=True, belay=True, belay_wrapper=self.wrapper)
        first = Path(path).read_text()
        proc = _run(path, apply_=True, belay=True, belay_wrapper=self.wrapper)
        self.assertEqual(Path(path).read_text(), first)
        self.assertTrue("nothing to add or repoint" in proc.stdout or "already wired" in proc.stdout)

    def test_missing_file_created_with_belay_when_wrapper_exists(self):
        path = self._missing_path()
        _run(path, apply_=True, belay=True, belay_wrapper=self.wrapper)
        data = self._data(path)
        self.assertEqual(data["hooks"]["Stop"][0]["hooks"][0]["command"], self.wrapper)


class TestFunctionHooksFlag(WireTestBase):
    def test_env_var_is_added(self):
        text = json.dumps({"model": "sonnet"})
        path = self._file(text)
        proc = _run(path, apply_=True, function_hooks=True)
        self.assertIn('env.CLAUDE_CODE_ENABLE_FUNCTION_HOOKS = "1"', proc.stdout)
        out = self._data(path)
        self.assertEqual(out["env"]["CLAUDE_CODE_ENABLE_FUNCTION_HOOKS"], "1")

    def test_existing_env_keys_survive(self):
        data = {"model": "sonnet", "env": {"SOME_OTHER_VAR": "x"}}
        path = self._file(json.dumps(data))
        _run(path, apply_=True, function_hooks=True)
        out = self._data(path)
        self.assertEqual(out["env"]["SOME_OTHER_VAR"], "x")
        self.assertEqual(out["env"]["CLAUDE_CODE_ENABLE_FUNCTION_HOOKS"], "1")

    def test_function_hooks_add_is_idempotent(self):
        text = json.dumps({"model": "sonnet"})
        path = self._file(text)
        _run(path, apply_=True, function_hooks=True)
        first = Path(path).read_text()
        proc = _run(path, apply_=True, function_hooks=True)
        self.assertEqual(Path(path).read_text(), first)
        self.assertTrue("nothing to add or repoint" in proc.stdout or "already wired" in proc.stdout)


if __name__ == "__main__":
    unittest.main()
