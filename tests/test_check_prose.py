"""Unit tests for tools/check_prose.py.

The tool lives in ``tools/`` rather than in a package, so these tests load it by
path the same way a contributor runs it: ``python3 tools/check_prose.py``.
"""
import importlib.util
import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOL = os.path.join(ROOT, "tools", "check_prose.py")


def _load_tool():
    """Import tools/check_prose.py by path.

    It must be in ``sys.modules`` before ``exec_module`` runs: a frozen
    dataclass whose field is annotated with a string resolves that annotation
    through ``sys.modules[cls.__module__]``, and on Python 3.14 a missing entry
    there raises ``AttributeError`` at class-creation time.
    """
    spec = importlib.util.spec_from_file_location("check_prose", TOOL)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["check_prose"] = module
    spec.loader.exec_module(module)
    return module


cp = _load_tool()


class Opts:
    """The subset of the argparse namespace the checks read."""

    def __init__(self, **kw):
        self.max_sentence_words = cp.MAX_SENTENCE_WORDS
        self.max_paragraph_words = cp.MAX_PARAGRAPH_WORDS
        self.max_prose_lines = cp.MAX_PROSE_LINES
        self.max_parens = cp.MAX_PARENS
        self.skip = set()
        for k, v in kw.items():
            setattr(self, k, v)


def run(text, phrases=(), **kw):
    return cp.check_text("t.md", text, list(phrases), Opts(**kw))


def rules(findings):
    return sorted({f.rule for f in findings})


class PhraseLoadingTests(unittest.TestCase):
    def test_comments_and_blanks_are_ignored(self):
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as fh:
            fh.write("# a comment\n\ndelve\n  \n# another\nseamless :: say what it does\n")
            path = fh.name
        try:
            phrases = cp.load_phrases(path)
        finally:
            os.unlink(path)
        self.assertEqual([p.text for p in phrases], ["delve", "seamless"])
        self.assertEqual(phrases[1].hint, "say what it does")

    def test_regex_entries(self):
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as fh:
            fh.write(r"re:\bnot just (a|the)\b :: say what it is" + "\n")
            path = fh.name
        try:
            phrases = cp.load_phrases(path)
        finally:
            os.unlink(path)
        self.assertTrue(phrases[0].pattern.search("It is not just a guard."))
        self.assertFalse(phrases[0].pattern.search("It is a guard."))

    def test_shipped_list_loads_and_is_generic(self):
        phrases = cp.load_phrases(cp.DEFAULT_PHRASES)
        self.assertGreater(len(phrases), 50)
        blob = open(cp.DEFAULT_PHRASES, encoding="utf-8").read().lower()
        for project_word in ("jev", "airlock", "typesafe", "claude"):
            self.assertNotIn(project_word, blob)


class PatternTests(unittest.TestCase):
    def test_inflections_of_an_e_word(self):
        pattern = cp.phrase_pattern("delve")
        for form in ("delve", "delves", "delved", "delving"):
            self.assertTrue(pattern.search("we " + form + " in"), form)
        self.assertFalse(pattern.search("twelve items"))

    def test_inflections_of_a_consonant_word(self):
        pattern = cp.phrase_pattern("unlock the")
        self.assertTrue(pattern.search("Unlock the value"))
        self.assertFalse(pattern.search("unlocked the value"))

    def test_apostrophe_variants_match(self):
        pattern = cp.phrase_pattern("it's worth noting")
        self.assertTrue(pattern.search("It's worth noting that"))
        self.assertTrue(pattern.search("It’s worth noting that"))

    def test_word_boundaries_hold(self):
        pattern = cp.phrase_pattern("robust")
        self.assertTrue(pattern.search("a robust design"))
        self.assertFalse(pattern.search("robustness testing"))


class SkippedRegionTests(unittest.TestCase):
    phrases = [cp.Phrase("delve", cp.phrase_pattern("delve"))]

    def test_fenced_code_is_skipped(self):
        text = "Prose here.\n\n```bash\ndelve --  a -- b\n```\n"
        self.assertEqual(run(text, self.phrases), [])

    def test_inline_code_is_skipped(self):
        self.assertEqual(run("Run `delve -- now` to start.\n", self.phrases), [])

    def test_tables_are_skipped(self):
        self.assertEqual(run("| a | b |\n|---|---|\n| delve | x -- y |\n", self.phrases), [])

    def test_html_blocks_are_skipped(self):
        text = "<p>\n  we delve into it -- always\n</p>\n"
        self.assertEqual(run(text, self.phrases), [])

    def test_block_quotes_are_skipped(self):
        self.assertEqual(run("> we delve into it -- always\n", self.phrases), [])

    def test_link_targets_and_urls_are_skipped(self):
        text = "See [the notes](docs/delve--notes.md) and https://example.com/delve--x for more.\n"
        self.assertEqual(run(text, self.phrases), [])

    def test_prose_after_a_fence_is_still_checked(self):
        text = "```\ncode\n```\n\nWe delve into it.\n"
        findings = run(text, self.phrases)
        self.assertEqual([f.rule for f in findings], ["banned"])
        self.assertEqual(findings[0].line, 5)


class RuleTests(unittest.TestCase):
    def test_banned_phrase_reports_line_and_hint(self):
        phrases = [cp.Phrase("seamless", cp.phrase_pattern("seamless"), "say what it does")]
        text = "First line.\n\nThe install is seamless.\n"
        findings = run(text, phrases)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].rule, "banned")
        self.assertEqual(findings[0].line, 3)
        self.assertEqual(findings[0].hint, "say what it does")

    def test_em_dash_forms(self):
        for body in ("A dash — here.", "A dash -- here.", "A dash – here."):
            self.assertIn("em-dash", rules(run(body + "\n")), body)

    def test_a_command_flag_is_not_an_em_dash(self):
        self.assertEqual(run("Pass --check-only to see the plan.\n"), [])

    def test_a_numeric_range_is_not_an_em_dash(self):
        self.assertEqual(run("It takes 314-486 ms per decision.\n"), [])

    def test_long_sentence(self):
        long = " ".join(["word"] * 40) + "."
        findings = run(long + "\n", max_paragraph_words=999)
        self.assertEqual([f.rule for f in findings], ["long-sentence"])
        self.assertIn("40 words", findings[0].text)

    def test_sentence_at_the_limit_passes(self):
        self.assertEqual(run(" ".join(["word"] * 35) + ".\n", max_paragraph_words=999), [])

    def test_long_paragraph(self):
        para = ("Short one here. " * 35).strip()
        findings = run(para + "\n", max_sentence_words=999)
        self.assertIn("long-paragraph", rules(findings))

    def test_wall_of_text(self):
        text = "## A heading\n\n" + "".join("Line number %d here.\n" % i for i in range(1, 11))
        findings = run(text, max_paragraph_words=999)
        wall = [f for f in findings if f.rule == "wall-of-text"]
        self.assertEqual(len(wall), 1)
        self.assertIn("A heading", wall[0].text)
        self.assertEqual(wall[0].line, 3)

    def test_eight_lines_is_not_a_wall(self):
        text = "".join("Line number %d here.\n" % i for i in range(1, 9))
        self.assertNotIn("wall-of-text", rules(run(text, max_paragraph_words=999)))

    def test_parens(self):
        text = ("It runs here (on Linux) and there (on WSL) and also "
                "elsewhere (on Windows) today.\n")
        findings = run(text)
        self.assertIn("parens", rules(findings))

    def test_two_parens_pass(self):
        self.assertEqual(run("It runs here (on Linux) and there (on WSL).\n"), [])

    def test_flat_rhythm(self):
        text = ("The guard reads the call and then decides what to do next now. "
                "The daemon holds the connection open so the call stays warm here. "
                "The logger writes one row for every call the rules table claims.\n")
        findings = run(text, max_paragraph_words=999)
        self.assertIn("flat-rhythm", rules(findings))

    def test_varied_rhythm_passes(self):
        text = ("The guard reads the call and then decides what to do about it next. "
                "It fails open. The daemon holds one warm connection so that a "
                "judgement costs a third of a second rather than most of one.\n")
        self.assertNotIn("flat-rhythm", rules(run(text, max_paragraph_words=999)))

    def test_anaphora(self):
        text = ("It fails open. It logs the call. It never blocks twice. "
                "That is the whole design.\n")
        findings = run(text)
        anaphora = [f for f in findings if f.rule == "anaphora"]
        self.assertEqual(len(anaphora), 1)
        self.assertIn('opening on "it"', anaphora[0].text)

    def test_rhetorical_question(self):
        findings = run("Does it slow the agent down? No, it adds 33 ms.\n")
        self.assertIn("rhetorical-question", rules(findings))

    def test_a_trailing_question_is_not_rhetorical(self):
        self.assertNotIn("rhetorical-question",
                         rules(run("It adds 33 ms. Is that too slow?\n")))

    def test_headings_get_phrases_but_not_rhythm(self):
        phrases = [cp.Phrase("delve", cp.phrase_pattern("delve"))]
        text = "## We delve into " + " ".join(["things"] * 40) + "\n"
        self.assertEqual([f.rule for f in run(text, phrases)], ["banned"])

    def test_abbreviations_do_not_split_a_sentence(self):
        text = "Use e.g. the flag and i.e. the file " + " ".join(["word"] * 34) + ".\n"
        findings = run(text, max_paragraph_words=999)
        self.assertEqual([f.rule for f in findings], ["long-sentence"])


class OrderingAndCliTests(unittest.TestCase):
    def test_findings_are_sorted_by_line(self):
        text = "A dash -- here.\n\n" + " ".join(["word"] * 40) + ".\n"
        findings = run(text, max_paragraph_words=999)
        self.assertEqual([f.line for f in findings], sorted(f.line for f in findings))

    def test_skip_switches_a_rule_off(self):
        self.assertEqual(run("A dash -- here.\n", skip={"em-dash"}), [])

    def _cli(self, argv):
        out = io.StringIO()
        err = io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cp.main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_clean_file_exits_zero(self):
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as fh:
            fh.write("The guard fails open. Every error allows the call.\n")
            path = fh.name
        try:
            code, out, _ = self._cli([path])
        finally:
            os.unlink(path)
        self.assertEqual(code, 0)
        self.assertEqual(out, "")

    def test_dirty_file_exits_one_and_prints_a_summary(self):
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as fh:
            fh.write("A dash -- here.\n")
            path = fh.name
        try:
            code, out, _ = self._cli([path])
        finally:
            os.unlink(path)
        self.assertEqual(code, 1)
        self.assertIn("em-dash", out)
        self.assertIn("1 finding(s)", out)

    def test_fix_hints_prints_a_suggestion(self):
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as fh:
            fh.write("A dash -- here.\n")
            path = fh.name
        try:
            _, plain, _ = self._cli([path])
            _, hinted, _ = self._cli(["--fix-hints", path])
        finally:
            os.unlink(path)
        self.assertNotIn("hint:", plain)
        self.assertIn("hint: ", hinted)

    def test_unknown_rule_name_exits_two(self):
        code, _, err = self._cli(["--skip", "nonsense", "README.md"])
        self.assertEqual(code, 2)
        self.assertIn("unknown rule", err)


class RepositoryTests(unittest.TestCase):
    """The gate this repository actually holds itself to."""

    def test_readme_is_clean(self):
        phrases = cp.load_phrases(cp.DEFAULT_PHRASES)
        findings = cp.check_file(os.path.join(ROOT, "README.md"), phrases, Opts())
        self.assertEqual(
            [f.render(False) for f in findings], [],
            "README.md must pass tools/check_prose.py")


if __name__ == "__main__":
    unittest.main()
