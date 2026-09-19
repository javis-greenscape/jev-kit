import tests  # noqa: F401 -- MUST be the first import; see test_policy.py.

import unittest

from airlock import policy

_ABOVE_BAR_CONFIDENCE = 0.9
_ABOVE_BAR_MARGIN = 0.6


class TestFilenameSearchSuggestionWsl(unittest.TestCase):
    def test_wsl_windows_host_root_gets_es_wsl_suggestion(self):
        self.assertEqual(
            policy.filename_search_suggestion(wsl=True, roots=["/mnt/c/Users/x"]),
            policy.ES_WSL_SUGGESTION,
        )

    def test_wsl_linux_home_root_gets_plocate(self):
        self.assertEqual(
            policy.filename_search_suggestion(wsl=True, roots=["/home/alice"]),
            policy.PLOCATE_SUGGESTION,
        )

    def test_wsl_mixed_roots_get_both_indexes(self):
        # The real failing command's roots: a Linux root and a /mnt/c root.
        # Everything cannot search the Linux side and plocate cannot search
        # /mnt, so naming either one alone would silently drop half the search
        # (Codex P1 on PR #1; this test previously asserted the ES-only answer).
        self.assertEqual(
            policy.filename_search_suggestion(
                wsl=True, roots=["/home/alice", "/mnt/c/Users"],
            ),
            policy.ES_WSL_MIXED_SUGGESTION,
        )

    def test_wsl_windows_only_roots_get_es_wsl(self):
        self.assertEqual(
            policy.filename_search_suggestion(
                wsl=True, roots=["/mnt/c/Users", "/mnt/d/data"],
            ),
            policy.ES_WSL_SUGGESTION,
        )

    def test_wsl_no_roots_gets_plocate(self):
        self.assertEqual(
            policy.filename_search_suggestion(wsl=True, roots=None),
            policy.PLOCATE_SUGGESTION,
        )
        self.assertEqual(
            policy.filename_search_suggestion(wsl=True, roots=[]),
            policy.PLOCATE_SUGGESTION,
        )

    def test_native_windows_always_gets_es_suggestion_regardless_of_roots(self):
        self.assertEqual(
            policy.filename_search_suggestion(windows=True, roots=["/mnt/c/Users"]),
            policy.ES_SUGGESTION,
        )
        self.assertEqual(
            policy.filename_search_suggestion(windows=True, roots=None, wsl=True),
            policy.ES_SUGGESTION,
        )

    def test_non_wsl_linux_with_mnt_root_gets_plocate(self):
        # A non-WSL Linux box has no Everything to fall back to.
        self.assertEqual(
            policy.filename_search_suggestion(wsl=False, roots=["/mnt/c/Users"]),
            policy.PLOCATE_SUGGESTION,
        )


class TestRootIsWindowsHost(unittest.TestCase):
    def test_mnt_mount_roots_are_windows_host(self):
        self.assertTrue(policy.root_is_windows_host("/mnt/c/Users/x"))
        self.assertTrue(policy.root_is_windows_host("/mnt/c"))

    def test_native_windows_path_is_windows_host(self):
        self.assertTrue(policy.root_is_windows_host(r"C:\Users\alice"))

    def test_linux_paths_are_not_windows_host(self):
        self.assertFalse(policy.root_is_windows_host("/home/alice"))
        self.assertFalse(policy.root_is_windows_host("/mnt2/c/Users"))

    def test_none_and_empty_never_raise(self):
        self.assertFalse(policy.root_is_windows_host(None))
        self.assertFalse(policy.root_is_windows_host(""))

    def test_any_root_is_windows_host_over_none_list(self):
        self.assertFalse(policy.any_root_is_windows_host(None))
        self.assertFalse(policy.any_root_is_windows_host([]))
        self.assertTrue(policy.any_root_is_windows_host(["/home/alice", "/mnt/c/Users"]))


class TestCommandAlreadyUsesIndexedSearchWsl(unittest.TestCase):
    def test_es_command_recognised_under_wsl(self):
        self.assertTrue(
            policy.command_already_uses_indexed_search(
                'es -path "C:\\Users" -n 50 x', wsl=True,
            )
        )

    def test_same_command_not_recognised_off_wsl_off_windows(self):
        self.assertFalse(
            policy.command_already_uses_indexed_search(
                'es -path "C:\\Users" -n 50 x', wsl=False, windows=False,
            )
        )

    def test_command_already_uses_locate_still_works(self):
        self.assertTrue(policy.command_already_uses_locate("plocate -i foo"))
        self.assertFalse(policy.command_already_uses_locate("grep -r foo ."))


class TestEvaluateSearchWsl(unittest.TestCase):
    def test_disk_wide_filename_search_mnt_root_wsl_denies_with_es_wsl_suggestion(self):
        verdict = policy.evaluate_search(
            scope="disk_wide",
            search_intent="filename_search",
            confidence=_ABOVE_BAR_CONFIDENCE,
            command='find / -iname "*jev-kit*"',
            root_has_graphify_graph=False,
            margin=_ABOVE_BAR_MARGIN,
            roots=["/home/alice", "/mnt/c/Users"],
            wsl=True,
        )
        self.assertTrue(verdict["would_deny"])
        self.assertEqual(verdict["suggestion"], policy.ES_WSL_MIXED_SUGGESTION)

    def test_same_scenario_with_es_command_already_used_does_not_deny(self):
        verdict = policy.evaluate_search(
            scope="disk_wide",
            search_intent="filename_search",
            confidence=_ABOVE_BAR_CONFIDENCE,
            command='es -path "/mnt/c/Users" -n 50 "*jev-kit*"',
            root_has_graphify_graph=False,
            margin=_ABOVE_BAR_MARGIN,
            roots=["/home/alice", "/mnt/c/Users"],
            wsl=True,
        )
        self.assertFalse(verdict["would_deny"])



class TestMixedRootsNameBothIndexes(unittest.TestCase):
    """Codex P1 on PR #1: a disk-wide search spanning both filesystems was
    answered with the Everything suggestion alone, because any_root_is_windows_host
    is satisfied by a single /mnt root. Everything cannot search the Linux side,
    so that advice silently drops every result from the $HOME roots."""

    def test_mixed_roots_get_both_commands(self):
        s = policy.filename_search_suggestion(
            windows=False, roots=["/home/someone", "/mnt/c/Users"], wsl=True)
        self.assertIs(s, policy.ES_WSL_MIXED_SUGGESTION)
        self.assertIn("plocate", s)
        self.assertIn("es -path", s)

    def test_windows_only_roots_still_get_everything_alone(self):
        self.assertIs(
            policy.filename_search_suggestion(
                windows=False, roots=["/mnt/c/Users", "/mnt/d/data"], wsl=True),
            policy.ES_WSL_SUGGESTION)

    def test_linux_only_roots_still_get_plocate_alone(self):
        self.assertIs(
            policy.filename_search_suggestion(
                windows=False, roots=["/home/someone", "/opt"], wsl=True),
            policy.PLOCATE_SUGGESTION)

    def test_the_command_that_started_this(self):
        """`find "$HOME" /mnt/c/Users -name x` -- the real failing call."""
        self.assertIs(
            policy.filename_search_suggestion(
                windows=False, roots=["/home/someone", "/mnt/c/Users"], wsl=True),
            policy.ES_WSL_MIXED_SUGGESTION)

    def test_any_root_is_linux_side(self):
        self.assertTrue(policy.any_root_is_linux_side(["/home/x", "/mnt/c"]))
        self.assertTrue(policy.any_root_is_linux_side(["/opt"]))
        self.assertFalse(policy.any_root_is_linux_side(["/mnt/c/Users"]))
        self.assertFalse(policy.any_root_is_linux_side([]))
        self.assertFalse(policy.any_root_is_linux_side(None))


class TestWslFsRootSpansBothIndexes(unittest.TestCase):
    """Codex P1 on PR #1: `find / -name x` under WSL has roots=["/"], which
    any_root_is_windows_host answered False for (it isn't a /mnt/<drive> or
    Windows-looking path), so it fell through to PLOCATE_SUGGESTION alone --
    even though traversing '/' also traverses every mounted Windows drive
    under /mnt, and Everything's ground was silently dropped."""

    def test_fs_root_alone_gets_mixed_suggestion(self):
        self.assertEqual(
            policy.filename_search_suggestion(wsl=True, roots=["/"]),
            policy.ES_WSL_MIXED_SUGGESTION,
        )

    def test_fs_root_with_other_roots_still_gets_mixed_suggestion(self):
        self.assertEqual(
            policy.filename_search_suggestion(wsl=True, roots=["/", "/mnt/c/Users"]),
            policy.ES_WSL_MIXED_SUGGESTION,
        )

    def test_fs_root_off_wsl_gets_plocate_alone(self):
        # No Everything to fall back to off WSL.
        self.assertEqual(
            policy.filename_search_suggestion(wsl=False, roots=["/"]),
            policy.PLOCATE_SUGGESTION,
        )

    def test_root_is_wsl_fs_root(self):
        self.assertTrue(policy.root_is_wsl_fs_root("/"))
        self.assertFalse(policy.root_is_wsl_fs_root("/home/alice"))
        self.assertFalse(policy.root_is_wsl_fs_root("/mnt/c"))
        self.assertFalse(policy.root_is_wsl_fs_root(None))
        self.assertFalse(policy.root_is_wsl_fs_root(""))

    def test_evaluate_search_fs_root_wsl_denies_with_mixed_suggestion(self):
        verdict = policy.evaluate_search(
            scope="disk_wide",
            search_intent="filename_search",
            confidence=_ABOVE_BAR_CONFIDENCE,
            command='find / -iname "*jev-kit*"',
            root_has_graphify_graph=False,
            margin=_ABOVE_BAR_MARGIN,
            roots=["/"],
            wsl=True,
        )
        self.assertTrue(verdict["would_deny"])
        self.assertEqual(verdict["suggestion"], policy.ES_WSL_MIXED_SUGGESTION)


class TestEsOnlyInCommandPosition(unittest.TestCase):
    """Codex P2 on PR #1: _ES_RE accepted `es` in any whitespace-delimited
    argument, so `find / -name es` read as already-indexed and suppressed the
    deny it should have produced."""

    def test_es_as_an_argument_is_not_a_command(self):
        self.assertFalse(policy.command_already_uses_indexed_search(
            "find / -name es", windows=False, wsl=True))

    def test_quoted_es_argument_is_not_a_command(self):
        self.assertFalse(policy.command_already_uses_indexed_search(
            'find /mnt/c -name "es"', windows=False, wsl=True))

    def test_es_at_the_start_is_a_command(self):
        self.assertTrue(policy.command_already_uses_indexed_search(
            'es -path "/mnt/c" -n 50 "x"', windows=False, wsl=True))

    def test_es_after_a_separator_is_a_command(self):
        for cmd in ('cd /tmp && es -n 5 "x"', 'ls | es "x"', 'true; es "x"'):
            with self.subTest(cmd=cmd):
                self.assertTrue(policy.command_already_uses_indexed_search(
                    cmd, windows=False, wsl=True))

    def test_es_exe_still_recognised(self):
        self.assertTrue(policy.command_already_uses_indexed_search(
            'es.exe -n 5 "x"', windows=False, wsl=True))

    def test_es_inside_a_word_is_not_a_command(self):
        self.assertFalse(policy.command_already_uses_indexed_search(
            "grep -r bytes .", windows=False, wsl=True))


class TestEsQuotingIsHonored(unittest.TestCase):
    """Codex P2, round 2 on PR #1: the raw _ES_RE regex ignored quoting, so a
    filename pattern that happens to contain a separator followed by `es`
    was misread as a second command invoking Everything."""

    def test_semicolon_and_es_inside_a_quoted_argument_is_not_a_command(self):
        self.assertFalse(policy.command_already_uses_indexed_search(
            'find /mnt/c -name "foo; es bar"', windows=False, wsl=True))

    def test_pipe_and_es_inside_a_quoted_argument_is_not_a_command(self):
        self.assertFalse(policy.command_already_uses_indexed_search(
            'find /mnt/c -name "foo| es bar"', windows=False, wsl=True))

    def test_ampersand_and_es_inside_a_quoted_argument_is_not_a_command(self):
        self.assertFalse(policy.command_already_uses_indexed_search(
            "find /mnt/c -name 'foo&& es bar'", windows=False, wsl=True))

    def test_a_real_es_command_after_a_quoted_lookalike_is_still_found(self):
        # The first stage's quoted argument LOOKS like a separator+es, but
        # the second stage is a genuine es invocation.
        self.assertTrue(policy.command_already_uses_indexed_search(
            'find /mnt/c -name "foo; es bar"; es -path "/mnt/c" -n 50 "x"',
            windows=False, wsl=True))

    def test_native_windows_also_honors_quoting(self):
        self.assertFalse(policy.command_already_uses_indexed_search(
            'find /mnt/c -name "foo; es bar"', windows=True))


if __name__ == "__main__":
    unittest.main()
