"""Decide search SCOPE in code, never by asking Jev.

Whether a search command is disk-wide, confined to one repo, or just one
directory is a fact readable straight off the command line and the working
directory -- it needs no model judgement. This module extracts the search
root(s) for a shell command and classifies scope purely from the filesystem.

Jev is left to answer only the fuzzy part: what KIND of search this is
(filename / code-structure / literal-text / not-a-search), handled in
questions.py and policy.py.
"""
import os
import shlex
from pathlib import Path

# Programs this module knows how to extract a root from.
SEARCH_PROGRAMS = {
    "find", "fd", "fdfind",
    "grep", "egrep", "fgrep", "rg", "ag", "ack",
    "tree", "ls", "du",
    "locate", "plocate",
}

# rg/ag/ack search recursively from the given (or default) directory by
# default; grep/egrep/fgrep only do so with an explicit -r/-R flag.
_RECURSIVE_BY_DEFAULT = {"rg", "ag", "ack"}
_GREP_FAMILY = {"grep", "egrep", "fgrep"}

# Flags that consume the following token as a value (best-effort; only the
# common ones we actually expect to see on this box).
_VALUE_FLAGS = {
    "-e", "--regexp", "-f", "--file",
    "-A", "-B", "-C", "-m", "--max-count",
    "-t", "--type", "-E", "--exclude", "-x", "--exec",
    "-g", "--glob", "-d", "--max-depth", "--include", "--extension",
}

_SKIP_PREFIX_TOKENS = {"sudo", "nice", "time", "env"}

_SEQUENTIAL_OPS = ("&&", "||", ";")
_PIPE_OP = ("|",)


def _split_top_level(s, ops):
    """Split `s` on any operator in `ops` that appears outside quotes.
    Operators are tried longest-first per position so '&&' isn't split as
    two '&' or '||' as two undefined single-char ops."""
    ops = sorted(ops, key=len, reverse=True)
    parts = []
    current = []
    i = 0
    n = len(s)
    quote = None
    while i < n:
        c = s[i]
        if quote:
            current.append(c)
            if c == quote:
                quote = None
            i += 1
            continue
        if c in ("'", '"'):
            quote = c
            current.append(c)
            i += 1
            continue
        matched = None
        for op in ops:
            if s.startswith(op, i):
                matched = op
                break
        if matched:
            parts.append("".join(current))
            current = []
            i += len(matched)
            continue
        current.append(c)
        i += 1
    parts.append("".join(current))
    return parts


def _safe_shlex(s):
    try:
        return shlex.split(s)
    except ValueError:
        return None


def _expand(p, cwd):
    """Expand $HOME/${HOME}/~ and resolve a relative path against cwd."""
    if p is None:
        return cwd or os.environ.get("HOME") or str(Path.home())
    home = os.environ.get("HOME") or str(Path.home())
    p = p.replace("${HOME}", home).replace("$HOME", home)
    if p == "~":
        p = home
    elif p.startswith("~/"):
        p = home + p[1:]
    if not os.path.isabs(p):
        base = cwd or os.getcwd()
        p = os.path.join(base, p)
    return os.path.normpath(p)


def _try_parse_cd(statement, cwd):
    """If `statement` is a `cd [DIR]` command, return the resulting absolute
    path. Otherwise return None."""
    tokens = _safe_shlex(statement.strip())
    if not tokens:
        return None
    if tokens[0] != "cd":
        return None
    if len(tokens) == 1:
        return os.environ.get("HOME") or str(Path.home())
    return _expand(tokens[1], cwd)


def _strip_prefixes(tokens):
    """Drop leading sudo/nice/time/env and VAR=val assignments."""
    idx = 0
    while idx < len(tokens):
        tok = tokens[idx]
        if tok == "nice":
            idx += 1
            # nice [-n N] command...
            if idx < len(tokens) and tokens[idx] == "-n":
                idx += 2
            continue
        if tok in _SKIP_PREFIX_TOKENS:
            idx += 1
            continue
        if "=" in tok and not tok.startswith("-") and tok.split("=", 1)[0].replace("_", "").isalnum():
            idx += 1
            continue
        break
    return tokens[idx:]


def _find_upward(path, relative):
    try:
        p = Path(path).expanduser()
    except Exception:
        return None
    for parent in [p] + list(p.parents):
        try:
            if (parent / relative).exists():
                return parent
        except Exception:
            continue
    return None


def _is_within_git_repo(path):
    return _find_upward(path, ".git") is not None


def _has_graphify_graph(path):
    return _find_upward(path, "graphify-out/graph.json") is not None


def _contains_multiple_repos(path):
    """True if `path` has two or more directories directly beneath it that
    are themselves git repos or worktrees (e.g. ~/code)."""
    try:
        entries = os.listdir(path)
    except Exception:
        return False
    count = 0
    for name in entries:
        child = os.path.join(path, name)
        try:
            if not os.path.isdir(child):
                continue
            if os.path.exists(os.path.join(child, ".git")):
                count += 1
                if count >= 2:
                    return True
        except Exception:
            continue
    return False


def _scope_for_roots(roots):
    if not roots:
        return "unknown"
    home = os.path.normpath(os.environ.get("HOME") or str(Path.home()))
    for r in roots:
        rp = os.path.normpath(r)
        if rp == "/" or rp == home:
            return "disk_wide"
    for r in roots:
        if _contains_multiple_repos(r):
            return "disk_wide"
    for r in roots:
        if _is_within_git_repo(r):
            return "single_repo"
    return "single_dir"


def _positional_args(tokens, value_flags=None):
    """Split flags from positional arguments. `value_flags` is a set of
    flags (short or long) that consume the following token."""
    value_flags = value_flags or _VALUE_FLAGS
    positionals = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok.startswith("-") and tok != "-":
            if tok in value_flags and "=" not in tok:
                i += 2
                continue
            i += 1
            continue
        positionals.append(tok)
        i += 1
    return positionals


def _has_flag(tokens, *names):
    for tok in tokens:
        if tok in names:
            return True
        # combined short flags, e.g. -rn contains -r
        if tok.startswith("-") and not tok.startswith("--") and len(tok) > 1:
            for name in names:
                if len(name) == 2 and name.startswith("-") and name[1] in tok[1:]:
                    return True
    return False


def _classify_find(args, cwd):
    roots = []
    for tok in args:
        if tok.startswith("-"):
            break
        roots.append(_expand(tok, cwd))
    if not roots:
        roots = [_expand(".", cwd)]
    return roots


def _classify_fd(args, cwd):
    positionals = _positional_args(args)
    if len(positionals) >= 2:
        root = positionals[1]
    else:
        root = "."
    return [_expand(root, cwd)]


def _classify_grep_family(program, args, cwd):
    positionals = _positional_args(args)
    paths = positionals[1:] if positionals else []
    recursive = _has_flag(args, "-r", "-R", "--recursive") or program in _RECURSIVE_BY_DEFAULT

    if paths:
        return [_expand(p, cwd) for p in paths], None
    if recursive:
        return [_expand(".", cwd)], None
    return [], "stdin"


def _classify_dir_arg(args, cwd):
    positionals = _positional_args(args)
    if positionals:
        return [_expand(p, cwd) for p in positionals]
    return [_expand(".", cwd)]


_SCOPE_RANK = {"unknown": 0, "stdin": 1, "single_dir": 2, "single_repo": 2, "disk_wide": 3}
_LOCATE_FAMILY = ("locate", "plocate")


def _widest(current, candidate):
    """Keep the widest-scoped search program seen in a command.

    A pipeline such as `find ~ -iname x | grep -v node_modules` holds two search
    programs; the find is the one that walks the disk, so the later stdin grep
    must not mask it. On a tie, an indexed locate never displaces a walker.
    """
    cur_rank = _SCOPE_RANK.get(current.get("scope"), 0)
    new_rank = _SCOPE_RANK.get(candidate.get("scope"), 0)
    if new_rank > cur_rank:
        return candidate
    if new_rank == cur_rank and current.get("program") in _LOCATE_FAMILY \
            and candidate.get("program") not in _LOCATE_FAMILY:
        return candidate
    if current.get("program") is None:
        return candidate
    return current


def classify_command(command, cwd=None):
    """Classify the search scope of a shell command.

    Returns a dict: {"scope": ..., "program": str|None, "roots": [str]}.

    scope is one of: disk_wide, single_repo, single_dir, stdin, unknown.
    """
    if not command or not isinstance(command, str):
        return {"scope": "unknown", "program": None, "roots": []}

    try:
        statements = _split_top_level(command, _SEQUENTIAL_OPS)
    except Exception:
        return {"scope": "unknown", "program": None, "roots": []}

    current_cwd = cwd or ""
    last = {"scope": "unknown", "program": None, "roots": []}
    found_any = False

    for statement in statements:
        statement = statement.strip()
        if not statement:
            continue

        new_cwd = _try_parse_cd(statement, current_cwd)
        if new_cwd is not None:
            current_cwd = new_cwd
            continue

        try:
            stages = _split_top_level(statement, _PIPE_OP)
        except Exception:
            continue
        stages = [s.strip() for s in stages if s.strip()]

        for stage in stages:
            tokens = _safe_shlex(stage)
            if not tokens:
                continue
            tokens = _strip_prefixes(tokens)
            if not tokens:
                continue

            program = Path(tokens[0]).name
            if program not in SEARCH_PROGRAMS:
                continue

            args = tokens[1:]
            roots = None
            forced_scope = None

            if program == "find":
                roots = _classify_find(args, current_cwd)
            elif program in ("fd", "fdfind"):
                roots = _classify_fd(args, current_cwd)
            elif program in _GREP_FAMILY or program in ("rg", "ag", "ack"):
                roots, forced_scope = _classify_grep_family(program, args, current_cwd)
            elif program == "tree":
                roots = _classify_dir_arg(args, current_cwd)
            elif program == "ls":
                if not _has_flag(args, "-R", "--recursive"):
                    continue
                roots = _classify_dir_arg(args, current_cwd)
            elif program == "du":
                roots = _classify_dir_arg(args, current_cwd)
            elif program in ("locate", "plocate"):
                found_any = True
                last = _widest(last, {"scope": "disk_wide", "program": program, "roots": []})
                continue
            else:
                continue

            found_any = True
            if forced_scope == "stdin":
                candidate = {"scope": "stdin", "program": program, "roots": []}
            else:
                candidate = {
                    "scope": _scope_for_roots(roots),
                    "program": program,
                    "roots": roots,
                }
            last = _widest(last, candidate)

    if not found_any:
        return {"scope": "unknown", "program": None, "roots": []}
    return last


def root_has_graphify_graph(result):
    """Given a classify_command() result, does any of its roots sit inside a
    directory that already has a graphify graph?"""
    for root in result.get("roots") or []:
        if _has_graphify_graph(root):
            return True
    return False
