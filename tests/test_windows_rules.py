"""Rules-table behaviour that differs on native Windows, exercised on Linux.

Two things differ, and only two:

  R1  the secret-path table protects the WINDOWS key paths as well as the
      POSIX ones, in either separator and either case, and `type`,
      `Get-Content` and their friends count as readers alongside `cat`.
  R6  its DEFAULT action is `off` on Windows, because the rule encodes "this
      box is a headless server with no desktop" and a Windows workstation is
      not that. rules.json still overrides it either way.

Every test injects the platform, so none of them needs a Windows machine.
"""
import unittest

from airlock import rules


def _ctx(command, tool_name="Bash"):
    return rules.build_ctx({
        "session_id": "t", "cwd": "C:\\Users\\alice",
        "tool_name": tool_name, "tool_input": {"command": command},
    })


class TestR1ProtectsWindowsKeyPaths(unittest.TestCase):
    """The kit default and the guard-era default, spelled for Windows."""

    DENIED = (
        "type %APPDATA%\\jev-kit\\env",
        "type %APPDATA%\\airlock\\env",
        "Get-Content $env:APPDATA\\jev-kit\\env",
        "Get-Content $env:APPDATA\\airlock\\env",
        "gc C:\\Users\\alice\\AppData\\Roaming\\jev-kit\\env",
        "type C:\\Users\\alice\\AppData\\Roaming\\AIRLOCK\\ENV",
        # Git Bash spells the same file with forward slashes.
        "cat /c/Users/alice/AppData/Roaming/jev-kit/env",
        "findstr TYPESAFE %APPDATA%\\airlock\\env",
    )

    def test_every_windows_key_path_spelling_is_a_secret_store(self):
        for command in self.DENIED:
            with self.subTest(command=command):
                match = rules.prefilter_secret(_ctx(command))
                self.assertIsNotNone(match, command)
                self.assertFalse(match.ask, "R1 must decide this in code, "
                                            "with no key and no network")

    def test_the_posix_spellings_still_match(self):
        for command in ("cat ~/.config/jev-kit/env", "cat ~/.config/airlock/env"):
            with self.subTest(command=command):
                self.assertIsNotNone(rules.prefilter_secret(_ctx(command)))

    def test_it_fires_through_the_powershell_tool_too(self):
        # On a Windows machine without Git for Windows this is the ONLY shell
        # tool Claude Code registers.
        match = rules.prefilter_secret(
            _ctx("Get-Content $env:APPDATA\\airlock\\env", tool_name="PowerShell"))
        self.assertIsNotNone(match)

    def test_an_ordinary_windows_command_is_not_a_secret_read(self):
        for command in ("type README.md", "Get-Content .\\notes.txt",
                        "echo hello", "dir C:\\code"):
            with self.subTest(command=command):
                self.assertIsNone(rules.prefilter_secret(_ctx(command)))

    def test_a_windows_reader_name_is_matched_however_it_is_written(self):
        self.assertTrue(rules._windows_reader("Get-Content"))
        self.assertTrue(rules._windows_reader("GET-CONTENT"))
        self.assertTrue(rules._windows_reader("findstr.exe"))
        self.assertTrue(rules._windows_reader("C:\\Windows\\System32\\findstr.exe"))
        self.assertFalse(rules._windows_reader("notepad.exe"))
        self.assertFalse(rules._windows_reader(""))


class TestR1AdviceIsRunnableOnThePlatform(unittest.TestCase):
    """A deny that tells a Windows session to run `set -a; . ~/.config/...` is
    advice it cannot follow in either of its shells."""

    def test_the_posix_advice_is_posix_and_the_windows_advice_is_not(self):
        self.assertIn("set -a;", rules.R1_SUGGESTION_POSIX)
        self.assertNotIn("set -a;", rules.R1_SUGGESTION_WINDOWS)
        self.assertNotIn("~/.config", rules.R1_SUGGESTION_WINDOWS)
        self.assertIn("$env:APPDATA", rules.R1_SUGGESTION_WINDOWS)

    def test_the_windows_remedy_is_not_itself_denied_by_r1(self):
        """The remedy must not be a reader cmdlet, or following the advice
        would trip the very rule that gave it."""
        remedy = ('[IO.File]::ReadAllLines("$env:APPDATA\\jev-kit\\env") | '
                  'ForEach-Object { $n,$v = $_ -split \'=\',2; Set-Item "env:$n" $v }')
        self.assertIn(remedy.split(" | ")[0], rules.R1_SUGGESTION_WINDOWS)
        self.assertIsNone(rules.prefilter_secret(_ctx(remedy, tool_name="PowerShell")))

    def test_the_posix_remedy_is_not_itself_denied_by_r1(self):
        self.assertIsNone(rules.prefilter_secret(
            _ctx("set -a; . ~/.config/jev-kit/env; set +a")))


class TestR6DefaultsOffOnWindows(unittest.TestCase):
    """A Windows desktop is not a headless server."""

    RULE = rules.RULES_BY_ID["R6-gui-or-browser"]

    def test_the_default_is_deny_on_posix_and_off_on_windows(self):
        self.assertEqual(rules.default_action(self.RULE, windows=False), "deny")
        self.assertEqual(rules.default_action(self.RULE, windows=True), "off")

    def test_no_other_rule_changes_action_by_platform(self):
        changed = [r.id for r in rules.RULES if r.windows_action is not None]
        self.assertEqual(changed, ["R6-gui-or-browser"])

    def test_it_does_not_even_run_on_windows_by_default(self):
        ctx = _ctx("xdg-open https://example.com")
        self.assertEqual(
            [r.id for r, _ in rules.prefilter_matches(ctx, {}, windows=False)],
            ["R6-gui-or-browser"])
        self.assertEqual(
            [r.id for r, _ in rules.prefilter_matches(ctx, {}, windows=True)],
            [])

    def test_rules_json_turns_it_back_on_for_a_headless_windows_box(self):
        ctx = _ctx("xdg-open https://example.com")
        overrides = {"R6-gui-or-browser": "deny"}
        matched = rules.prefilter_matches(ctx, overrides, windows=True)
        self.assertEqual([r.id for r, _ in matched], ["R6-gui-or-browser"])
        self.assertEqual(
            rules.effective_action(self.RULE, overrides, windows=True), "deny")

    def test_rules_json_can_still_switch_it_off_on_posix(self):
        self.assertEqual(
            rules.effective_action(self.RULE, {"R6-gui-or-browser": "off"},
                                   windows=False), "off")

    def test_the_windows_deny_text_never_claims_the_box_is_headless(self):
        match = rules.prefilter_gui(_ctx("firefox https://example.com"), windows=True)
        self.assertIsNotNone(match)
        blob = (match.detail + " " + match.suggestion).lower()
        self.assertNotIn("headless server", blob)
        self.assertNotIn("$display", blob)
        self.assertNotIn("x server", blob)
        self.assertIn("rules.json", match.suggestion)

    def test_the_posix_deny_text_is_unchanged(self):
        match = rules.prefilter_gui(_ctx("xdg-open https://example.com"), windows=False)
        self.assertEqual(
            match.detail,
            "`xdg-open` tries to open a GUI or browser on a headless server")
        self.assertIs(match.suggestion, rules.R6_SUGGESTION)

    def test_dry_run_reports_the_platform_default(self):
        ctx = _ctx("xdg-open https://example.com")
        self.assertEqual(rules.dry_run(ctx, overrides={}, windows=True), [])
        rows = rules.dry_run(ctx, overrides={}, windows=False)
        self.assertEqual([r["rule_id"] for r in rows], ["R6-gui-or-browser"])
        self.assertEqual(rows[0]["action"], "deny")


if __name__ == "__main__":
    unittest.main()
