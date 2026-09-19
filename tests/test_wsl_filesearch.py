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
            policy.filename_search_suggestion(wsl=True, roots=["/home/grafe"]),
            policy.PLOCATE_SUGGESTION,
        )

    def test_wsl_mixed_roots_with_a_windows_host_root_gets_es_wsl(self):
        # The real failing command's roots: a Linux root and a /mnt/c root.
        self.assertEqual(
            policy.filename_search_suggestion(
                wsl=True, roots=["/home/grafe", "/mnt/c/Users"],
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
        self.assertFalse(policy.root_is_windows_host("/home/grafe"))
        self.assertFalse(policy.root_is_windows_host("/mnt2/c/Users"))

    def test_none_and_empty_never_raise(self):
        self.assertFalse(policy.root_is_windows_host(None))
        self.assertFalse(policy.root_is_windows_host(""))

    def test_any_root_is_windows_host_over_none_list(self):
        self.assertFalse(policy.any_root_is_windows_host(None))
        self.assertFalse(policy.any_root_is_windows_host([]))
        self.assertTrue(policy.any_root_is_windows_host(["/home/grafe", "/mnt/c/Users"]))


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
            roots=["/home/grafe", "/mnt/c/Users"],
            wsl=True,
        )
        self.assertTrue(verdict["would_deny"])
        self.assertEqual(verdict["suggestion"], policy.ES_WSL_SUGGESTION)

    def test_same_scenario_with_es_command_already_used_does_not_deny(self):
        verdict = policy.evaluate_search(
            scope="disk_wide",
            search_intent="filename_search",
            confidence=_ABOVE_BAR_CONFIDENCE,
            command='es -path "/mnt/c/Users" -n 50 "*jev-kit*"',
            root_has_graphify_graph=False,
            margin=_ABOVE_BAR_MARGIN,
            roots=["/home/grafe", "/mnt/c/Users"],
            wsl=True,
        )
        self.assertFalse(verdict["would_deny"])


if __name__ == "__main__":
    unittest.main()
