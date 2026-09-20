import tests  # noqa: F401 -- MUST be the first import; see test_policy.py.

import os
import unittest
from unittest import mock

from airlock import policy

_ABOVE_BAR_CONFIDENCE = 0.9
_ABOVE_BAR_MARGIN = 0.6

# Every fixture root here is under /home/alice, matching winpath.py's
# docstring convention. plocate's home.db covers $HOME and nothing else, so
# whether /home/alice counts as indexed ground depends on HOME (Codex P1,
# PR #1: /opt was being reported as plocate's ground). Pin it for the file.
_HOME = mock.patch.dict(os.environ, {"HOME": "/home/alice"})


# The suggestion now depends on what the machine has: Everything's client
# on PATH, and which plocate database exists. Pin both, so the file asserts
# the policy rather than the box it runs on.
# Captured before the pin below replaces it, so the one test that checks the
# detection itself can still reach the real function.
_REAL_ES_AVAILABLE = policy.es_available

_AVAIL = mock.patch.multiple(policy,
                             es_available=lambda *a, **k: True,
                             plocate_db_kind=lambda *a, **k: "home")


def setUpModule():
    _HOME.start()
    _AVAIL.start()


def tearDownModule():
    _AVAIL.stop()
    _HOME.stop()


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
        self.assertTrue(policy.root_is_windows_host(r"C:\Users\alice",
                                                    windows=True))

    def test_msys_drive_is_a_linux_path_under_wsl(self):
        # /c/projects on WSL is an ordinary Linux directory. Everything
        # cannot search it, so reading it as a drive would deny a working
        # crawl and hand back a query for a path that does not exist.
        self.assertFalse(policy.root_is_windows_host("/c/projects",
                                                     windows=False))
        self.assertTrue(policy.root_is_windows_host("/c/projects",
                                                    windows=True))

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

    def _mixed_verdict(self, command):
        return policy.evaluate_search(
            scope="disk_wide",
            search_intent="filename_search",
            confidence=_ABOVE_BAR_CONFIDENCE,
            command=command,
            root_has_graphify_graph=False,
            margin=_ABOVE_BAR_MARGIN,
            roots=["/home/alice", "/mnt/c/Users"],
            wsl=True,
        )

    def test_es_alone_does_not_cover_a_mixed_search(self):
        # Codex P2, PR #1: es indexes the Windows half only, so the crawl of
        # the Linux half is still unanswered and the deny must stand.
        verdict = self._mixed_verdict(
            'find "$HOME" /mnt/c/Users -name x; es -path "C:\\Users" x')
        self.assertTrue(verdict["would_deny"])

    def test_plocate_alone_does_not_cover_a_mixed_search(self):
        verdict = self._mixed_verdict(
            'find "$HOME" /mnt/c/Users -name x; plocate -i x')
        self.assertTrue(verdict["would_deny"])

    def test_both_indexes_named_cover_a_mixed_search(self):
        verdict = self._mixed_verdict(
            'plocate -d ~/.cache/plocate/home.db -i x; es -path "C:\\Users" x')
        self.assertFalse(verdict["would_deny"])

    def test_windows_host_root_denies_even_below_disk_wide(self):
        # Codex P1, PR #1: `find /mnt/c/Users -name x` scopes as single_dir,
        # so the old disk_wide-only gate let a crawl of the Windows
        # filesystem through untouched.
        verdict = policy.evaluate_search(
            scope="single_dir",
            search_intent="filename_search",
            confidence=_ABOVE_BAR_CONFIDENCE,
            command="find /mnt/c/Users -name x",
            root_has_graphify_graph=False,
            margin=_ABOVE_BAR_MARGIN,
            roots=["/mnt/c/Users"],
            wsl=True,
        )
        self.assertTrue(verdict["would_deny"])
        self.assertEqual(verdict["suggestion"], policy.ES_WSL_SUGGESTION)

    def test_a_linux_side_single_dir_search_still_allows(self):
        verdict = policy.evaluate_search(
            scope="single_dir",
            search_intent="filename_search",
            confidence=_ABOVE_BAR_CONFIDENCE,
            command="find /home/alice/notes -name x",
            root_has_graphify_graph=False,
            margin=_ABOVE_BAR_MARGIN,
            roots=["/home/alice/notes"],
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
            windows=False, roots=["/home/alice/notes", "/mnt/c/Users"], wsl=True)
        self.assertIs(s, policy.ES_WSL_MIXED_SUGGESTION)
        self.assertIn("plocate", s)
        self.assertIn("es -path", s)

    def test_windows_only_roots_still_get_everything_alone(self):
        self.assertIs(
            policy.filename_search_suggestion(
                windows=False, roots=["/mnt/c/Users", "/mnt/d/data"], wsl=True),
            policy.ES_WSL_SUGGESTION)

    def test_home_only_roots_still_get_plocate_alone(self):
        self.assertIs(
            policy.filename_search_suggestion(
                windows=False, roots=["/home/alice", "/home/alice/notes"], wsl=True),
            policy.PLOCATE_SUGGESTION)

    def test_a_linux_root_outside_home_is_not_plocate_ground(self):
        # Codex P1, PR #1: home.db indexes $HOME only, so promising plocate
        # for /opt reports every file there as absent.
        self.assertIs(
            policy.filename_search_suggestion(
                windows=False, roots=["/home/alice", "/opt"], wsl=True),
            policy.ES_WSL_WHOLE_FS_SUGGESTION)
        self.assertIs(
            policy.filename_search_suggestion(
                windows=False, roots=["/opt", "/mnt/c"], wsl=True),
            policy.ES_WSL_WHOLE_FS_SUGGESTION)

    def test_the_command_that_started_this(self):
        """`find "$HOME" /mnt/c/Users -name x` -- the real failing call."""
        self.assertIs(
            policy.filename_search_suggestion(
                windows=False, roots=["/home/alice/notes", "/mnt/c/Users"], wsl=True),
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
            policy.ES_WSL_WHOLE_FS_SUGGESTION,
        )

    def test_fs_root_with_other_roots_still_gets_mixed_suggestion(self):
        self.assertEqual(
            policy.filename_search_suggestion(wsl=True, roots=["/", "/mnt/c/Users"]),
            policy.ES_WSL_WHOLE_FS_SUGGESTION,
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
        self.assertEqual(verdict["suggestion"], policy.ES_WSL_WHOLE_FS_SUGGESTION)


class TestPrefilterLetsWindowsHostRootsThrough(unittest.TestCase):
    """Codex P1 on PR #1: evaluate_search grew a Windows-host branch, but
    compute_search_entry asks deny_possible_bash first and that still said
    no deny was reachable below disk_wide, so the branch never ran live."""

    def test_windows_host_root_below_disk_wide_is_deny_possible(self):
        self.assertTrue(policy.deny_possible_bash(
            "single_dir", "find", False, roots=["/mnt/c/Users"], wsl=True))

    def test_the_same_root_off_wsl_is_not(self):
        self.assertFalse(policy.deny_possible_bash(
            "single_dir", "find", False, roots=["/mnt/c/Users"], wsl=False))

    def test_a_linux_side_single_dir_is_still_skipped(self):
        self.assertFalse(policy.deny_possible_bash(
            "single_dir", "find", False, roots=["/home/alice/notes"], wsl=True))

    def test_a_plocate_command_that_still_crawls_the_host_is_judged(self):
        # Codex P2, PR #1: the program is plocate, so the prefilter skipped
        # the call, but another stage crawls /mnt/c/Users.
        self.assertTrue(policy.deny_possible_bash(
            "disk_wide", "plocate", False,
            roots=["/home/alice", "/mnt/c/Users"], wsl=True))

    def test_an_es_command_is_still_skipped(self):
        self.assertFalse(policy.deny_possible_bash(
            "single_dir", "es", False, roots=["/mnt/c/Users"], wsl=True))


class TestTheAdviceMatchesTheMachine(unittest.TestCase):
    """Found by audit, not by review: every suggestion above names tools
    and a database that this repository's author happens to have. On any
    other machine the deny would block the crawl and hand back a command
    that fails."""

    def test_no_everything_client_means_no_deny_for_a_windows_root(self):
        with mock.patch.object(policy, "es_available", lambda *a, **k: False):
            self.assertIsNone(policy.filename_search_suggestion(
                windows=False, roots=["/mnt/c/Users"], wsl=True,
                has_es=False))
            verdict = policy.evaluate_search(
                scope="disk_wide", search_intent="filename_search",
                confidence=_ABOVE_BAR_CONFIDENCE, command="find /mnt/c -name x",
                root_has_graphify_graph=False, margin=_ABOVE_BAR_MARGIN,
                roots=["/mnt/c"], wsl=True, has_es=False)
            self.assertFalse(verdict["would_deny"])
            self.assertIsNone(verdict["suggestion"])

    def test_no_plocate_database_means_no_deny_on_plain_linux(self):
        self.assertIsNone(policy.filename_search_suggestion(
            windows=False, roots=["/home/alice"], wsl=False, db_kind=None,
            has_es=False))

    def test_a_system_database_names_itself_and_covers_the_linux_side(self):
        self.assertEqual(
            policy.filename_search_suggestion(
                windows=False, roots=["/opt"], wsl=False, db_kind="system"),
            "plocate -i '<pattern>'")
        # /opt IS indexed by the system database, so a mixed search gets
        # the two-index advice rather than the keep-crawling one.
        s = policy.filename_search_suggestion(
            windows=False, roots=["/opt", "/mnt/c"], wsl=True,
            db_kind="system")
        self.assertIn("plocate -i '<pattern>'", s)
        self.assertIn("es -path", s)
        self.assertNotIn("home.db", s)

    def test_locate_without_plocate_is_named_as_locate(self):
        # A machine with locate but no plocate was handed `plocate -i ...`,
        # a command it cannot run.
        self.assertEqual(
            policy.plocate_command("system-locate"), "locate -i '<pattern>'")
        self.assertEqual(
            policy.plocate_command("home-locate"),
            "locate -d ~/.cache/plocate/home.db -i '<pattern>'")
        # The -locate variants cover exactly the ground their base kind does.
        self.assertTrue(policy.root_is_plocate_covered(
            "/opt", db_kind="system-locate"))
        self.assertFalse(policy.root_is_plocate_covered(
            "/opt", home="/home/alice", db_kind="home-locate"))

    def test_probes_do_not_depend_on_what_this_host_has_installed(self):
        # The generated policy and its pinned fingerprint must read the same
        # on a machine with no index at all.
        with mock.patch.object(policy, "plocate_db_kind",
                               lambda *a, **k: None), \
                mock.patch.object(policy, "es_available",
                                  lambda *a, **k: False):
            self.assertEqual(
                policy.filename_search_suggestion(
                    windows=False, roots=["/home/alice"], wsl=False),
                policy.PLOCATE_SUGGESTION)
            verdict = policy.evaluate_search(
                scope="disk_wide", search_intent="filename_search",
                confidence=_ABOVE_BAR_CONFIDENCE, command="find / -name x",
                root_has_graphify_graph=False, margin=_ABOVE_BAR_MARGIN,
                roots=["/"], wsl=False)
            self.assertTrue(verdict["would_deny"])

    def test_find_without_L_does_not_follow_a_symlink_root(self):
        # GNU find examines a symlink root itself; it never enters the
        # target, so the search visits nothing on the Windows host.
        self.assertFalse(policy.command_follows_symlinks("find link -name x"))
        self.assertTrue(policy.command_follows_symlinks("find -L link -name x"))
        self.assertTrue(policy.command_follows_symlinks("find -H link -name x"))
        # Anything that is not find follows what the path resolves to.
        self.assertTrue(policy.command_follows_symlinks("rg -l foo link"))
        self.assertTrue(policy.command_follows_symlinks(None))

    def test_a_stopped_everything_service_is_not_a_usable_index(self):
        # es on PATH with the service stopped returns nothing, so a deny
        # would block a working crawl for a command that finds no files.
        policy._AVAILABILITY_CACHE.clear()
        try:
            with mock.patch.object(policy, "_tool_on_path",
                                   lambda *a, **k: True), \
                    mock.patch("airlock.everything.status",
                               lambda *a, **k: {"ok": False}):
                self.assertFalse(_REAL_ES_AVAILABLE(windows=False))
            policy._AVAILABILITY_CACHE.clear()
            with mock.patch.object(policy, "_tool_on_path",
                                   lambda *a, **k: True), \
                    mock.patch("airlock.everything.status",
                               lambda *a, **k: {"ok": True}):
                self.assertTrue(_REAL_ES_AVAILABLE(windows=False))
        finally:
            policy._AVAILABILITY_CACHE.clear()

    def test_the_home_database_does_not_claim_ground_it_lacks(self):
        with mock.patch.object(policy, "plocate_db_kind", lambda *a, **k: "home"):
            self.assertIs(
                policy.filename_search_suggestion(
                    windows=False, roots=["/opt", "/mnt/c"], wsl=True),
                policy.ES_WSL_WHOLE_FS_SUGGESTION)


class TestASymlinkOutOfHomeIsWindowsGround(unittest.TestCase):
    """Found by audit: ~/notes/vault on the author's machine is a symlink
    into /mnt/c. Classifying the unresolved path calls it Linux-side and
    steers to plocate, whose index never followed the symlink -- the same
    "file reported absent" failure this branch was opened to fix."""

    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.link = os.path.join(self.tmp.name, "vault")
        self.target = os.path.join(self.tmp.name, "mnt_c_target")
        os.makedirs(self.target)
        os.symlink(self.target, self.link)

    def test_resolve_root_follows_the_symlink(self):
        self.assertEqual(policy.resolve_root(self.link), self.target)

    def test_resolve_root_survives_a_broken_link(self):
        broken = os.path.join(self.tmp.name, "gone")
        os.symlink(os.path.join(self.tmp.name, "nothing-here"), broken)
        self.assertTrue(policy.resolve_root(broken))

    def test_a_home_path_resolving_onto_the_windows_host_gets_everything(self):
        with mock.patch.object(policy, "resolve_root",
                               lambda r: "/mnt/c/Users/x" if r == "~/notes/vault" else r):
            self.assertIs(
                policy.filename_search_suggestion(
                    windows=False, roots=["~/notes/vault"], wsl=True),
                policy.ES_WSL_SUGGESTION)


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

    def test_escaped_separator_is_not_a_command_boundary(self):
        # Codex P2, PR #1: bash reads `foo\;es` as one filename pattern.
        self.assertFalse(policy.command_already_uses_indexed_search(
            r"find /mnt/c -name foo\;es", windows=False, wsl=True))

    def test_escaped_separator_inside_double_quotes_is_not_a_boundary(self):
        self.assertFalse(policy.command_already_uses_indexed_search(
            r'find /mnt/c -name "foo\;es"', windows=False, wsl=True))

    def test_whole_fs_suggestion_keeps_the_linux_ground_neither_index_holds(self):
        for d in ("/etc", "/opt", "/usr"):
            self.assertIn(d, policy.ES_WSL_WHOLE_FS_SUGGESTION)

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
