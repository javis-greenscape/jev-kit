
import tests  # noqa: F401 -- MUST be the first import. `python3 -m unittest
# discover -s tests` runs with start_dir == top_level_dir, so unittest treats
# `tests/` as a flat directory of top-level modules and never executes
# tests/__init__.py as a package init (name == '.' in TestLoader._find_tests).
# Importing it explicitly, here, first, is what actually runs its HOME/
# AIRLOCK_*-isolating fixture before any airlock.* module resolves a real path.

import unittest
from unittest import mock

from airlock import guards


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
            self.assertEqual(entry["suggestion"], "plocate -d ~/.cache/plocate/home.db -i '<pattern>'")


if __name__ == "__main__":
    unittest.main()
