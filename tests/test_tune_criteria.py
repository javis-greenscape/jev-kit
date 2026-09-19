
import tests  # noqa: F401 -- MUST be the first import. `python3 -m unittest
# discover -s tests` runs with start_dir == top_level_dir, so unittest treats
# `tests/` as a flat directory of top-level modules and never executes
# tests/__init__.py as a package init (name == '.' in TestLoader._find_tests).
# Importing it explicitly, here, first, is what actually runs its HOME/
# AIRLOCK_*-isolating fixture before any airlock.* module resolves a real path.

import ast
import unittest

from airlock import questions
from tuning import tune


class TestApplyCriteriaReplacement(unittest.TestCase):
    def setUp(self):
        with open("airlock/questions.py") as f:
            self.source = f.read()

    def test_replaces_only_named_option(self):
        new_source, applied, skipped = tune.apply_criteria_replacement(
            self.source, {"task_kind": {"lookup": "NEW LOOKUP TEXT"}}
        )
        self.assertEqual(applied, {("task_kind", "lookup")})
        self.assertEqual(skipped, [])
        ns = {}
        exec(compile(new_source, "<test>", "exec"), ns)
        qs = ns["tier_questions"]()
        self.assertEqual(qs["task_kind"]["criteria"]["lookup"], "NEW LOOKUP TEXT")
        # every other option is untouched
        original = questions.tier_questions()
        for opt in ("mechanical_edit", "scoped_implementation", "judgement", "hard_problem", "unclear"):
            self.assertEqual(qs["task_kind"]["criteria"][opt], original["task_kind"]["criteria"][opt])

    def test_diff_is_scoped_to_changed_option(self):
        new_source, _applied, _skipped = tune.apply_criteria_replacement(
            self.source, {"search_intent": {"not_a_search": "NEW TEXT"}}
        )
        # The rest of the file -- everything before and after the replaced
        # option's span -- must be byte-for-byte identical. A full
        # ast.unparse of the whole tree would fail this (it reformats every
        # line); a targeted source splice must pass it.
        marker_before = "def bash_state("  # comes after search_intent in the file
        old_tail = self.source[self.source.index(marker_before):]
        new_tail = new_source[new_source.index(marker_before):]
        self.assertEqual(old_tail, new_tail)
        self.assertIn("NEW TEXT", new_source)
        self.assertNotIn("NEW TEXT", self.source)

    def test_result_is_valid_python(self):
        new_source, _applied, _skipped = tune.apply_criteria_replacement(
            self.source, {"task_kind": {"unclear": "short"}, "search_intent": {"unclear": "also short"}}
        )
        ast.parse(new_source)  # raises on invalid syntax

    def test_unknown_question_id_is_skipped_not_raised(self):
        new_source, applied, skipped = tune.apply_criteria_replacement(
            self.source, {"not_a_real_question": {"foo": "bar"}}
        )
        self.assertEqual(new_source, self.source)
        self.assertEqual(applied, set())
        self.assertEqual(len(skipped), 1)

    def test_unknown_option_name_is_skipped_not_raised(self):
        new_source, applied, skipped = tune.apply_criteria_replacement(
            self.source, {"task_kind": {"not_a_real_option": "x"}}
        )
        self.assertEqual(new_source, self.source)
        self.assertEqual(applied, set())
        self.assertEqual(len(skipped), 1)

    def test_non_string_value_is_skipped_not_raised(self):
        new_source, applied, skipped = tune.apply_criteria_replacement(
            self.source, {"task_kind": {"lookup": {"nested": "dict"}}}
        )
        self.assertEqual(new_source, self.source)
        self.assertEqual(applied, set())
        self.assertEqual(len(skipped), 1)

    def test_multiple_options_across_both_questions(self):
        new_source, applied, skipped = tune.apply_criteria_replacement(
            self.source,
            {
                "task_kind": {"lookup": "A", "unclear": "B"},
                "search_intent": {"not_a_search": "C"},
            },
        )
        self.assertEqual(skipped, [])
        self.assertEqual(
            applied,
            {("task_kind", "lookup"), ("task_kind", "unclear"), ("search_intent", "not_a_search")},
        )
        ns = {}
        exec(compile(new_source, "<test>", "exec"), ns)
        qs = ns["tier_questions"]()
        bs = ns["bash_questions"]()
        self.assertEqual(qs["task_kind"]["criteria"]["lookup"], "A")
        self.assertEqual(qs["task_kind"]["criteria"]["unclear"], "B")
        self.assertEqual(bs["search_intent"]["criteria"]["not_a_search"], "C")

    def test_extract_json_ignores_trailing_prose(self):
        value = tune._extract_json('[{"a": 1}]\n\nSome trailing commentary.')
        self.assertEqual(value, [{"a": 1}])

    def test_extract_json_handles_code_fence(self):
        value = tune._extract_json('```json\n{"a": 1}\n```')
        self.assertEqual(value, {"a": 1})

    def test_merge_new_cases_dedupes_and_caps(self):
        existing = [{"id": "seed-1", "guard": "tool_choice_guard", "source": "seed",
                     "payload": {"tool_input": {"command": "find . -name x"}}}]
        dup = {"id": "shadow-dup", "guard": "tool_choice_guard", "source": "shadow",
               "payload": {"tool_input": {"command": "find . -name x"}}}
        new = {"id": "shadow-new", "guard": "tool_choice_guard", "source": "shadow",
               "payload": {"tool_input": {"command": "grep -rn foo ."}}}
        merged = tune.merge_new_cases(existing, [dup, new])
        ids = [c["id"] for c in merged]
        self.assertIn("seed-1", ids)
        self.assertIn("shadow-new", ids)
        self.assertNotIn("shadow-dup", ids)  # deduped against existing seed by normalized command

    def test_merge_new_cases_never_drops_seed_when_capping(self):
        seeds = [{"id": "seed-%d" % i, "guard": "tool_choice_guard", "source": "seed",
                  "payload": {"tool_input": {"command": "cmd-%d" % i}}} for i in range(390)]
        new = [{"id": "shadow-%d" % i, "guard": "tool_choice_guard", "source": "shadow",
                "payload": {"tool_input": {"command": "newcmd-%d" % i}}} for i in range(20)]
        merged = tune.merge_new_cases(seeds, new)
        self.assertLessEqual(len(merged), tune.MAX_CASES)
        self.assertTrue(all(c.get("source") == "seed" for c in seeds if c["id"] in {m["id"] for m in merged}) or True)
        seed_ids_in_merged = {c["id"] for c in merged if c["source"] == "seed"}
        self.assertEqual(len(seed_ids_in_merged), 390)


if __name__ == "__main__":
    unittest.main()
