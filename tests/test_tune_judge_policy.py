"""What the tuning judge is told, which rows it is shown, and what survives.

Every test here traces back to one live run: 2026-09-19T17:13:03Z judged 20
shadow rows, called 17 of them wrong (error_rate 0.85), and proposed a
criteria change the gate then rejected. Reconstructing that batch from the
shadow log showed 17 of the 20 carried no Jev answer at all, and that the
prompt described a policy the guard no longer had. These tests hold both
halves of that fix down.
"""
import tests  # noqa: F401, I001 -- MUST be the first import; see tests/__init__.py.

import os
import stat
import tempfile
import unittest
from pathlib import Path

from airlock import policy, tiers
from tuning import policy_text, sampling, tune, verdicts


# --- the policy text is generated, and must stay generated ------------------


class TestPolicyTextIsGeneratedFromTheCode(unittest.TestCase):
    def setUp(self):
        self.text = policy_text.policy_text()

    def test_every_live_threshold_appears_in_the_text(self):
        self.assertIn(policy_text._fmt(policy.CONFIDENCE_THRESHOLD), self.text)
        self.assertIn(policy_text._fmt(policy.MARGIN_THRESHOLD), self.text)

    def test_every_rung_on_the_live_ladder_appears(self):
        for rung in tiers.rung_names():
            self.assertIn(rung, self.text, "rung %r missing from the judge's policy" % rung)

    def test_every_task_kind_and_its_adequate_rung_appear_together(self):
        for kind, rung in policy.TASK_KIND_ADEQUATE_RUNG.items():
            self.assertIn("%s -> %s" % (kind, rung), self.text)

    def test_every_search_intent_option_and_scope_appears(self):
        for option in policy_text.search_intent_options():
            self.assertIn(option, self.text)
        for scope in policy_text.scope_names():
            self.assertIn(scope, self.text)

    def test_the_three_tier_outcomes_are_named(self):
        for outcome in ("block", "warn", "allow (silent)"):
            self.assertIn(outcome, self.text)

    def test_under_tiering_is_stated_never_to_be_a_deny(self):
        self.assertIn("UNDER-TIERING IS NEVER A DENY", self.text)

    def test_below_bar_silence_is_stated_to_be_correct(self):
        self.assertIn("CORRECT", self.text)
        self.assertIn("SILENT", self.text)

    def test_the_block_and_warn_gaps_are_read_from_the_policy_not_typed_in(self):
        # Derived by running enforce_deny_tier, not by copying its `>= 2`.
        self.assertEqual(policy_text.min_blocking_rung_gap(), 2)
        self.assertEqual(policy_text.min_warning_rung_gap(), 1)
        self.assertIn("a gap of 2 rungs or more", self.text)
        self.assertIn("a gap of exactly 1 rung", self.text)

    def test_every_deny_combination_the_code_produces_is_listed(self):
        for combo in policy_text.search_deny_combinations():
            self.assertIn(
                "scope=%s search_intent=%s graphify-graph-present=%s"
                % (combo["scope"], combo["search_intent"], combo["graph_present"]),
                self.text,
            )

    def test_the_fable_rule_is_stated(self):
        self.assertIn("fable", self.text)
        self.assertIn("prior failed attempt", self.text)

    def test_the_action_field_is_explained_as_the_rules_config_not_the_outcome(self):
        # 18 of the 20 rows in the bad run read action="deny" while
        # would_deny was false: they were ALLOWED.
        self.assertIn("configured action", self.text)


class TestPolicyFingerprintCatchesDrift(unittest.TestCase):
    """The generated text cannot drift on its own; the hand-written prose
    around it can, the moment somebody changes a constant. This pin fails
    then, and re-pinning it forces a re-read of the prompt."""

    def test_pinned_fingerprint_matches_the_live_constants(self):
        self.assertEqual(
            policy_text.policy_fingerprint(),
            policy_text.POLICY_FINGERPRINT,
            "airlock policy constants changed. Re-read tuning/policy_text.py's "
            "prose (and tune.py's JUDGE_TASK) against the new policy, then "
            "re-pin POLICY_FINGERPRINT with:\n"
            "  python3 -c 'from tuning import policy_text as p; print(p.policy_fingerprint())'",
        )

    def test_the_fingerprint_moves_when_a_threshold_moves(self):
        before = policy_text.policy_fingerprint()
        original = policy.CONFIDENCE_THRESHOLD
        try:
            policy.CONFIDENCE_THRESHOLD = 0.95
            self.assertNotEqual(policy_text.policy_fingerprint(), before)
        finally:
            policy.CONFIDENCE_THRESHOLD = original
        self.assertEqual(policy_text.policy_fingerprint(), before)


# --- the prompt -------------------------------------------------------------


def _answered_row(**extra):
    row = {
        "ts": "2026-01-01T00:00:00Z",
        "guard": "tool_choice_guard",
        "tool_name": "Bash",
        "scope": "disk_wide",
        "margin": 1.0,
        "input_summary": {"command": "find / -name foo"},
        "answers": {"search_intent": {"choice": "filename_search", "confidence": 1.0}},
    }
    row.update(extra)
    return row


class TestJudgePrompt(unittest.TestCase):
    def setUp(self):
        self.prompt = tune.build_judge_prompt([_answered_row()])

    def test_the_prompt_carries_the_generated_policy(self):
        self.assertIn("THE GUARD'S REAL POLICY", self.prompt)
        self.assertIn(policy_text.tier_policy_text().strip(), self.prompt)
        self.assertIn(policy_text.search_policy_text().strip(), self.prompt)

    def test_the_prompt_asks_for_label_and_action_separately(self):
        self.assertIn('"label_correct"', self.prompt)
        self.assertIn('"action_correct"', self.prompt)
        self.assertIn("THE LABEL", self.prompt)
        self.assertIn("THE ACTION TAKEN", self.prompt)

    def test_the_prompt_allows_cannot_tell(self):
        self.assertIn('"cannot_tell"', self.prompt)
        self.assertIn("cannot tell", self.prompt)

    def test_the_old_single_flag_schema_is_gone(self):
        self.assertNotIn("jev_correct", self.prompt)

    def test_the_old_would_deny_only_sentence_is_gone(self):
        self.assertNotIn("out of scope for this judgement", self.prompt)
        self.assertNotIn("only affects the would_deny policy downstream", self.prompt)

    def test_internal_bookkeeping_never_reaches_the_judge(self):
        row = _answered_row()
        row["_sample_rule"] = sampling.SAMPLE_RULE_RANDOM
        self.assertNotIn("_sample_rule", tune.build_judge_prompt([row]))

    def test_every_row_gets_an_id_so_verdicts_can_be_matched_back(self):
        prompt = tune.build_judge_prompt([_answered_row(), _answered_row()])
        self.assertIn('"id": "row-0"', prompt)
        self.assertIn('"id": "row-1"', prompt)


# --- sampling ---------------------------------------------------------------


class TestSampling(unittest.TestCase):
    def test_a_row_with_no_answers_is_not_judgeable(self):
        row = {"guard": "tool_choice_guard", "skipped": "no_deny_possible",
               "input_summary": {"command": "grep -rn foo ."}}
        self.assertFalse(sampling.judgeable(row))
        self.assertEqual(sampling.skip_reason(row), sampling.SKIP_NO_JEV_ANSWER)

    def test_a_retired_question_id_is_skipped_as_legacy_not_judged(self):
        # `search_kind` predates `search_intent`; 34 such rows are in the
        # live log and none of them can be scored against today's rubric.
        row = {"guard": "tool_choice_guard",
               "input_summary": {"command": "grep -rn foo ."},
               "answers": {"search_kind": {"choice": "filename_search"}}}
        self.assertEqual(sampling.skip_reason(row), sampling.SKIP_LEGACY_QUESTION)

    def test_a_question_with_no_rubric_is_skipped_not_judged(self):
        row = {"guard": "rules", "input_summary": {"command": "rm -rf /tmp/x"},
               "answers": {"risk": {"score": 1.0}}}
        self.assertEqual(sampling.skip_reason(row), sampling.SKIP_NO_RUBRIC)

    def test_a_row_with_an_answer_but_no_input_summary_is_skipped(self):
        row = {"guard": "tool_choice_guard", "input_summary": {},
               "answers": {"search_intent": {"choice": "filename_search"}}}
        self.assertEqual(sampling.skip_reason(row), sampling.SKIP_MISSING_INPUT)

    def test_the_live_failure_shape_samples_only_the_answerable_rows(self):
        """The 2026-09-19T17:13Z batch: 17 rows with no Jev answer, 3 with
        one. The old code sent all 20 and got 17 'wrong' back."""
        rows = [{"ts": "2026-01-01T00:%02d:00Z" % i, "guard": "tool_choice_guard",
                 "skipped": "no_deny_possible", "action": "deny", "would_deny": False,
                 "input_summary": {"command": "grep -rn foo ."}} for i in range(17)]
        rows += [_answered_row(ts="2026-01-01T01:%02d:00Z" % i) for i in range(3)]
        selected, report = sampling.select(rows, 20, seed=1)
        self.assertEqual(report["considered"], 20)
        self.assertEqual(report["judgeable"], 3)
        self.assertEqual(report["skipped_by_reason"][sampling.SKIP_NO_JEV_ANSWER], 17)
        self.assertEqual(len(selected), 3)
        for row in selected:
            self.assertIn("answers", row)

    def test_the_budget_is_split_between_recent_and_uniformly_random(self):
        rows = [_answered_row(ts="2026-01-01T00:%02d:00Z" % i) for i in range(40)]
        selected, report = sampling.select(rows, 20, seed=7)
        self.assertEqual(len(selected), 20)
        self.assertEqual(report["sampled_recent"], 10)
        self.assertEqual(report["sampled_random"], 10)
        rules = {r["_sample_rule"] for r in selected}
        self.assertEqual(rules, {sampling.SAMPLE_RULE_RECENT, sampling.SAMPLE_RULE_RANDOM})

    def test_the_recent_half_really_is_the_newest_rows(self):
        rows = [_answered_row(ts="2026-01-01T00:%02d:00Z" % i) for i in range(40)]
        selected, _ = sampling.select(rows, 20, seed=7)
        recent = [r["ts"] for r in selected if r["_sample_rule"] == sampling.SAMPLE_RULE_RECENT]
        self.assertEqual(sorted(recent, reverse=True),
                         ["2026-01-01T00:%02d:00Z" % i for i in range(39, 29, -1)])

    def test_the_random_half_is_drawn_from_the_rest_not_the_recent_half(self):
        rows = [_answered_row(ts="2026-01-01T00:%02d:00Z" % i) for i in range(40)]
        selected, _ = sampling.select(rows, 20, seed=7)
        recent = {r["ts"] for r in selected if r["_sample_rule"] == sampling.SAMPLE_RULE_RECENT}
        drawn = {r["ts"] for r in selected if r["_sample_rule"] == sampling.SAMPLE_RULE_RANDOM}
        self.assertFalse(recent & drawn)

    def test_the_same_seed_gives_the_same_sample(self):
        rows = [_answered_row(ts="2026-01-01T00:%02d:00Z" % i) for i in range(40)]
        a, _ = sampling.select(rows, 20, seed=99)
        b, _ = sampling.select(rows, 20, seed=99)
        self.assertEqual([r["ts"] for r in a], [r["ts"] for r in b])


class TestRateReporting(unittest.TestCase):
    def test_each_sample_rule_gets_its_own_rate(self):
        table = sampling.rates([
            {"sample_rule": sampling.SAMPLE_RULE_RECENT, "wrong": True},
            {"sample_rule": sampling.SAMPLE_RULE_RECENT, "wrong": False},
            {"sample_rule": sampling.SAMPLE_RULE_RANDOM, "wrong": False},
            {"sample_rule": sampling.SAMPLE_RULE_RANDOM, "wrong": False},
        ])
        self.assertEqual(table[sampling.SAMPLE_RULE_RECENT]["error_rate"], 0.5)
        self.assertEqual(table[sampling.SAMPLE_RULE_RANDOM]["error_rate"], 0.0)
        self.assertEqual(table["_all_sampled"]["error_rate"], 0.25)

    def test_cannot_tell_is_in_neither_half_of_the_ratio(self):
        table = sampling.rates([
            {"sample_rule": sampling.SAMPLE_RULE_RANDOM, "cannot_tell": True},
            {"sample_rule": sampling.SAMPLE_RULE_RANDOM, "wrong": True},
        ])
        bucket = table[sampling.SAMPLE_RULE_RANDOM]
        self.assertEqual(bucket["judged"], 1)
        self.assertEqual(bucket["cannot_tell"], 1)
        self.assertEqual(bucket["error_rate"], 1.0)

    def test_the_human_sentence_never_states_a_bare_rate(self):
        _, report = sampling.select(
            [_answered_row(ts="2026-01-01T00:%02d:00Z" % i) for i in range(4)], 4, seed=3)
        table = sampling.rates([
            {"sample_rule": sampling.SAMPLE_RULE_RANDOM, "wrong": True},
            {"sample_rule": sampling.SAMPLE_RULE_RANDOM, "wrong": False},
        ])
        sentence = sampling.rate_sentence(report, table)
        self.assertIn("of the 2 rows sampled by", sentence)
        self.assertIn("uniformly random", sentence)
        self.assertIn("judgeable", sentence)


# --- verdicts ---------------------------------------------------------------


class TestVerdictPersistence(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.state = Path(self._td.name)
        self.addCleanup(self._td.cleanup)

    def _one(self, **extra):
        row = {"id": "row-0", "guard": "tool_choice_guard", "wrong": True,
               "reason": "x", "cannot_tell": False}
        row.update(extra)
        return row

    def test_a_run_writes_its_per_row_verdicts(self):
        path = verdicts.write_run(self.state, [self._one()], run_ts="20260101T000000Z")
        self.assertIsNotNone(path)
        rows = verdicts.read_run(path)
        self.assertEqual(rows[-1]["id"], "row-0")

    def test_the_file_is_mode_600(self):
        path = verdicts.write_run(self.state, [self._one()], run_ts="20260101T000000Z")
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)

    def test_a_meta_line_records_how_the_run_sampled(self):
        path = verdicts.write_run(self.state, [self._one()], run_ts="20260101T000000Z",
                                  meta={"sample": {"judgeable": 3}})
        self.assertEqual(verdicts.read_run(path)[0]["_meta"]["sample"]["judgeable"], 3)

    def test_only_the_last_twenty_runs_are_kept(self):
        for i in range(25):
            verdicts.write_run(self.state, [self._one()], run_ts="202601%02dT000000Z" % (i + 1))
        files = sorted(verdicts.verdict_dir(self.state).glob("*.jsonl"))
        self.assertEqual(len(files), verdicts.KEEP_RUNS)
        self.assertEqual(files[-1].name, "20260125T000000Z.jsonl")

    def test_no_row_content_is_copied_into_the_verdict_file(self):
        path = verdicts.write_run(
            self.state,
            [self._one(command="rm -rf /secret/path", cwd="/home/someone")],
            run_ts="20260101T000000Z",
        )
        blob = Path(path).read_text()
        self.assertNotIn("/secret/path", blob)
        self.assertNotIn("/home/someone", blob)

    def test_an_unwritable_state_dir_returns_none_instead_of_killing_the_run(self):
        self.assertIsNone(verdicts.write_run(Path("/proc/nonexistent"), [self._one()]))

    def test_nothing_to_write_is_not_an_empty_file(self):
        self.assertIsNone(verdicts.write_run(self.state, []))


# --- verdict normalisation --------------------------------------------------


class TestNormaliseVerdict(unittest.TestCase):
    def test_a_wrong_label_is_wrong(self):
        v = tune.normalise_verdict(
            {"label_correct": False, "action_correct": True, "correct_label": "not_a_search"},
            _answered_row(), 0)
        self.assertTrue(v["wrong"])
        self.assertEqual(v["jev_label"], "filename_search")
        self.assertEqual(v["correct_label"], "not_a_search")

    def test_a_wrong_action_with_a_right_label_is_still_wrong(self):
        v = tune.normalise_verdict(
            {"label_correct": True, "action_correct": False}, _answered_row(), 0)
        self.assertTrue(v["wrong"])

    def test_cannot_tell_is_never_wrong(self):
        v = tune.normalise_verdict(
            {"cannot_tell": True, "label_correct": False, "action_correct": False},
            _answered_row(), 0)
        self.assertFalse(v["wrong"])
        self.assertTrue(v["cannot_tell"])
        self.assertIsNone(v["label_correct"])

    def test_the_old_single_flag_schema_degrades_to_cannot_tell_not_to_wrong(self):
        """The exact failure: a verdict carrying no answer to either question
        used to count as a guard error."""
        v = tune.normalise_verdict({"jev_correct": False, "reason": "x"},
                                   _answered_row(), 0)
        self.assertTrue(v["cannot_tell"])
        self.assertFalse(v["wrong"])

    def test_junk_in_place_of_a_verdict_is_cannot_tell(self):
        v = tune.normalise_verdict("not a dict", _answered_row(), 0)
        self.assertTrue(v["cannot_tell"])
        self.assertFalse(v["wrong"])

    def test_the_sample_rule_survives_onto_the_verdict(self):
        row = _answered_row()
        row["_sample_rule"] = sampling.SAMPLE_RULE_RANDOM
        v = tune.normalise_verdict({"label_correct": True, "action_correct": True}, row, 0)
        self.assertEqual(v["sample_rule"], sampling.SAMPLE_RULE_RANDOM)


class TestDescribeAction(unittest.TestCase):
    def test_a_configured_deny_that_did_not_fire_is_reported_as_allowed(self):
        # 18 of the 20 rows in the bad run looked exactly like this.
        row = _answered_row(action="deny", would_deny=False, enforced=False)
        self.assertEqual(tune.describe_action(row), "allowed_silently")

    def test_a_skipped_row_names_why_it_was_skipped(self):
        row = _answered_row(action="deny", skipped="no_deny_possible")
        self.assertEqual(tune.describe_action(row), "skipped:no_deny_possible")

    def test_an_enforced_row_is_blocked(self):
        self.assertEqual(tune.describe_action(_answered_row(enforced=True)), "blocked")

    def test_a_flagged_but_unenforced_row_says_so(self):
        row = _answered_row(would_deny=True, enforced=False)
        self.assertEqual(tune.describe_action(row), "flagged_would_deny_not_enforced")


class TestExpectedCaseFields(unittest.TestCase):
    def test_the_deny_expectation_is_not_copied_off_the_row(self):
        """A row the judge just called wrong is the last place to read the
        correct answer from."""
        row = _answered_row(would_deny=True, scope="single_repo")
        expected = tune.expected_case_fields(row, "tool_choice_guard", "not_a_search")
        self.assertEqual(expected["search_intent"], "not_a_search")
        self.assertFalse(expected.get("would_deny"))

    def test_a_row_missing_what_the_policy_needs_is_marked_unverified(self):
        row = {"guard": "tier_guard", "answers": {"task_kind": {"choice": "lookup"}}}
        expected = tune.expected_case_fields(row, "tier_guard", "judgement")
        self.assertEqual(expected["deny_expectation"], "unverified")
        self.assertNotIn("would_deny", expected)

    def test_a_tier_case_carries_the_block_expectation_the_policy_produces(self):
        row = {"guard": "tier_guard", "chosen_type": "fable", "margin": 1.0,
               "prior_failed": 1.0,
               "answers": {"task_kind": {"choice": "hard_problem", "confidence": 1.0}}}
        expected = tune.expected_case_fields(row, "tier_guard", "lookup")
        self.assertTrue(expected["expect_block"])

    def test_no_correct_label_means_no_case(self):
        self.assertIsNone(
            tune.expected_case_fields(_answered_row(), "tool_choice_guard", None))


if __name__ == "__main__":
    unittest.main()
