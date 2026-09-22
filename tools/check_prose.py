#!/usr/bin/env python3
"""Check Markdown prose for machine-writing tells, flat rhythm and walls of text.

Run from the repository root:

    python3 tools/check_prose.py README.md
    python3 tools/check_prose.py --fix-hints README.md docs/*.md

Prints one block per finding -- file, line, rule, the offending text -- and exits
1 if there is at least one. No dependencies, stdlib only.

What it checks
--------------

``banned``              a phrase from ``tools/prose/banned-phrases.txt``
``em-dash``             an em dash, an en dash between words, or ``' -- '``
``long-sentence``       a sentence over ``--max-sentence-words`` words
``long-paragraph``      a paragraph over ``--max-paragraph-words`` words
``wall-of-text``        over ``--max-prose-lines`` source lines with no break
``flat-rhythm``         three or more sentences running at the same length
``anaphora``            three or more sentences opening on the same word
``parens``              more than ``--max-parens`` parenthetical asides
``rhetorical-question`` a question whose own paragraph answers it

What it skips
-------------

Fenced code, inline code, tables, HTML blocks, block quotes (quoted output and
pasted text are quotations, not this repository's prose), link targets and bare
URLs. Headings are checked for banned phrases and dashes only: a heading has no
sentence rhythm to measure.

Every threshold is a flag, and every rule can be switched off with ``--skip``,
because a gate nobody can tune is a gate somebody disables.
"""
from __future__ import annotations

import argparse
import bisect
import os
import re
import sys
from dataclasses import dataclass, field

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_PHRASES = os.path.join(ROOT, "tools", "prose", "banned-phrases.txt")

MAX_SENTENCE_WORDS = 35
MAX_PARAGRAPH_WORDS = 90
MAX_PROSE_LINES = 8
MAX_PARENS = 2

#: A run of this many sentences whose lengths sit inside :data:`FLAT_SPREAD`
#: words of each other reads as a metronome.
FLAT_RUN = 3
FLAT_SPREAD = 2
#: Sentences under this many words are too short for "same length" to mean
#: anything, so they neither start nor extend a flat run.
FLAT_MIN_WORDS = 8

ANAPHORA_RUN = 3

ALL_RULES = (
    "banned",
    "em-dash",
    "long-sentence",
    "long-paragraph",
    "wall-of-text",
    "flat-rhythm",
    "anaphora",
    "parens",
    "rhetorical-question",
)

RULE_HINTS = {
    "em-dash": "use a full stop, a comma with 'and'/'but', a colon, or brackets",
    "long-sentence": "split it; put the number and the noun in the first half",
    "long-paragraph": "break at the first new sub-point, or make it a list",
    "wall-of-text": "break it with a sub-heading, a list, a table or an example",
    "flat-rhythm": "cut one sentence to under eight words",
    "anaphora": "rewrite one of the openings, or join two of the sentences",
    "parens": "keep one aside; promote the rest to their own sentence",
    "rhetorical-question": "delete the question and state the answer",
}


# ----------------------------------------------------------------------------
# the phrase list
# ----------------------------------------------------------------------------

#: Inflections a single-word entry is matched in. Deliberately small and
#: English-specific; no irregular verbs and no pluralisation rules, because a
#: false positive on a gate costs more than a missed tic.
_INFLECTIONS = ("s", "es", "d", "ed", "ing")
_APOSTROPHES = "'’‘ʼ"
_APOSTROPHE_CLASS = "[" + _APOSTROPHES + "]"


@dataclass(frozen=True)
class Phrase:
    """One entry from the phrase list, compiled."""

    text: str
    pattern: re.Pattern[str]
    hint: str = ""


def _apostrophe_insensitive(escaped: str) -> str:
    """Widen every apostrophe in an ALREADY-ESCAPED pattern to match any variant.

    One pass, character by character. Looping ``str.replace`` per variant instead
    nests the class inside itself, which compiles fine and then matches nothing.
    """
    return "".join(_APOSTROPHE_CLASS if ch in _APOSTROPHES else ch for ch in escaped)


def phrase_pattern(phrase: str) -> re.Pattern[str]:
    """Word-boundary-anchored, case-insensitive pattern for one phrase.

    A trailing ``\\b`` does not make a phrase match its own inflections:
    ``\\bdelve\\b`` cannot match "delved", because "e" to "d" is word-to-word and
    there is no boundary there to find. So the suffix group is built explicitly,
    and it is applied to the last word only, which keeps a multi-word phrase
    anchored where it should be.
    """
    escaped = _apostrophe_insensitive(re.escape(phrase))
    if phrase.endswith("e"):
        stem = _apostrophe_insensitive(re.escape(phrase[:-1]))
        body = stem + "(?:e|es|ed|ing)"
    else:
        body = escaped + "(?:" + "|".join(_INFLECTIONS) + ")?"
    return re.compile(r"\b" + body + r"\b", re.IGNORECASE)


def load_phrases(path: str) -> list:
    """Read a phrase file into compiled :class:`Phrase` entries."""
    phrases = []
    with open(path, encoding="utf-8") as fh:
        for raw in fh:
            line = raw.split("#", 1)[0].strip() if raw.lstrip().startswith("#") else raw.rstrip("\n")
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            hint = ""
            if "::" in line:
                line, _, hint = line.partition("::")
                line, hint = line.strip(), hint.strip()
            if line.startswith("re:"):
                body = line[3:].strip()
                phrases.append(Phrase(body, re.compile(body, re.IGNORECASE), hint))
            else:
                phrases.append(Phrase(line, phrase_pattern(line), hint))
    return phrases


# ----------------------------------------------------------------------------
# masking: what is not this document's prose
# ----------------------------------------------------------------------------

_MASKS = (
    re.compile(r"`[^`\n]*`"),                     # inline code
    re.compile(r"<[^<>\n]{1,200}>"),              # an inline HTML tag
    re.compile(r"\]\([^)\s]*\)"),                 # a link target, keeping the text
    re.compile(r"\bhttps?://\S+"),                # a bare URL
    re.compile(r"!\[[^\]]*\]"),                   # image alt text
)


def mask(text: str) -> str:
    """Blank every span that is not prose, keeping every offset where it was.

    Equal-length blanking rather than deletion is what lets a finding report the
    line it is really on: the masked string and the joined source string index
    identically.
    """
    out = text
    for pattern in _MASKS:
        out = pattern.sub(lambda m: " " * len(m.group(0)), out)
    return out


# ----------------------------------------------------------------------------
# block parsing
# ----------------------------------------------------------------------------

FENCE_RE = re.compile(r"^\s{0,3}(```|~~~)")
HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s")
TABLE_RE = re.compile(r"^\s*\|")
QUOTE_RE = re.compile(r"^\s{0,3}>")
LIST_RE = re.compile(r"^\s{0,3}(?:[-*+]\s+|\d{1,9}[.)]\s+)")
HTML_RE = re.compile(r"^\s*<")
COMMENT_RE = re.compile(r"^\s*<!--")


@dataclass
class Block:
    """One run of consecutive source lines of a single kind."""

    kind: str
    lines: list = field(default_factory=list)  # (lineno, raw text)
    section: str = ""

    @property
    def start(self) -> int:
        return self.lines[0][0]

    def joined(self):
        """The block as one string, with an offset-to-line index beside it."""
        parts = []
        starts = []
        offset = 0
        for lineno, raw in self.lines:
            piece = raw.strip()
            starts.append((offset, lineno))
            parts.append(piece)
            offset += len(piece) + 1
        return " ".join(parts), starts


def line_kind(line: str, in_list: bool) -> str:
    """Classify one source line. ``in_list`` keeps a wrapped bullet a bullet."""
    if not line.strip():
        return "blank"
    if HEADING_RE.match(line):
        return "heading"
    if TABLE_RE.match(line):
        return "table"
    if QUOTE_RE.match(line):
        return "quote"
    if LIST_RE.match(line):
        return "list"
    if COMMENT_RE.match(line) or HTML_RE.match(line):
        return "html"
    if in_list and line[:1] in (" ", "\t"):
        return "list"
    return "prose"


def parse_blocks(text: str) -> list:
    """Split a Markdown document into typed blocks.

    Fences are consumed whole, before anything else looks at their contents: a
    blank line or a ``|`` inside a code sample means nothing to this parser.
    An HTML block runs to the next blank line, so a multi-line ``<p>`` stays one
    block instead of leaking its middle lines into the prose checks.
    """
    lines = text.splitlines()
    blocks = []
    current = None
    section = ""
    in_list = False
    in_html = False
    i = 0

    def close():
        nonlocal current
        if current is not None:
            blocks.append(current)
            current = None

    while i < len(lines):
        line = lines[i]
        fence = FENCE_RE.match(line)
        if fence:
            close()
            marker = fence.group(1)
            block = Block("code", [(i + 1, line)], section)
            i += 1
            while i < len(lines):
                block.lines.append((i + 1, lines[i]))
                if lines[i].strip().startswith(marker):
                    i += 1
                    break
                i += 1
            blocks.append(block)
            in_list = False
            in_html = False
            continue

        if not line.strip():
            close()
            in_list = False
            in_html = False
            i += 1
            continue

        kind = "html" if in_html else line_kind(line, in_list)
        if kind == "html":
            in_html = True
        in_list = kind == "list"

        if kind == "heading":
            section = line.lstrip("# ").strip()

        if current is None or current.kind != kind:
            close()
            current = Block(kind, [], section)
        current.lines.append((i + 1, line))
        if kind == "heading":
            close()
        i += 1

    close()
    return blocks


# ----------------------------------------------------------------------------
# sentences
# ----------------------------------------------------------------------------

_BOUNDARY_RE = re.compile(r"[.!?]+[\"'”’)\]*_]*(?=\s)")
_TRAILING_TOKEN_RE = re.compile(r"(\S+)$")
_ABBREVIATIONS = frozenset(
    {"e.g.", "i.e.", "mr.", "mrs.", "ms.", "dr.", "vs.", "approx.", "etc.",
     "prof.", "st.", "jr.", "sr.", "no.", "fig."}
)
_WORD_RE = re.compile(r"[0-9A-Za-z][\w'’./%-]*")


def split_sentences(text: str) -> list:
    """Split joined prose into ``(offset, sentence)`` pairs."""
    out = []
    start = 0
    for m in _BOUNDARY_RE.finditer(text):
        end = m.end()
        token_match = _TRAILING_TOKEN_RE.search(text[:end])
        token = token_match.group(1).lower() if token_match else ""
        if token in _ABBREVIATIONS:
            continue
        piece = text[start:end]
        if piece.strip():
            out.append((start + len(piece) - len(piece.lstrip()), piece.strip()))
        start = end
    tail = text[start:]
    if tail.strip():
        out.append((start + len(tail) - len(tail.lstrip()), tail.strip()))
    return out


def word_count(sentence: str) -> int:
    return len(_WORD_RE.findall(sentence))


# ----------------------------------------------------------------------------
# findings
# ----------------------------------------------------------------------------


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    rule: str
    text: str
    hint: str = ""

    def render(self, show_hints: bool) -> str:
        head = f"{self.path}:{self.line}: {self.rule}: {self.text}"
        if show_hints and self.hint:
            return head + "\n    hint: " + self.hint
        return head


def _line_at(starts, offset: int) -> int:
    idx = bisect.bisect_right([s for s, _ in starts], offset) - 1
    return starts[max(idx, 0)][1]


def _excerpt(text: str, offset: int, width: int = 72) -> str:
    left = max(offset - 20, 0)
    piece = text[left:left + width].strip()
    return ("..." if left else "") + piece + ("..." if left + width < len(text) else "")


# ----------------------------------------------------------------------------
# the rules
# ----------------------------------------------------------------------------

_EM_DASH_RE = re.compile(r"—|(?<=\s)–(?=\s)|(?<=\s)--(?=\s)|(?<=\w)--(?=\w)")


def check_block(path: str, block: Block, phrases, opts) -> list:
    """Every finding in one block. A block that is not prose gets the two
    rules that apply to any visible sentence and none of the rhythm rules."""
    findings = []
    if block.kind in ("code", "table", "quote", "html"):
        return findings

    joined, starts = block.joined()
    text = mask(joined)

    if "banned" not in opts.skip:
        for phrase in phrases:
            for m in phrase.pattern.finditer(text):
                findings.append(Finding(
                    path, _line_at(starts, m.start()), "banned",
                    f'"{m.group(0)}" in: {_excerpt(joined, m.start())}',
                    phrase.hint,
                ))

    if "em-dash" not in opts.skip:
        for m in _EM_DASH_RE.finditer(text):
            findings.append(Finding(
                path, _line_at(starts, m.start()), "em-dash",
                _excerpt(joined, m.start()), RULE_HINTS["em-dash"],
            ))

    if block.kind != "prose":
        return findings

    sentences = split_sentences(text)
    counts = [word_count(s) for _, s in sentences]

    if "long-sentence" not in opts.skip:
        for (offset, sentence), count in zip(sentences, counts):
            if count > opts.max_sentence_words:
                findings.append(Finding(
                    path, _line_at(starts, offset), "long-sentence",
                    f"{count} words: {_excerpt(joined, offset)}",
                    RULE_HINTS["long-sentence"],
                ))

    if "long-paragraph" not in opts.skip:
        total = word_count(text)
        if total > opts.max_paragraph_words:
            findings.append(Finding(
                path, block.start, "long-paragraph",
                f"{total} words: {_excerpt(joined, 0)}",
                RULE_HINTS["long-paragraph"],
            ))

    if "wall-of-text" not in opts.skip and len(block.lines) > opts.max_prose_lines:
        findings.append(Finding(
            path, block.start, "wall-of-text",
            "{} unbroken lines under '{}': {}".format(
                len(block.lines), block.section or "(no heading)", _excerpt(joined, 0)),
            RULE_HINTS["wall-of-text"],
        ))

    if "parens" not in opts.skip:
        asides = [m for m in re.finditer(r"\([^)]{4,}\)", text)]
        if len(asides) > opts.max_parens:
            findings.append(Finding(
                path, _line_at(starts, asides[opts.max_parens].start()), "parens",
                f"{len(asides)} asides in one paragraph: {_excerpt(joined, asides[opts.max_parens].start())}",
                RULE_HINTS["parens"],
            ))

    if "flat-rhythm" not in opts.skip:
        run_start = 0
        while run_start < len(counts):
            if counts[run_start] < FLAT_MIN_WORDS:
                run_start += 1
                continue
            end = run_start + 1
            while end < len(counts) and counts[end] >= FLAT_MIN_WORDS and (
                max(counts[run_start:end + 1]) - min(counts[run_start:end + 1]) <= FLAT_SPREAD
            ):
                end += 1
            if end - run_start >= FLAT_RUN:
                offset = sentences[run_start][0]
                findings.append(Finding(
                    path, _line_at(starts, offset), "flat-rhythm",
                    "{} sentences of {} words: {}".format(
                        end - run_start, "/".join(str(c) for c in counts[run_start:end]),
                        _excerpt(joined, offset)),
                    RULE_HINTS["flat-rhythm"],
                ))
                run_start = end
            else:
                run_start += 1

    if "anaphora" not in opts.skip:
        openers = []
        for offset, sentence in sentences:
            first = _WORD_RE.search(sentence)
            openers.append(first.group(0).lower() if first else "")
        run_start = 0
        while run_start < len(openers):
            end = run_start + 1
            while end < len(openers) and openers[end] and openers[end] == openers[run_start]:
                end += 1
            if end - run_start >= ANAPHORA_RUN and openers[run_start]:
                offset = sentences[run_start][0]
                findings.append(Finding(
                    path, _line_at(starts, offset), "anaphora",
                    f'{end - run_start} sentences opening on "{openers[run_start]}": {_excerpt(joined, offset)}',
                    RULE_HINTS["anaphora"],
                ))
            run_start = end

    if "rhetorical-question" not in opts.skip:
        for offset, sentence in sentences[:-1]:
            if sentence.rstrip().endswith("?"):
                findings.append(Finding(
                    path, _line_at(starts, offset), "rhetorical-question",
                    _excerpt(joined, offset), RULE_HINTS["rhetorical-question"],
                ))

    return findings


def check_text(path: str, text: str, phrases, opts) -> list:
    findings = []
    for block in parse_blocks(text):
        findings.extend(check_block(path, block, phrases, opts))
    findings.sort(key=lambda f: (f.line, f.rule))
    return findings


def check_file(path: str, phrases, opts) -> list:
    with open(path, encoding="utf-8") as fh:
        return check_text(os.path.relpath(path, ROOT) if os.path.isabs(path) else path,
                          fh.read(), phrases, opts)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Check Markdown prose for machine-writing tells and flat rhythm.")
    parser.add_argument("paths", nargs="+", help="Markdown files to check")
    parser.add_argument("--phrases", default=DEFAULT_PHRASES,
                        help="phrase list (default: tools/prose/banned-phrases.txt)")
    parser.add_argument("--fix-hints", action="store_true",
                        help="print a plainer form where a mechanical one exists")
    parser.add_argument("--max-sentence-words", type=int, default=MAX_SENTENCE_WORDS)
    parser.add_argument("--max-paragraph-words", type=int, default=MAX_PARAGRAPH_WORDS)
    parser.add_argument("--max-prose-lines", type=int, default=MAX_PROSE_LINES)
    parser.add_argument("--max-parens", type=int, default=MAX_PARENS)
    parser.add_argument("--skip", default="", help="comma-separated rule names to ignore")
    return parser


def main(argv=None) -> int:
    opts = build_parser().parse_args(argv)
    opts.skip = {r.strip() for r in opts.skip.split(",") if r.strip()}
    unknown = opts.skip - set(ALL_RULES)
    if unknown:
        print("unknown rule(s): " + ", ".join(sorted(unknown)), file=sys.stderr)
        print("known rules: " + ", ".join(ALL_RULES), file=sys.stderr)
        return 2

    phrases = load_phrases(opts.phrases)
    findings = []
    for path in opts.paths:
        findings.extend(check_file(path, phrases, opts))

    for finding in findings:
        print(finding.render(opts.fix_hints))

    if findings:
        counts = {}
        for finding in findings:
            counts[finding.rule] = counts.get(finding.rule, 0) + 1
        summary = ", ".join(f"{v} {k}" for k, v in sorted(counts.items()))
        print(f"\n{len(findings)} finding(s): {summary}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
