#!/usr/bin/env python3
"""Check every relative link in README.md and docs/ resolves, and that every
Mermaid block is structurally sound.

Run from the repository root:

    python3 tools/check_docs.py

Exits non-zero and prints one line per problem. No dependencies.
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# [text](target) -- but not images with an external URL, and not reference defs.
LINK_RE = re.compile(r"!?\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
# src="..." inside the HTML header block.
SRC_RE = re.compile(r"<img[^>]*\ssrc=\"([^\"]+)\"")
FENCE_RE = re.compile(r"^```(\w*)\s*$")
HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
MERMAID_HEADER_RE = re.compile(r"^(flowchart|graph)\s+(TD|TB|BT|LR|RL)\s*$")

SKIP_SCHEMES = ("http://", "https://", "mailto:", "#")


def markdown_files():
    out = [os.path.join(ROOT, "README.md")]
    for name in ("AGENTS.md", "CLAUDE.md", "ROADMAP.md"):
        p = os.path.join(ROOT, name)
        if os.path.isfile(p):
            out.append(p)
    docs = os.path.join(ROOT, "docs")
    for dirpath, _dirnames, filenames in os.walk(docs):
        for f in sorted(filenames):
            if f.endswith(".md"):
                out.append(os.path.join(dirpath, f))
    return out


def anchors_in(path):
    """GitHub-style anchor slugs for every ATX heading in a file."""
    slugs = set()
    try:
        text = open(path, encoding="utf-8").read()
    except OSError:
        return slugs
    for line in text.splitlines():
        m = re.match(r"^(#{1,6})\s+(.*?)\s*$", line)
        if not m:
            continue
        title = m.group(2)
        title = re.sub(r"`([^`]*)`", r"\1", title)
        title = re.sub(r"\*\*?([^*]*)\*\*?", r"\1", title)
        title = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", title)
        slug = title.lower()
        slug = re.sub(r"[^\w\s-]", "", slug)
        slug = re.sub(r"\s+", "-", slug.strip())
        slugs.add(slug)
    # Explicit HTML anchors, e.g. <a id="x">, and <h1>-style ids.
    for m in re.finditer(r"(?:id|name)=\"([^\"]+)\"", text):
        slugs.add(m.group(1))
    return slugs


def check_links(problems, parked):
    for path in markdown_files():
        rel = os.path.relpath(path, ROOT)
        raw = open(path, encoding="utf-8").read()
        # A target inside an HTML comment is parked on purpose (the hero image
        # the README expects but nobody has generated yet). Report it as a
        # note, never as a failure: the page renders correctly without it.
        for comment in HTML_COMMENT_RE.findall(raw):
            for m in list(LINK_RE.finditer(comment)) + list(SRC_RE.finditer(comment)):
                t = m.group(1)
                if t.startswith(SKIP_SCHEMES):
                    continue
                resolved = os.path.normpath(
                    os.path.join(os.path.dirname(path), t.partition("#")[0]))
                if not os.path.exists(resolved):
                    parked.append("%s: %s (commented out, file absent)" % (rel, t))
        text = HTML_COMMENT_RE.sub("", raw)
        targets = [m.group(1) for m in LINK_RE.finditer(text)]
        targets += [m.group(1) for m in SRC_RE.finditer(text)]
        for target in targets:
            if target.startswith(SKIP_SCHEMES):
                if target.startswith("#"):
                    anchor = target[1:]
                    if anchor and anchor not in anchors_in(path):
                        problems.append(
                            "%s: in-page anchor #%s has no matching heading"
                            % (rel, anchor))
                continue
            file_part, _, anchor = target.partition("#")
            if not file_part:
                continue
            resolved = os.path.normpath(
                os.path.join(os.path.dirname(path), file_part))
            if not os.path.exists(resolved):
                problems.append("%s: link target does not exist: %s"
                                % (rel, target))
                continue
            if anchor and resolved.endswith(".md"):
                if anchor not in anchors_in(resolved):
                    problems.append(
                        "%s: %s exists but has no anchor #%s"
                        % (rel, file_part, anchor))


def check_mermaid(problems):
    for path in markdown_files():
        rel = os.path.relpath(path, ROOT)
        lines = open(path, encoding="utf-8").read().splitlines()
        in_block = False
        block = []
        start = 0
        for n, line in enumerate(lines, 1):
            m = FENCE_RE.match(line)
            if m and not in_block:
                if m.group(1) == "mermaid":
                    in_block, block, start = True, [], n
                continue
            if line.strip() == "```" and in_block:
                check_one_mermaid(rel, start, block, problems)
                in_block = False
                continue
            if in_block:
                block.append(line)
        if in_block:
            problems.append("%s: unterminated mermaid block opened at line %d"
                            % (rel, start))


def check_one_mermaid(rel, start, block, problems):
    body = [b for b in block if b.strip()]
    if not body:
        problems.append("%s:%d: empty mermaid block" % (rel, start))
        return
    if not MERMAID_HEADER_RE.match(body[0].strip()):
        problems.append("%s:%d: mermaid block does not start with a valid "
                        "flowchart/graph header: %r" % (rel, start, body[0]))
    for offset, line in enumerate(block):
        lineno = start + 1 + offset
        if line.count('"') % 2:
            problems.append("%s:%d: odd number of quotes in mermaid line"
                            % (rel, lineno))
        for opener, closer in (("[", "]"), ("(", ")"), ("{", "}")):
            if line.count(opener) != line.count(closer):
                problems.append("%s:%d: unbalanced %s%s in mermaid line"
                                % (rel, lineno, opener, closer))
        # Any label text must be quoted: bare special characters are what
        # actually breaks GitHub's renderer.
        for label in re.findall(r"[\[\{]([^\[\]\{\}]*)[\]\}]", line):
            label = label.strip()
            if not label:
                continue
            if label.startswith('"') and label.endswith('"'):
                continue
            problems.append("%s:%d: unquoted mermaid label %r"
                            % (rel, lineno, label))
        # Edge labels use |...|; those must not contain a bare quote-breaker.
        for edge in re.findall(r"\|([^|]*)\|", line):
            if not (edge.strip().startswith('"')
                    and edge.strip().endswith('"')):
                problems.append("%s:%d: unquoted mermaid edge label %r"
                                % (rel, lineno, edge))


def main():
    problems = []
    parked = []
    check_links(problems, parked)
    check_mermaid(problems)
    for note in parked:
        print("NOTE parked image reference: %s" % note)
    if problems:
        for p in problems:
            print("FAIL %s" % p)
        print("\n%d problem(s)" % len(problems))
        return 1
    print("OK: every relative link resolves and every mermaid block parses")
    return 0


if __name__ == "__main__":
    sys.exit(main())
