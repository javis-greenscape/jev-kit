
import tests  # noqa: F401 -- MUST be the first import. `python3 -m unittest
# discover -s tests` runs with start_dir == top_level_dir, so unittest treats
# `tests/` as a flat directory of top-level modules and never executes
# tests/__init__.py as a package init (name == '.' in TestLoader._find_tests).
# Importing it explicitly, here, first, is what actually runs its HOME/
# AIRLOCK_*-isolating fixture before any airlock.* module resolves a real path.

import unittest

from airlock import policy


class TestEnforceDenySearch(unittest.TestCase):
    def test_mirrors_would_deny_true(self):
        self.assertTrue(policy.enforce_deny_search({"would_deny": True}))

    def test_mirrors_would_deny_false(self):
        self.assertFalse(policy.enforce_deny_search({"would_deny": False}))

    def test_missing_entry_fails_closed_to_allow(self):
        self.assertFalse(policy.enforce_deny_search(None))
        self.assertFalse(policy.enforce_deny_search({}))


class TestEnforceDenyTier(unittest.TestCase):
    def _entry(self, **over):
        base = {
            "chosen_type": "fable",
            "prior_failed": 0.9,
            "rung_diff": 0,
            "task_kind": "judgement",
            "task_kind_confidence": 0.95,
            "margin": 0.6,
        }
        base.update(over)
        return base

    def test_fable_without_prior_failure_denies(self):
        entry = self._entry(prior_failed=0.1)
        self.assertTrue(policy.enforce_deny_tier(entry))

    def test_fable_with_stated_prior_failure_allows(self):
        entry = self._entry(prior_failed=0.9, rung_diff=0)
        self.assertFalse(policy.enforce_deny_tier(entry))

    def test_one_rung_gap_never_denies(self):
        entry = self._entry(chosen_type="workerO", prior_failed=1.0, rung_diff=1)
        self.assertFalse(policy.enforce_deny_tier(entry))

    def test_two_rung_gap_denies_when_bar_met(self):
        entry = self._entry(chosen_type="director", prior_failed=1.0, rung_diff=2)
        self.assertTrue(policy.enforce_deny_tier(entry))

    def test_two_rung_gap_but_low_confidence_allows(self):
        entry = self._entry(chosen_type="director", prior_failed=1.0, rung_diff=2, task_kind_confidence=0.5)
        self.assertFalse(policy.enforce_deny_tier(entry))

    def test_two_rung_gap_but_low_margin_allows(self):
        entry = self._entry(chosen_type="director", prior_failed=1.0, rung_diff=2, margin=0.1)
        self.assertFalse(policy.enforce_deny_tier(entry))

    def test_two_rung_gap_but_unclear_task_kind_allows(self):
        entry = self._entry(chosen_type="director", prior_failed=1.0, rung_diff=2, task_kind="unclear")
        self.assertFalse(policy.enforce_deny_tier(entry))

    def test_under_tiering_never_denies(self):
        entry = self._entry(chosen_type="scout-find", prior_failed=1.0, rung_diff=-2)
        self.assertFalse(policy.enforce_deny_tier(entry))


class TestEvaluateTierRungDiff(unittest.TestCase):
    def test_rung_diff_present_for_known_task_kind(self):
        verdict = policy.evaluate_tier(
            task_kind="lookup",
            task_kind_confidence=0.9,
            task_kind_margin=0.5,
            states_prior_failed_attempts=0.0,
            chosen_type="fable",
        )
        # lookup -> scout-find (index 0); fable is the last rung.
        self.assertEqual(verdict["rung_diff"], len(policy.RUNG_ORDER) - 1)

    def test_rung_diff_none_for_unmapped_task_kind(self):
        verdict = policy.evaluate_tier(
            task_kind="unclear",
            task_kind_confidence=0.9,
            task_kind_margin=0.5,
            states_prior_failed_attempts=0.0,
            chosen_type="fable",
        )
        self.assertIsNone(verdict["rung_diff"])


if __name__ == "__main__":
    unittest.main()
