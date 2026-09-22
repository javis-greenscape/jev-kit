"""The tier eval scores the THREE LIVE OUTCOMES, not one flag.

`policy.evaluate_tier`'s `would_deny` is the union of block and warn, so scoring
it against one label cannot tell them apart -- and the labels in
eval/cases.jsonl had been written to the two-rung block rule while the eval
scored the wider flag. These tests pin the corrected comparison down, with Jev
mocked throughout, and assert that what the eval predicts is what the hook would
actually do.
"""

import tests  # noqa: F401, I001 -- MUST be the first import. `python3 -m unittest
# discover -s tests` runs with start_dir == top_level_dir, so unittest treats
# `tests/` as a flat directory of top-level modules and never executes
# tests/__init__.py as a package init (name == '.' in TestLoader._find_tests).
# Importing it explicitly, here, first, is what actually runs its HOME/
# AIRLOCK_*-isolating fixture before any airlock.* module resolves a real path.

import json
import unittest
from unittest import mock

from airlock import eval as eval_mod, guards, policy


def _entry(chosen_type, task_kind, confidence=1.0, margin=1.0, prior_failed=0.0):
    verdict = policy.evaluate_tier(
        task_kind=task_kind,
        task_kind_confidence=confidence,
        task_kind_margin=margin,
        states_prior_failed_attempts=prior_failed,
        chosen_type=chosen_type,
    )
    return policy.tier_entry_fields(verdict, task_kind, confidence, margin,
                                    prior_failed, chosen_type)


class TestTierOutcome(unittest.TestCase):
    def outcome(self, chosen_type, task_kind, **kw):
        return eval_mod.tier_outcome(chosen_type, _entry(chosen_type, task_kind, **kw))

    def test_two_rungs_over_blocks(self):
        self.assertEqual(self.outcome("workerS", "lookup"), "block")

    def test_exactly_one_rung_over_warns(self):
        self.assertEqual(self.outcome("scout", "lookup"), "warn")
        self.assertEqual(self.outcome("workerS", "mechanical_edit"), "warn")
        self.assertEqual(self.outcome("workerO", "scoped_implementation"), "warn")
        self.assertEqual(self.outcome("claude", "judgement"), "warn")

    def test_adequate_is_silent(self):
        self.assertEqual(self.outcome("scout", "mechanical_edit"), "silent")
        self.assertEqual(self.outcome("workerO", "judgement"), "silent")

    def test_under_tiered_is_silent_never_a_deny(self):
        self.assertEqual(self.outcome("scout", "judgement"), "silent")
        self.assertEqual(self.outcome("workerS", "hard_problem"), "silent")

    def test_unclear_is_silent(self):
        self.assertEqual(self.outcome("claude", "unclear"), "silent")

    def test_fable_without_a_stated_prior_failure_blocks(self):
        self.assertEqual(self.outcome("fable", "hard_problem", prior_failed=0.0), "block")
        # even when the task itself could not be classified
        self.assertEqual(self.outcome("fable", "unclear", prior_failed=0.0), "block")

    def test_fable_with_a_stated_prior_failure_is_silent(self):
        self.assertEqual(self.outcome("fable", "hard_problem", prior_failed=1.0), "silent")

    def test_the_cheapest_rung_is_silent_and_never_judged(self):
        """policy.deny_possible_agent means the hook does not even ask."""
        self.assertFalse(policy.deny_possible_agent("scout-find"))
        self.assertEqual(self.outcome("scout-find", "judgement"), "silent")

    def test_below_the_deny_bar_is_silent(self):
        self.assertEqual(self.outcome("scout", "lookup", confidence=0.7, margin=0.1),
                         "silent")

    def test_rewrite_mode_is_forced_off(self):
        """Rewrite is opt-in, so the eval must not score against it even on a
        machine that has turned it on."""
        with mock.patch.object(policy, "rewrite_enabled", return_value=True):
            self.assertEqual(self.outcome("workerO", "scoped_implementation"), "warn")

    def test_it_agrees_with_the_hook_entry_builder(self):
        """The eval and guards.compute_tier_entry must build the same entry, or
        the eval scores something the hook does not do."""
        answers = {
            "task_kind": {"choice": "lookup", "confidence": 1.0,
                          # compute_margin needs a runner-up to compare against.
                          "probabilities": {"lookup": 1.0, "mechanical_edit": 0.0}},
            "states_prior_failed_attempts": {"noul": 0.0},
        }
        data = {"session_id": "s", "cwd": "/tmp", "tool_name": "Agent",
                "tool_input": {"subagent_type": "workerS", "description": "d",
                               "prompt": "List every call site of X."}}
        with mock.patch.object(guards.keyfile, "get_api_key", return_value="k"), \
                mock.patch.object(guards.client, "ask",
                                  return_value=({"answers": answers}, 10)), \
                mock.patch.object(guards.policy, "sample_rate", return_value=0.0):
            entry = guards.compute_tier_entry(data)
        self.assertEqual(policy.tier_surface(entry, rewrite_on=False), "block")
        self.assertEqual(eval_mod.tier_outcome("workerS", entry), "block")


class TestExpectedTierOutcome(unittest.TestCase):
    def test_reads_the_one_true_flag(self):
        self.assertEqual(eval_mod.expected_tier_outcome({"expect_block": True}), "block")
        self.assertEqual(eval_mod.expected_tier_outcome({"expect_warn": True}), "warn")
        self.assertEqual(eval_mod.expected_tier_outcome({"expect_silent": True}), "silent")

    def test_none_when_no_flag_is_set(self):
        self.assertIsNone(eval_mod.expected_tier_outcome({"task_kind": "lookup"}))


class TestRunCaseScoring(unittest.TestCase):
    """run_case must compare the expected outcome with the predicted one, and
    must report a deny as the BLOCK outcome rather than the wider flag."""

    def _case(self, chosen_type, prompt, expected):
        return {"id": "t", "guard": "tier_guard",
                "payload": {"tool_name": "Agent", "cwd": "/tmp",
                            "tool_input": {"subagent_type": chosen_type,
                                           "description": "d", "prompt": prompt}},
                "expected": expected}

    def _run(self, case, choice, confidence=1.0, prior_failed=0.0):
        answers = {
            "task_kind": {"choice": choice, "confidence": confidence,
                          # a runner-up, so compute_margin has two options
                          "probabilities": {choice: confidence, "__other": 0.0}},
            "states_prior_failed_attempts": {"noul": prior_failed},
        }
        with mock.patch.object(eval_mod.client, "ask",
                               return_value=({"answers": answers, "usage": {}}, 5)):
            return eval_mod.run_case(case)

    def test_a_one_rung_warn_is_not_a_false_deny(self):
        case = self._case("scout", "Read a file and tell me one value.",
                          {"task_kind": "lookup", "expect_block": False,
                           "expect_warn": True, "expect_silent": False})
        r = self._run(case, "lookup")
        self.assertEqual(r["predicted_outcome"], "warn")
        self.assertTrue(r["outcome_correct"])
        self.assertFalse(r["predicted_would_deny"])
        self.assertFalse(r["expected_would_deny"])
        self.assertTrue(r["deny_correct"])

    def test_a_two_rung_block_is_scored_as_a_deny(self):
        case = self._case("workerS", "List every call site of X.",
                          {"task_kind": "lookup", "expect_block": True,
                           "expect_warn": False, "expect_silent": False})
        r = self._run(case, "lookup")
        self.assertEqual(r["predicted_outcome"], "block")
        self.assertTrue(r["predicted_would_deny"])
        self.assertTrue(r["deny_correct"])

    def test_an_ambiguous_case_is_not_scored(self):
        case = self._case("workerS", "Find the race.",
                          {"ambiguous": True, "expect_block": False,
                           "expect_warn": False, "expect_silent": True})
        r = self._run(case, "judgement")
        self.assertTrue(r["ambiguous"])
        self.assertIsNone(r["label_correct"])
        summary = eval_mod.summarize([r])["tier_guard"]
        self.assertEqual(summary["ambiguous"], 1)
        self.assertEqual(summary.get("outcome_scored"), None)

    def test_summary_reports_each_outcome_separately(self):
        results = [
            {"id": "a", "guard": "tier_guard", "expected_label": "lookup",
             "predicted_label": "lookup", "label_correct": True,
             "expected_would_deny": True, "predicted_would_deny": True,
             "deny_correct": True, "expected_outcome": "block",
             "predicted_outcome": "block", "outcome_correct": True,
             "ambiguous": False, "latency_ms": 1, "tokens": 0},
            {"id": "b", "guard": "tier_guard", "expected_label": "mechanical_edit",
             "predicted_label": "scoped_implementation", "label_correct": False,
             "expected_would_deny": False, "predicted_would_deny": False,
             "deny_correct": True, "expected_outcome": "warn",
             "predicted_outcome": "silent", "outcome_correct": False,
             "ambiguous": False, "latency_ms": 1, "tokens": 0},
        ]
        s = eval_mod.summarize(results)["tier_guard"]
        self.assertEqual(s["per_outcome"]["block"]["accuracy"], 1.0)
        self.assertEqual(s["per_outcome"]["warn"]["accuracy"], 0.0)
        self.assertEqual(s["outcome_accuracy"], 0.5)
        self.assertEqual([m["id"] for m in s["outcome_misses"]], ["b"])
        self.assertEqual(s["false_deny"], 0)
        self.assertEqual(s["missed_deny"], 0)


class TestCasesFileLabels(unittest.TestCase):
    """The shipped corpus must carry exactly one outcome flag per tier case, and
    every label must be the one the ladder gives for that case's own chosen tier
    and task -- not what Jev happens to answer."""

    def setUp(self):
        with open(eval_mod.CASES_FILE) as f:
            self.cases = [json.loads(line) for line in f if line.strip()]
        self.tier = [c for c in self.cases if c.get("guard") == "tier_guard"]

    def test_there_are_tier_cases(self):
        self.assertGreater(len(self.tier), 50)

    def test_exactly_one_outcome_flag_each(self):
        for c in self.tier:
            flags = [c["expected"].get("expect_%s" % n) for n in eval_mod.TIER_OUTCOMES]
            self.assertEqual(sum(1 for f in flags if f), 1, c["id"])

    def test_no_case_still_carries_the_old_flag(self):
        for c in self.tier:
            self.assertNotIn("would_deny", c["expected"], c["id"])

    def test_an_ambiguous_case_carries_no_task_kind(self):
        for c in self.tier:
            if c["expected"].get("ambiguous"):
                self.assertNotIn("task_kind", c["expected"], c["id"])

    def test_every_label_follows_the_ladder(self):
        """Re-derive each label from the case's own chosen tier and task_kind and
        assert the file agrees. This is what stops a future edit quietly
        relabelling a case to whatever the model answered."""
        prior_failure_ids = set()
        for c in self.tier:
            chosen = c["payload"]["tool_input"].get("subagent_type") or ""
            expected = c["expected"]
            outcome = eval_mod.expected_tier_outcome(expected)
            if not policy.deny_possible_agent(chosen):
                self.assertEqual(outcome, "silent", c["id"])
                continue
            if chosen == "fable":
                # A fable dispatch is a block unless the prompt states a prior
                # FAILED attempt, in which case it is judged on the rung gap.
                if outcome == "block":
                    continue
                prior_failure_ids.add(c["id"])
            task_kind = expected.get("task_kind")
            if task_kind is None:
                self.assertTrue(expected.get("ambiguous"), c["id"])
                continue
            adequate = policy.TASK_KIND_ADEQUATE_RUNG.get(task_kind)
            if adequate is None:
                self.assertEqual(outcome, "silent", c["id"])
                continue
            index = eval_mod.policy.tiers.rung_index()
            diff = index[policy.rung_for_agent_type(chosen)] - index[adequate]
            want = "block" if diff >= 2 else ("warn" if diff == 1 else "silent")
            self.assertEqual(outcome, want, "%s (rung gap %d)" % (c["id"], diff))
        self.assertTrue(prior_failure_ids, "no fable-with-stated-failure case left")


if __name__ == "__main__":
    unittest.main()
