
import tests  # noqa: F401, I001 -- MUST be the first import. `python3 -m unittest
# discover -s tests` runs with start_dir == top_level_dir, so unittest treats
# `tests/` as a flat directory of top-level modules and never executes
# tests/__init__.py as a package init (name == '.' in TestLoader._find_tests).
# Importing it explicitly, here, first, is what actually runs its HOME/
# AIRLOCK_*-isolating fixture before any airlock.* module resolves a real path.

import unittest
from unittest import mock

from airlock import policy, guards

# These tests assert the guard's behaviour, not what this machine happens to
# have installed. The live guard detects its replacement commands, so a runner
# without a plocate database would see every deny-path test fail for a reason
# that has nothing to do with the code under test (Codex P1, PR #1). Pin the
# detection for the file; the tests that check detection itself live in
# tests/test_wsl_filesearch.py.
_AVAIL = mock.patch.object(policy, "detect_availability",
                           lambda *a, **k: ("home", True))


def setUpModule():
    _AVAIL.start()


def tearDownModule():
    _AVAIL.stop()



def _fake_tier_response(task_kind="judgement", confidence=0.9, prior_failed=0.0):
    other = "unclear" if task_kind != "unclear" else "lookup"
    return (
        {
            "model": "jev-1.13.0",
            "answers": {
                "task_kind": {
                    "type": "choice",
                    "choice": task_kind,
                    "confidence": confidence,
                    "probabilities": {task_kind: confidence, other: max(0.0, 1.0 - confidence)},
                },
                "states_prior_failed_attempts": {"type": "noul", "noul": prior_failed},
                "brief_is_self_contained": {"type": "noul", "noul": 0.8},
            },
            "usage": {"input_tokens": 300, "output_tokens": 40},
        },
        123,
    )


class TestRunTierGuard(unittest.TestCase):
    def test_no_api_key_makes_no_call_and_logs_nothing(self):
        data = {
            "session_id": "s1",
            "cwd": "/tmp",
            "tool_name": "Agent",
            "tool_input": {"subagent_type": "fable", "description": "d", "prompt": "p"},
        }
        with mock.patch("airlock.keyfile.get_api_key", return_value=None), \
             mock.patch("airlock.client.ask") as call, \
             mock.patch("airlock.log.append") as append:
            guards.run_tier_guard(data)
            call.assert_not_called()
            append.assert_not_called()

    def test_would_deny_logged_for_mismatched_rung(self):
        data = {
            "session_id": "s1",
            "cwd": "/tmp",
            "tool_name": "Agent",
            "tool_input": {
                "subagent_type": "fable",
                "description": "a plain lookup",
                "prompt": "Find where X is defined",
            },
        }
        with mock.patch("airlock.keyfile.get_api_key", return_value="key"), \
             mock.patch("airlock.client.ask", return_value=_fake_tier_response(task_kind="lookup", confidence=0.95, prior_failed=0.9)), \
             mock.patch("airlock.log.append") as append:
            guards.run_tier_guard(data)
            append.assert_called_once()
            entry = append.call_args[0][0]
            self.assertTrue(entry["would_deny"])
            self.assertEqual(entry["guard"], "tier_guard")
            self.assertIn("answers", entry)

    def test_api_error_logs_error_field_and_does_not_raise(self):
        data = {
            "session_id": "s1",
            "cwd": "/tmp",
            "tool_name": "Agent",
            "tool_input": {"subagent_type": "worker", "description": "d", "prompt": "p"},
        }
        with mock.patch("airlock.keyfile.get_api_key", return_value="key"), \
             mock.patch("airlock.client.ask", side_effect=RuntimeError("boom")), \
             mock.patch("airlock.log.append") as append:
            guards.run_tier_guard(data)
            entry = append.call_args[0][0]
            self.assertIn("error", entry)

    def test_secrets_never_reach_the_state_sent_to_jev(self):
        data = {
            "session_id": "s1",
            "cwd": "/tmp",
            "tool_name": "Agent",
            "tool_input": {
                "subagent_type": "worker",
                "description": "do the thing",
                "prompt": "use apikey_SUPERSECRET123 to call the api",
            },
        }
        with mock.patch("airlock.keyfile.get_api_key", return_value="key"), \
             mock.patch("airlock.client.ask", return_value=_fake_tier_response()) as call, \
             mock.patch("airlock.log.append"):
            guards.run_tier_guard(data)
            sent_body = call.call_args[0][0]
            self.assertNotIn("apikey_SUPERSECRET123", str(sent_body["state"]))


class TestSkipNoDenyPossible(unittest.TestCase):
    """Job 1: skip the Jev call entirely when a deny is not reachable."""

    def test_scout_find_agent_dispatch_skips_call(self):
        data = {
            "session_id": "s1",
            "cwd": "/tmp",
            "tool_name": "Agent",
            "tool_input": {"subagent_type": "scout-find", "description": "d", "prompt": "p"},
        }
        with mock.patch("airlock.keyfile.get_api_key", return_value="key"), \
             mock.patch("random.random", return_value=0.99), \
             mock.patch("airlock.client.ask") as call, \
             mock.patch("airlock.log.append") as append:
            guards.run_tier_guard(data)
            call.assert_not_called()
            entry = append.call_args[0][0]
            self.assertEqual(entry["skipped"], "no_deny_possible")
            self.assertFalse(entry["would_deny"])

    def test_scout_find_agent_dispatch_sampled_still_calls(self):
        data = {
            "session_id": "s1",
            "cwd": "/tmp",
            "tool_name": "Agent",
            "tool_input": {"subagent_type": "scout-find", "description": "d", "prompt": "p"},
        }
        with mock.patch("airlock.keyfile.get_api_key", return_value="key"), \
             mock.patch("random.random", return_value=0.0), \
             mock.patch("airlock.client.ask", return_value=_fake_tier_response()) as call, \
             mock.patch("airlock.log.append") as append:
            guards.run_tier_guard(data)
            call.assert_called_once()
            entry = append.call_args[0][0]
            self.assertEqual(entry["skipped"], "sampled_shadow")
            self.assertFalse(entry["would_deny"])

    def test_non_scout_find_agent_dispatch_still_judged(self):
        data = {
            "session_id": "s1",
            "cwd": "/tmp",
            "tool_name": "Agent",
            "tool_input": {"subagent_type": "fable", "description": "d", "prompt": "p"},
        }
        with mock.patch("airlock.keyfile.get_api_key", return_value="key"), \
             mock.patch("random.random", return_value=0.99), \
             mock.patch("airlock.client.ask", return_value=_fake_tier_response()) as call, \
             mock.patch("airlock.log.append"):
            guards.run_tier_guard(data)
            call.assert_called_once()

    def test_single_repo_grep_without_graph_skips_call(self):
        data = {"session_id": "s1", "cwd": "/tmp", "tool_name": "Bash", "tool_input": {"command": "grep -rn foo ."}}
        with mock.patch("airlock.keyfile.get_api_key", return_value="key"), \
             mock.patch("airlock.scope.root_has_graphify_graph", return_value=False), \
             mock.patch("random.random", return_value=0.99), \
             mock.patch("airlock.client.ask") as call, \
             mock.patch("airlock.log.append") as append:
            guards.run_tool_choice_guard(data)
            call.assert_not_called()
            entry = append.call_args[0][0]
            self.assertEqual(entry["skipped"], "no_deny_possible")

    def test_single_repo_grep_with_graph_still_judged(self):
        data = {"session_id": "s1", "cwd": "/tmp", "tool_name": "Bash", "tool_input": {"command": "grep -rn foo ."}}
        fake = (
            {
                "model": "jev-1.13.0",
                "answers": {
                    "search_intent": {
                        "type": "choice",
                        "choice": "code_structure_search",
                        "confidence": 0.9,
                        "probabilities": {"code_structure_search": 0.9, "literal_text_search": 0.1},
                    }
                },
                "usage": {"input_tokens": 100, "output_tokens": 10},
            },
            50,
        )
        with mock.patch("airlock.keyfile.get_api_key", return_value="key"), \
             mock.patch("airlock.scope.root_has_graphify_graph", return_value=True), \
             mock.patch("random.random", return_value=0.99), \
             mock.patch("airlock.client.ask", return_value=fake) as call, \
             mock.patch("airlock.log.append") as append:
            guards.run_tool_choice_guard(data)
            call.assert_called_once()
            entry = append.call_args[0][0]
            self.assertTrue(entry["would_deny"])

    def test_disk_wide_find_still_judged(self):
        data = {"session_id": "s1", "cwd": "/tmp", "tool_name": "Bash", "tool_input": {"command": "find / -name '*.xlsm'"}}
        fake = (
            {
                "model": "jev-1.13.0",
                "answers": {
                    "search_intent": {
                        "type": "choice",
                        "choice": "filename_search",
                        "confidence": 0.95,
                        "probabilities": {"filename_search": 0.95, "not_a_search": 0.05},
                    }
                },
                "usage": {"input_tokens": 100, "output_tokens": 10},
            },
            50,
        )
        with mock.patch("airlock.keyfile.get_api_key", return_value="key"), \
             mock.patch("random.random", return_value=0.99), \
             mock.patch("airlock.client.ask", return_value=fake) as call, \
             mock.patch("airlock.log.append"):
            guards.run_tool_choice_guard(data)
            call.assert_called_once()

    def test_sampled_skip_never_would_deny_even_if_answer_says_so(self):
        data = {"session_id": "s1", "cwd": "/tmp", "tool_name": "Bash", "tool_input": {"command": "grep -rn foo ."}}
        fake = (
            {
                "model": "jev-1.13.0",
                "answers": {
                    "search_intent": {
                        "type": "choice",
                        "choice": "code_structure_search",
                        "confidence": 0.95,
                        "probabilities": {"code_structure_search": 0.95, "literal_text_search": 0.05},
                    }
                },
                "usage": {"input_tokens": 100, "output_tokens": 10},
            },
            50,
        )
        with mock.patch("airlock.keyfile.get_api_key", return_value="key"), \
             mock.patch("airlock.scope.root_has_graphify_graph", return_value=False), \
             mock.patch("random.random", return_value=0.0), \
             mock.patch("airlock.client.ask", return_value=fake), \
             mock.patch("airlock.log.append") as append:
            guards.run_tool_choice_guard(data)
            entry = append.call_args[0][0]
            self.assertEqual(entry["skipped"], "sampled_shadow")
            self.assertFalse(entry["would_deny"])


class TestRunToolChoiceGuard(unittest.TestCase):
    def test_non_search_command_makes_no_call(self):
        data = {"session_id": "s1", "cwd": "/tmp", "tool_name": "Bash", "tool_input": {"command": "npm install"}}
        with mock.patch("airlock.client.ask") as call, mock.patch("airlock.log.append") as append:
            guards.run_tool_choice_guard(data)
            call.assert_not_called()
            append.assert_not_called()

    def test_search_command_without_key_makes_no_call(self):
        data = {"session_id": "s1", "cwd": "/tmp", "tool_name": "Bash", "tool_input": {"command": "find / -name '*.xlsm'"}}
        with mock.patch("airlock.keyfile.get_api_key", return_value=None), \
             mock.patch("airlock.client.ask") as call:
            guards.run_tool_choice_guard(data)
            call.assert_not_called()

    def test_disk_wide_search_would_deny(self):
        data = {"session_id": "s1", "cwd": "/tmp", "tool_name": "Bash", "tool_input": {"command": "find / -name '*.xlsm'"}}
        fake = (
            {
                "model": "jev-1.13.0",
                "answers": {
                    "search_intent": {
                        "type": "choice",
                        "choice": "filename_search",
                        "confidence": 0.95,
                        "probabilities": {"filename_search": 0.95, "not_a_search": 0.05},
                    }
                },
                "usage": {"input_tokens": 100, "output_tokens": 10},
            },
            50,
        )
        with mock.patch("airlock.keyfile.get_api_key", return_value="key"), \
             mock.patch("airlock.client.ask", return_value=fake), \
             mock.patch("airlock.log.append") as append:
            guards.run_tool_choice_guard(data)
            entry = append.call_args[0][0]
            self.assertEqual(entry["scope"], "disk_wide")
            self.assertTrue(entry["would_deny"])
            # The verdict is platform-neutral; the suggestion text is not, so it
        # is compared against the function that produces it rather than
        # against one platform's literal string. roots=["/"] matches what
        # scope.classify_command actually extracts from "find / -name ...".
        _db, _es = policy.detect_availability()
        self.assertEqual(entry["suggestion"], policy.filename_search_suggestion(
            roots=["/"], db_kind=_db, has_es=_es))


class TestRootHasCodeGraphField(unittest.TestCase):
    """The log row must carry `root_has_code_graph` -- the value the policy
    actually used -- ONLY on a row where the code_structure_search branch was
    actually evaluated (a Jev call was made and evaluate_search ran), and
    must never stat the filesystem or fill it in any other row."""

    def _fake_code_structure_answer(self, confidence=0.9):
        return (
            {
                "model": "jev-1.13.0",
                "answers": {
                    "search_intent": {
                        "type": "choice",
                        "choice": "code_structure_search",
                        "confidence": confidence,
                        "probabilities": {"code_structure_search": confidence,
                                          "literal_text_search": 1 - confidence},
                    }
                },
                "usage": {"input_tokens": 100, "output_tokens": 10},
            },
            50,
        )

    def test_present_and_true_when_graph_present_and_branch_evaluated(self):
        data = {"session_id": "s1", "cwd": "/tmp", "tool_name": "Bash", "tool_input": {"command": "grep -rn foo ."}}
        with mock.patch("airlock.keyfile.get_api_key", return_value="key"), \
             mock.patch("airlock.scope.root_has_graphify_graph", return_value=True), \
             mock.patch("random.random", return_value=0.99), \
             mock.patch("airlock.client.ask", return_value=self._fake_code_structure_answer()), \
             mock.patch("airlock.log.append") as append:
            guards.run_tool_choice_guard(data)
            entry = append.call_args[0][0]
            self.assertIn("root_has_code_graph", entry)
            self.assertIs(entry["root_has_code_graph"], True)

    def test_present_and_false_when_sampled_without_a_graph(self):
        """Sampled shadow traffic (no deny possible) still evaluates the
        branch and still records the fact -- would_deny is forced False
        afterwards, but root_has_code_graph is the real code-side value."""
        data = {"session_id": "s1", "cwd": "/tmp", "tool_name": "Bash", "tool_input": {"command": "grep -rn foo ."}}
        with mock.patch("airlock.keyfile.get_api_key", return_value="key"), \
             mock.patch("airlock.scope.root_has_graphify_graph", return_value=False), \
             mock.patch("random.random", return_value=0.0), \
             mock.patch("airlock.client.ask", return_value=self._fake_code_structure_answer()), \
             mock.patch("airlock.log.append") as append:
            guards.run_tool_choice_guard(data)
            entry = append.call_args[0][0]
            self.assertEqual(entry["skipped"], "sampled_shadow")
            self.assertIn("root_has_code_graph", entry)
            self.assertIs(entry["root_has_code_graph"], False)

    def test_absent_on_the_no_deny_possible_skip_row(self):
        """No Jev call, no evaluate_search call -- never fill the field by
        statting the filesystem after the fact."""
        data = {"session_id": "s1", "cwd": "/tmp", "tool_name": "Bash", "tool_input": {"command": "grep -rn foo ."}}
        with mock.patch("airlock.keyfile.get_api_key", return_value="key"), \
             mock.patch("airlock.scope.root_has_graphify_graph", return_value=False) as stat_call, \
             mock.patch("random.random", return_value=0.99), \
             mock.patch("airlock.client.ask") as ask_call, \
             mock.patch("airlock.log.append") as append:
            guards.run_tool_choice_guard(data)
            entry = append.call_args[0][0]
            self.assertEqual(entry["skipped"], "no_deny_possible")
            self.assertNotIn("root_has_code_graph", entry)
            ask_call.assert_not_called()
            # root_has_graphify_graph IS still called once, up front, to
            # decide deny_possible_bash itself -- that is the one legitimate
            # use, not a fill-in-the-field stat. Assert it is not called
            # again after the skip decision.
            stat_call.assert_called_once()

    def test_absent_on_a_disk_wide_filename_search_row(self):
        """The other deny branch (filename_search on disk_wide scope) never
        touches root_has_graphify_graph at all."""
        data = {"session_id": "s1", "cwd": "/tmp", "tool_name": "Bash", "tool_input": {"command": "find / -name '*.xlsm'"}}
        fake = (
            {
                "model": "jev-1.13.0",
                "answers": {
                    "search_intent": {
                        "type": "choice",
                        "choice": "filename_search",
                        "confidence": 0.95,
                        "probabilities": {"filename_search": 0.95, "not_a_search": 0.05},
                    }
                },
                "usage": {"input_tokens": 100, "output_tokens": 10},
            },
            50,
        )
        with mock.patch("airlock.keyfile.get_api_key", return_value="key"), \
             mock.patch("airlock.client.ask", return_value=fake), \
             mock.patch("airlock.log.append") as append:
            guards.run_tool_choice_guard(data)
            entry = append.call_args[0][0]
            self.assertTrue(entry["would_deny"])
            # A disk_wide `find` is not grep-family, so deny_possible_bash's
            # graph check never runs, but the branch IS evaluated (a Jev call
            # happened) -- the field is present, recording that the graph
            # fact was false/irrelevant for this row's actual deny reason.
            self.assertIn("root_has_code_graph", entry)

    def test_absent_on_client_ask_exception(self):
        """The call errored before evaluate_search ever ran."""
        data = {"session_id": "s1", "cwd": "/tmp", "tool_name": "Bash", "tool_input": {"command": "grep -rn foo ."}}
        with mock.patch("airlock.keyfile.get_api_key", return_value="key"), \
             mock.patch("airlock.scope.root_has_graphify_graph", return_value=True), \
             mock.patch("random.random", return_value=0.99), \
             mock.patch("airlock.client.ask", side_effect=Exception("boom")), \
             mock.patch("airlock.log.append") as append:
            guards.run_tool_choice_guard(data)
            entry = append.call_args[0][0]
            self.assertIn("error", entry)
            self.assertNotIn("root_has_code_graph", entry)


if __name__ == "__main__":
    unittest.main()
