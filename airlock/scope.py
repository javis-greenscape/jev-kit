"""Decide search SCOPE in code, never by asking Jev.

Whether a search command is disk-wide, confined to one repo, or just one
directory is a fact readable straight off the command line and the working
directory -- it needs no model judgement. This module extracts the search
root(s) for a shell command and classifies scope purely from the filesystem.

Jev is left to answer only the fuzzy part: what KIND of search this is
(filename / code-structure / literal-text / not-a-search), handled in
questions.py and policy.py.

Windows
-------
On native Windows the same question has a second set of answers, because the
shell is a different shell. Claude Code's own setup documentation is explicit
about which: "Git for Windows is recommended on native Windows so Claude Code
can use the Bash tool. If Git for Windows is not installed, Claude Code uses
PowerShell as the shell tool instead." So BOTH are live -- a Bash tool call
carrying Git Bash syntax and MSYS paths (`/c/Users/...`), and a PowerShell
tool call carrying `Get-ChildItem -Recurse` and `C:\\Users\\...`. The hooks
reference adds that a hook inspecting shell commands must "Match
`Bash|PowerShell`", because on a machine without Git Bash the Bash tool is
never registered at all.

The Windows shapes are recognised ONLY when `windows` is true, which defaults
to `sys.platform`. That is deliberate: `dir`, `where` and `ls` all mean
something different on Linux, and Linux classification has to stay byte for
byte what it was. Every Windows test in this repository injects
`windows=True` instead of needing a Windows machine.
"""
import os
import re
import shlex
from pathlib import Path

from . import winpath
from .platform_compat import is_windows

# A WSL drive-root mount (`/mnt/c`, `/mnt/d/`), the counterpart of a native
# Windows drive root (`C:\`): the whole volume, not a directory within it.
_WSL_DRIVE_ROOT_RE = re.compile(r"^/mnt/[A-Za-z]$")


def _is_wsl():
    try:
        from . import headless
        return headless.is_wsl()
    except Exception:
        return False

# Programs this module knows how to extract a root from.
SEARCH_PROGRAMS = {
    "find", "fd", "fdfind",
    "grep", "egrep", "fgrep", "rg", "ag", "ack",
    "tree", "ls", "du",
    "locate", "plocate",
}

# Search shapes that exist only on Windows. Recognised in addition to the set
# above, never instead of it: Git Bash means a Windows session still issues
# ordinary `find` and `grep`.
#
#   dir /s                 the cmd.exe recursive directory walk
#   where /r <dir> <pat>   cmd.exe's filename search under a directory
#   findstr /s             cmd.exe's recursive content search
#   Get-ChildItem -Recurse PowerShell's walk (aliases gci, ls, dir)
#   Select-String          PowerShell's content search
#   es / es.exe            voidtools Everything's command-line client: the
#                          INDEXED tool, the Windows counterpart of plocate
WINDOWS_SEARCH_PROGRAMS = {
    "dir", "where", "findstr",
    "get-childitem", "gci", "childitem",
    "select-string", "sls",
    "es",
}

# Shells a command can be wrapped in. The inner command is re-classified, so
# `cmd.exe /c "dir /s C:\\"` scopes exactly as a bare `dir /s C:\\` would.
WINDOWS_SHELL_WRAPPERS = {"cmd", "powershell", "pwsh"}

# cmd.exe/PowerShell flags that introduce the wrapped command. Everything
# before one of these is the wrapper's own configuration and is dropped.
_WIN_SHELL_COMMAND_FLAGS = {"/c", "/k", "/r", "-command", "-c", "-encodedcommand"}
# Wrapper flags that take no value and can simply be skipped.
_WIN_SHELL_BARE_FLAGS = {"-noprofile", "-nologo", "-noninteractive", "-nop", "-mta", "-sta"}
# Wrapper flags that consume the token after them.
_WIN_SHELL_VALUE_FLAGS = {"-executionpolicy", "-ep", "-version", "-inputformat",
                          "-outputformat", "-windowstyle", "-file"}

# PowerShell parameters that consume the following token, so a positional
# search for the root never mistakes a filter pattern for a directory.
_PS_VALUE_FLAGS = {
    "-path", "-literalpath", "-filter", "-include", "-exclude", "-depth",
    "-erroraction", "-pattern", "-simplematch", "-context", "-encoding",
}
_PS_RECURSE_FLAGS = {"-recurse", "-r", "-rec"}

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


def _safe_shlex(s, windows=False):
    """Tokenise one command stage.

    On Windows the backslash is a PATH SEPARATOR, not an escape character, so
    the POSIX lexer would quietly eat it: `shlex.split(r"dir /s C:\\")`
    returns `["dir", "/s", "C:"]` and every drive root would be misread as a
    relative path. Turning the lexer's escape character off keeps quote
    handling (and quote stripping) while leaving backslashes alone. The Linux
    call is the original `shlex.split`, untouched.
    """
    try:
        if windows:
            lex = shlex.shlex(s, posix=True)
            lex.whitespace_split = True
            lex.escape = ""
            lex.commenters = ""
            return list(lex)
        return shlex.split(s)
    except ValueError:
        return None


def _expand(p, cwd, windows=False):
    """Expand $HOME/${HOME}/~ and resolve a relative path against cwd.

    On Windows this also expands `%USERPROFILE%`-style and `$env:VAR`-style
    variables and canonicalises the result (uppercase drive, backslashes),
    so `/c/Users/alice`, `C:/Users/Alice` and `C:\\users\\alice` all reduce to
    one string that compares equal.
    """
    if windows:
        return _expand_windows(p, cwd)
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


def _expand_windows(p, cwd):
    """The Windows half of _expand. Never raises."""
    base = winpath.canonical(cwd) if cwd else ""
    home = winpath.home()
    if p is None:
        return base or home
    try:
        p = winpath.expand_vars(str(p))
    except Exception:
        p = str(p)
    if home:
        p = p.replace("${HOME}", home).replace("$HOME", home)
        if p in ("~", "~\\", "~/"):
            p = home
        elif p.startswith("~/") or p.startswith("~\\"):
            p = home + p[1:]
    c = winpath.canonical(p)
    if not c:
        return base or home
    if not winpath.is_absolute(c):
        if c in (".", ".\\"):
            return base or home
        if base:
            c = winpath.canonical(base + "\\" + c)
    return c


def _program_name(token, windows=False):
    """The bare program name from a command word.

    On Windows that means dropping both kinds of separator, dropping the
    `.exe`/`.cmd`/`.bat` suffix, and lower-casing, so `C:\\Windows\\System32\\
    findstr.exe`, `findstr.exe` and `FindStr` are one program. On Linux the
    behaviour is the original `Path(token).name`, unchanged.
    """
    if not windows:
        return Path(token).name
    name = str(token).replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
    lowered = name.lower()
    for suffix in (".exe", ".cmd", ".bat", ".com", ".ps1"):
        if lowered.endswith(suffix):
            lowered = lowered[:-len(suffix)]
            break
    return lowered


def _try_parse_cd(statement, cwd, windows=False):
    """If `statement` is a `cd [DIR]` command, return the resulting absolute
    path. Otherwise return None.

    On Windows `Set-Location` and its `sl` alias are the same statement, and a
    bare `cd` goes to the user profile rather than to $HOME."""
    tokens = _safe_shlex(statement.strip(), windows)
    if not tokens:
        return None
    head = tokens[0].lower() if windows else tokens[0]
    if head != "cd" and not (windows and head in ("set-location", "sl", "chdir")):
        return None
    if len(tokens) == 1:
        if windows:
            return winpath.home() or None
        return os.environ.get("HOME") or str(Path.home())
    return _expand(tokens[1], cwd, windows)


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


def _scope_for_roots(roots, windows=False):
    if not roots:
        return "unknown"
    if windows:
        return _scope_for_roots_windows(roots)
    home = os.path.normpath(os.environ.get("HOME") or str(Path.home()))
    wsl = _is_wsl()
    for r in roots:
        rp = os.path.normpath(r)
        if rp == "/" or rp == home:
            return "disk_wide"
        # Under WSL a drive-root mount (`/mnt/c`) IS the whole Windows C:
        # drive, the same disk-wide ground a native `C:\` root covers --
        # without this, `find /mnt/c -name x` classified single_dir and the
        # WSL-aware suggestion below it was never reached (Codex P1, PR #1).
        if wsl and _WSL_DRIVE_ROOT_RE.match(rp):
            return "disk_wide"
    for r in roots:
        if _contains_multiple_repos(r):
            return "disk_wide"
    for r in roots:
        if _is_within_git_repo(r):
            return "single_repo"
    return "single_dir"


def _scope_for_roots_windows(roots):
    """Windows scope, same three tiers, three differences.

    A drive root (`C:\\`) or a UNC share root is disk-wide: it is the whole
    volume, the direct counterpart of `find /`. The user profile is disk-wide
    for the same reason `$HOME` is on Linux. And every comparison is
    case-insensitive, because the filesystem is.
    """
    home = winpath.home()
    for r in roots:
        if winpath.is_drive_root(r):
            return "disk_wide"
        if home and winpath.same_path(r, home):
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


def _classify_find(args, cwd, windows=False):
    roots = []
    for tok in args:
        if tok.startswith("-"):
            break
        roots.append(_expand(tok, cwd, windows))
    if not roots:
        roots = [_expand(".", cwd, windows)]
    return roots


def _classify_fd(args, cwd, windows=False):
    positionals = _positional_args(args)
    if len(positionals) >= 2:
        root = positionals[1]
    else:
        root = "."
    return [_expand(root, cwd, windows)]


def _classify_grep_family(program, args, cwd, windows=False):
    positionals = _positional_args(args)
    paths = positionals[1:] if positionals else []
    recursive = _has_flag(args, "-r", "-R", "--recursive") or program in _RECURSIVE_BY_DEFAULT

    if paths:
        return [_expand(p, cwd, windows) for p in paths], None
    if recursive:
        return [_expand(".", cwd, windows)], None
    return [], "stdin"


def _classify_dir_arg(args, cwd, windows=False):
    positionals = _positional_args(args)
    if positionals:
        return [_expand(p, cwd, windows) for p in positionals]
    return [_expand(".", cwd, windows)]


# --- Windows search shapes ---------------------------------------------------

def _win_flag(tok):
    """`/S`, `-Recurse` and `--recurse` all normalise to a lower-case
    `-`-prefixed form so one table covers cmd.exe and PowerShell alike."""
    if not tok:
        return ""
    if tok.startswith("/"):
        return "-" + tok[1:].lower()
    if tok.startswith("--"):
        return "-" + tok[2:].lower()
    if tok.startswith("-"):
        return tok.lower()
    return ""


def _win_has_flag(args, *names):
    wanted = set(names)
    return any(_win_flag(a) in wanted for a in args)


def _win_positionals(args, value_flags=()):
    """Positional tokens, skipping flags in either cmd.exe or PowerShell
    spelling and the value of any flag that takes one."""
    out = []
    i = 0
    while i < len(args):
        flag = _win_flag(args[i])
        if flag:
            if flag in value_flags:
                i += 2
                continue
            i += 1
            continue
        out.append(args[i])
        i += 1
    return out


def _dir_part(spec):
    """The directory half of a file spec: `C:\\code\\*.py` -> `C:\\code`.
    A spec that is only a wildcard (`*.py`) has no directory half."""
    s = str(spec).replace("/", "\\")
    if "*" not in s and "?" not in s:
        return s
    head = s.rsplit("\\", 1)[0]
    if head == s or "*" in head or "?" in head:
        return ""
    return head


def _classify_cmd_dir(args, cwd):
    """`dir /s [path] [filespec]`. Without /s it walks one directory and is
    not a search at all, which the caller checks before getting here. Only
    the FIRST positional names a root; a trailing `*.xlsm` is a pattern."""
    for tok in _win_positionals(args):
        head = _dir_part(tok)
        if head:
            return [_expand(head, cwd, True)]
    return [_expand(".", cwd, True)]


def _classify_where(args, cwd):
    """`where /r <dir> <pattern>`. A bare `where foo` searches %PATH% only --
    a handful of directories, not a crawl -- so it is not treated as a
    search at all."""
    for i, tok in enumerate(args):
        if _win_flag(tok) == "-r" and i + 1 < len(args):
            return [_expand(args[i + 1], cwd, True)]
    return None


def _classify_findstr(args, cwd):
    """`findstr /s <pattern> <filespec>`: cmd.exe's recursive content search.
    The trailing token is a wildcard file spec (`*.py`), not a directory, so
    the root is the directory part of it when it has one and the working
    directory otherwise."""
    if not _win_has_flag(args, "-s"):
        return None
    positionals = _win_positionals(args)
    # positionals[0] is the pattern; anything after it is a file spec.
    for spec in positionals[1:]:
        head = _dir_part(spec)
        if head and head != spec:
            return [_expand(head, cwd, True)]
    return [_expand(".", cwd, True)]


def _classify_get_childitem(args, cwd):
    """`Get-ChildItem -Recurse [-Path] <dir>` and its aliases gci / ls / dir.
    Without -Recurse it lists one directory and is not a search."""
    if not _win_has_flag(args, *_PS_RECURSE_FLAGS):
        return None
    for i, tok in enumerate(args):
        if _win_flag(tok) in ("-path", "-literalpath") and i + 1 < len(args):
            return [_expand(args[i + 1], cwd, True)]
    for tok in _win_positionals(args, _PS_VALUE_FLAGS):
        head = _dir_part(tok)
        if head:
            return [_expand(head, cwd, True)]
    return [_expand(".", cwd, True)]


def _classify_select_string(args, cwd):
    """`Select-String -Path <glob>`: PowerShell's content search. Only the
    -Path form names a root; the pipeline form reads stdin."""
    for i, tok in enumerate(args):
        if _win_flag(tok) in ("-path", "-literalpath") and i + 1 < len(args):
            head = _dir_part(args[i + 1])
            return [_expand(head or ".", cwd, True)]
    return None


def _unwrap_windows_shell(tokens):
    """Given the tokens of `cmd.exe /c <command>` or
    `powershell -NoProfile -Command <command>`, return the inner command as
    one string, or None when there is no inner command (an interactive shell,
    or -EncodedCommand, which is base64 and deliberately not decoded here).
    """
    i = 0
    while i < len(tokens):
        flag = _win_flag(tokens[i])
        if not flag:
            break
        if flag == "-encodedcommand":
            return None
        if flag in _WIN_SHELL_COMMAND_FLAGS:
            rest = tokens[i + 1:]
            if not rest:
                return None
            if len(rest) == 1:
                return rest[0]
            return " ".join(rest)
        if flag in _WIN_SHELL_VALUE_FLAGS:
            i += 2
            continue
        if flag in _WIN_SHELL_BARE_FLAGS:
            i += 1
            continue
        i += 1
    return None


_SCOPE_RANK = {"unknown": 0, "stdin": 1, "single_dir": 2, "single_repo": 2, "disk_wide": 3}
# The already-indexed tools. On Linux that is plocate; on Windows it is
# voidtools Everything's `es`. A command that already uses one of them must
# never be displaced by a walker of the same scope, and must never be told to
# use the indexed tool it is already using.
_LOCATE_FAMILY = ("locate", "plocate", "es")


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


def classify_command(command, cwd=None, windows=None, _depth=0):
    """Classify the search scope of a shell command.

    Returns a dict: {"scope": ..., "program": str|None, "roots": [str]}.

    scope is one of: disk_wide, single_repo, single_dir, stdin, unknown.

    `windows` defaults to this machine's platform. Pass it explicitly to
    classify a Windows command line on Linux (which is what the Windows unit
    tests do) or a POSIX one on Windows. `_depth` is internal: a
    `cmd.exe /c ...` wrapper re-enters this function for the inner command
    and the counter stops that recursing without bound.
    """
    if windows is None:
        windows = is_windows()
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

        new_cwd = _try_parse_cd(statement, current_cwd, windows)
        if new_cwd is not None:
            current_cwd = new_cwd
            continue

        try:
            stages = _split_top_level(statement, _PIPE_OP)
        except Exception:
            continue
        stages = [s.strip() for s in stages if s.strip()]

        for stage in stages:
            tokens = _safe_shlex(stage, windows)
            if not tokens:
                continue
            tokens = _strip_prefixes(tokens)
            if not tokens:
                continue

            program = _program_name(tokens[0], windows)
            args = tokens[1:]

            # A Windows shell wrapper: re-classify what it was asked to run.
            if windows and _depth < 3 and program in WINDOWS_SHELL_WRAPPERS:
                inner = _unwrap_windows_shell(args)
                if inner:
                    nested = classify_command(inner, current_cwd, windows=True,
                                              _depth=_depth + 1)
                    if nested.get("program"):
                        found_any = True
                        last = _widest(last, nested)
                continue

            known = program in SEARCH_PROGRAMS or (
                windows and program in WINDOWS_SEARCH_PROGRAMS)
            if not known:
                continue

            roots = None
            forced_scope = None

            # Windows shapes first: on Windows `dir` and `ls` mean
            # Get-ChildItem, not the coreutils programs of the same name.
            if windows and program in ("get-childitem", "gci", "childitem"):
                roots = _classify_get_childitem(args, current_cwd)
                if roots is None:
                    continue
            elif windows and program in ("dir", "ls") and (
                    _win_has_flag(args, "-s") or _win_has_flag(args, *_PS_RECURSE_FLAGS)):
                if _win_has_flag(args, *_PS_RECURSE_FLAGS):
                    roots = _classify_get_childitem(args, current_cwd)
                else:
                    roots = _classify_cmd_dir(args, current_cwd)
                if roots is None:
                    continue
            elif windows and program == "where":
                roots = _classify_where(args, current_cwd)
                if roots is None:
                    continue
            elif windows and program == "findstr":
                roots = _classify_findstr(args, current_cwd)
                if roots is None:
                    continue
            elif windows and program in ("select-string", "sls"):
                roots = _classify_select_string(args, current_cwd)
                if roots is None:
                    forced_scope = "stdin"
                    roots = []
            elif windows and program == "es":
                # Everything's index. Disk-wide by default, because that is
                # exactly what it is for, and instant regardless.
                found_any = True
                last = _widest(last, {"scope": "disk_wide", "program": "es", "roots": []})
                continue
            elif program == "find":
                roots = _classify_find(args, current_cwd, windows)
            elif program in ("fd", "fdfind"):
                roots = _classify_fd(args, current_cwd, windows)
            elif program in _GREP_FAMILY or program in ("rg", "ag", "ack"):
                roots, forced_scope = _classify_grep_family(program, args, current_cwd, windows)
            elif program == "tree":
                roots = _classify_dir_arg(args, current_cwd, windows)
            elif program == "ls":
                if not _has_flag(args, "-R", "--recursive"):
                    continue
                roots = _classify_dir_arg(args, current_cwd, windows)
            elif program == "du":
                roots = _classify_dir_arg(args, current_cwd, windows)
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
                    "scope": _scope_for_roots(roots, windows),
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
