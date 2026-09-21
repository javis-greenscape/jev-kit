"""The rules table: one entry per kind of genuinely-wrong tool use.

This module has no network and no logging, and its only filesystem access is
the cheap upward walk the legacy tool-choice rule already did, one stat of
the key-file pointer for R1 (see `_pointer_secret_path_res`, which caches on
that stat), and one bounded read of a script a command names for R11 (see
`_pw_script_source`: one isfile, one size check, at most 256KB, and only when
a segment actually runs a file with a script extension). It answers, in microseconds, one question for a PreToolUse payload:

    could any rule possibly fire for this call?

A call that matches no rule costs a `shlex`-free string scan and nothing else:
no Jev request, no log row, no subprocess. Only when a code pre-filter matches
does airlock/enforce.py (or the shadow worker) go on to ask Jev the single
fuzzy question that rule needs -- and only for rules whose pre-filter says the
fuzzy part is actually in doubt.

Each rule carries:
  id          stable short id, used in ~/.config/airlock/rules.json
  tools       tool names it applies to ("*" for every tool)
  action      default action: "deny" | "warn" | "log" | "off"
  prefilter   ctx -> Match | None    (all code, no I/O)
  questions   (ctx, match) -> (state, questions) for the Jev call, or None
  deny_when   answers -> bool        (the fuzzy half of the rule)
  legacy      name of a pre-existing guard whose behaviour is reproduced
              unchanged (the two original guards, R8)
  fallback    True for the catch-all tier (R10): consulted ONLY when no other
              rule matched the call at all

Actions:
  deny  block the call with the suggestion text (enforce mode only)
  ask   hand the decision to the human (config only; degrades to deny when
        nobody is attending the session)
  warn  allow, and hand the advice back as hook output / a log row
  log   allow, log only, never surface anything
  off   the rule does not run at all. R6 ships this way on every platform:
        a headless machine turns it on in rules.json (install/install.sh
        writes that entry when it detects one)

Per-rule overrides live in ~/.config/airlock/rules.json, e.g.:

    {"R3-whole-test-suite": "off", "R7-destructive": "log"}

Policy source: the owner's standing CLAUDE.md for this box (the
instructions for this box). No rule here invents policy that file does not
contain, and no rule duplicates what the box's own local hooks already block
(graphify-grep-guard.py for recursive grep in a graphed repo,
webfetch-guard.py for WebFetch prompt rewriting).
"""
import json
import os
import re

from . import keyfile, paths
from .platform_compat import is_windows

HOME = os.path.expanduser("~")

# Every tool that carries a shell command string in tool_input.command.
# Claude Code's hooks reference is explicit: "Match `Bash|PowerShell` in hooks
# that inspect shell commands, so they cover both tools", because on Windows
# without Git Bash "the tool is enabled automatically and Claude Code doesn't
# register the Bash tool at all" -- a hook matching only Bash never fires
# there. On Linux nothing changes: a Bash payload is still a Bash payload.
SHELL_TOOLS = ("Bash", "PowerShell")
CONFIG_FILE = str(paths.config_file("rules.json"))
# "ask" sits between allow and deny: the human is asked rather than the call
# being blocked outright. It is a config-only action -- no rule ships with it
# as a default -- and in an unattended session it is reported as the deny it
# actually is (see airlock/enforce.py:effective_block_action).
VALID_ACTIONS = ("deny", "ask", "warn", "log", "off")


class Match(object):
    """A code pre-filter hit. `ask=True` means the rule still needs Jev for
    the fuzzy half; `ask=False` means the code decided on its own."""

    __slots__ = ("detail", "suggestion", "ask", "extra")

    def __init__(self, detail, suggestion="", ask=False, extra=None):
        self.detail = detail
        self.suggestion = suggestion
        self.ask = ask
        self.extra = extra or {}

    def as_dict(self):
        return {"detail": self.detail, "suggestion": self.suggestion, "ask": self.ask, "extra": self.extra}


class Rule(object):
    __slots__ = ("id", "tools", "action", "windows_action", "prefilter", "questions",
                 "deny_when", "legacy", "fallback", "why", "advise_on_error")

    def __init__(self, id, tools, action, prefilter=None, questions=None, deny_when=None,
                 legacy=None, fallback=False, why="", windows_action=None,
                 advise_on_error=False):
        self.id = id
        self.tools = tuple(tools)
        self.action = action
        # A different DEFAULT action on native Windows, or None for "the same
        # everywhere". No rule sets it today: R6 used to, and now defaults to
        # `off` on every platform instead, which made the special case
        # redundant. The mechanism is kept because the next rule that is
        # genuinely platform-shaped will want it, and because a rules.json
        # entry still overrides it on either platform -- the per-platform
        # value is a DEFAULT, never a ceiling.
        self.windows_action = windows_action
        self.prefilter = prefilter
        self.questions = questions
        self.deny_when = deny_when
        self.legacy = legacy
        # A fallback rule is only consulted when NOTHING else matched. It is
        # the catch-all tier, and running it alongside a specific rule would
        # mean paying twice to say the same thing.
        self.fallback = fallback
        # When the Jev half cannot be reached -- no key, no tokens, a timeout,
        # any error -- the call is allowed either way (the guard fails open
        # everywhere). This flag says the rule's suggestion is still worth
        # printing as advice on that path, because the advice is useful on its
        # own and does not depend on the judgement. Off for every other rule,
        # whose silence on an error is the established behaviour.
        self.advise_on_error = advise_on_error
        self.why = why

    def applies_to(self, tool_name):
        return "*" in self.tools or tool_name in self.tools


# --- shell helpers -----------------------------------------------------------

_SKIP_PREFIX = {"nice", "time", "command", "exec", "builtin", "stdbuf", "nohup", "ionice"}


_HEREDOC_RE = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_][A-Za-z_0-9]*)\1")


def strip_heredocs(command):
    """Drop heredoc BODIES from a command line, keeping the command that owns
    them. A heredoc body is data (a file being written, a prompt, a note), not
    something the shell executes, so words inside it must never trip a code
    rule. Seen live on 2026-09-19: a note containing the word for the
    privilege-elevation command was blocked as if it were that command.
    Never raises; on anything odd it returns the input unchanged."""
    try:
        if "<<" not in (command or ""):
            return command
        out = []
        pending = []
        for line in command.split("\n"):
            if pending:
                if line.strip() == pending[0]:
                    pending.pop(0)
                continue
            out.append(line)
            for m in _HEREDOC_RE.finditer(line):
                pending.append(m.group(2))
        return "\n".join(out)
    except Exception:
        return command


def split_segments(command):
    """Split a command line into pipeline/list segments on ; && || | and
    newlines, respecting single and double quotes. Cheap hand-rolled scan --
    shlex on an arbitrary command line can raise, and we must never raise."""
    segs = []
    buf = []
    quote = None
    i = 0
    n = len(command or "")
    while i < n:
        ch = command[i]
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in "'\"":
            quote = ch
            buf.append(ch)
            i += 1
            continue
        if ch == "\\" and i + 1 < n:
            buf.append(ch)
            buf.append(command[i + 1])
            i += 2
            continue
        if ch in ";\n":
            segs.append("".join(buf))
            buf = []
            i += 1
            continue
        if ch in "&|":
            segs.append("".join(buf))
            buf = []
            while i < n and command[i] in "&|":
                i += 1
            continue
        buf.append(ch)
        i += 1
    segs.append("".join(buf))
    return [s.strip() for s in segs if s.strip()]


_ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z_0-9]*=")


def words(segment):
    """Whitespace tokens of a segment, quotes stripped off the ends. Never
    raises (unlike shlex.split on an unbalanced quote)."""
    out = []
    for tok in (segment or "").split():
        if len(tok) >= 2 and tok[0] == tok[-1] and tok[0] in "'\"":
            tok = tok[1:-1]
        out.append(tok)
    return out


def program_of(segment):
    """First real command word of a segment, skipping leading VAR=val
    assignments and wrappers like nice/time. Returns (program, args)."""
    toks = words(segment)
    while toks:
        if _ASSIGN_RE.match(toks[0]):
            toks = toks[1:]
            continue
        if toks[0] in _SKIP_PREFIX:
            toks = toks[1:]
            # a wrapper's own flags (nice -n 10, stdbuf -oL) are not the program
            while toks and toks[0].startswith("-"):
                flag = toks[0]
                toks = toks[1:]
                if flag in ("-n", "-p", "-o", "-e", "-i") and toks and not toks[0].startswith("-"):
                    if toks[0].lstrip("+-").isdigit():
                        toks = toks[1:]
            continue
        break
    if not toks:
        return None, []
    prog = toks[0]
    base = prog.rsplit("/", 1)[-1]
    return base, toks[1:]


def _expand(path):
    p = path or ""
    p = p.replace("$HOME", HOME).replace("${HOME}", HOME)
    if p.startswith("~"):
        p = HOME + p[1:]
    return p


# --- R1: secret exposure -----------------------------------------------------

READERS = {
    "cat", "bat", "less", "more", "head", "tail", "nl", "od", "xxd", "strings",
    "grep", "egrep", "rg", "ag", "awk", "sed", "cut", "tac", "jq", "yq", "tee",
}

# The same job on Windows, in either shell: `type` in cmd, `Get-Content` and
# its aliases in PowerShell, `findstr` in both. Kept SEPARATE from READERS
# above, and matched on a normalised name, because PowerShell is
# case-insensitive, a command may be written with a `.exe` suffix, and a path
# there is `\`-separated -- none of which is true of the POSIX set, whose
# matching is deliberately left exactly as it was.
WINDOWS_READERS = {
    "type", "get-content", "gc", "select-string", "sls", "findstr",
}


def _windows_reader(prog):
    """True iff `prog` names a Windows or PowerShell command that would print
    a file's contents. Never raises."""
    try:
        base = prog.rsplit("\\", 1)[-1].rsplit("/", 1)[-1].lower()
    except Exception:
        return False
    for suffix in (".exe", ".com", ".cmd", ".bat", ".ps1"):
        if base.endswith(suffix):
            base = base[:-len(suffix)]
            break
    return base in WINDOWS_READERS

# Hard-known secret stores, plus the generic shapes CLAUDE.md names ("`.env`
# files, private keys, `credentials*`, `*.key`, API tokens"). The key file is
# whichever path airlock/keyfile.py resolves to -- the kit default
# ~/.config/jev-kit/env, the guard-era default ~/.config/airlock/env, or an
# AIRLOCK_KEY_FILE / JEVKIT_KEY_FILE override. BOTH defaults are matched here,
# for good: an install whose key never moved is protected exactly as well as a
# fresh one. A machine that keeps its key somewhere else adds that path to
# AIRLOCK_EXTRA_SECRET_PATHS in install/config.env and it is matched here too,
# so R1 protects it without this file naming anybody's directory layout.
SECRET_PATH_RES = [
    # Both defaults, on both platforms and in either spelling. On POSIX the
    # pair is ~/.config/jev-kit/env and ~/.config/airlock/env; on Windows it
    # is %APPDATA%\jev-kit\env and %APPDATA%\airlock\env, which a command may
    # write with backslashes (cmd, PowerShell) or forward slashes (Git Bash).
    # One separator-tolerant, case-insensitive pattern each covers all four,
    # so `type %APPDATA%\airlock\env` and `Get-Content ...\jev-kit\env` are
    # protected exactly as well as `cat ~/.config/airlock/env`.
    re.compile(r"[\\/]jev-kit[\\/]env\b", re.I),
    re.compile(r"[\\/]airlock[\\/]env\b", re.I),
    re.compile(r"\.credentials\.json\b"),
    re.compile(r"\bcredentials(\.json|\.yml|\.yaml|\.ini)?\b(?!\.example)"),
    re.compile(r"(^|/)\.env(\.[A-Za-z0-9_-]+)?$"),
    re.compile(r"(^|/)\.env(\.[A-Za-z0-9_-]+)?(\s|$)"),
    re.compile(r"(^|/)id_(rsa|dsa|ecdsa|ed25519)$"),
    re.compile(r"\.(pem|key|p12|pfx|jks)$"),
    re.compile(r"(^|/)\.netrc$"),
    re.compile(r"(^|/)\.pgpass$"),
    re.compile(r"(^|/)\.npmrc$"),
    re.compile(r"(^|/)\.credentials\b"),
]

# Extra key-file paths this machine wants R1 to protect, colon-separated, from
# AIRLOCK_EXTRA_SECRET_PATHS in install/config.env. Each is matched literally,
# with a leading ~ or $HOME stripped so it matches however it is written in a
# command. Nothing is baked in: on a machine that sets nothing, this is empty.
def _extra_secret_path_res():
    try:
        raw = os.environ.get("AIRLOCK_EXTRA_SECRET_PATHS", "")
    except Exception:
        return []
    out = []
    for part in raw.split(os.pathsep):
        part = part.strip()
        if not part:
            continue
        for prefix in ("~/", "$HOME/", "${HOME}/"):
            if part.startswith(prefix):
                part = part[len(prefix):]
                break
        if not part:
            continue
        try:
            out.append(re.compile(re.escape(part) + r"\b"))
        except Exception:
            continue
    return out


SECRET_PATH_RES.extend(_extra_secret_path_res())


# The key-file POINTER, and whatever file it names, are both protected paths.
#
# The pointer (`$AIRLOCK_CONFIG_DIR/keyfile.path`) holds only a path, never a
# key -- but printing it tells a transcript exactly which file on this machine
# to go and read next, and it is written by install/install.sh AFTER a release
# is deployed, so a table built once at deploy time would miss it. Both are
# therefore resolved at HOOK time, and the pointer's target is read with
# `check=False`: a pointer whose permissions mean keyfile.py refuses to FOLLOW
# it still names a file that must not land in a transcript.
#
# Cost is one lstat of a small file per judged call, cached on that stat, so a
# pointer written or repointed between two tool calls is picked up on the next
# one without re-reading the file every time.
_POINTER_CACHE = {"stamp": None, "res": ()}


def _literal_path_re(path, windows=None):
    """A regex matching `path` as it could appear in a command: the absolute
    form, and the home-relative tail (`_expand` has already turned `~` and
    `$HOME` into HOME, so the tail alone covers both).

    Case-insensitive on Windows, where `%APPDATA%\\Airlock\\env` and
    `%appdata%\\airlock\\env` are the same file and R1 must not be fooled by
    which one a command happened to type."""
    flags = re.I if is_windows(windows) else 0
    out = []
    for form in (path, path[len(HOME) + 1:] if path.startswith(HOME + "/") else None):
        if not form:
            continue
        try:
            out.append(re.compile(re.escape(form) + r"(\b|$)", flags))
        except Exception:
            continue
    return out


def _pointer_secret_path_res():
    """Protected-path regexes for the pointer file and its target, refreshed
    whenever the pointer's stat changes. Never raises: on any error R1 falls
    back to the static table, which is the fail-open direction."""
    try:
        pointer = keyfile.pointer_file_path()
        if not pointer:
            return ()
        try:
            st = os.stat(pointer)
            stamp = (pointer, st.st_mtime_ns, st.st_size, st.st_ino)
        except Exception:
            stamp = (pointer, None, None, None)
        if _POINTER_CACHE["stamp"] == stamp:
            return _POINTER_CACHE["res"]
        res = list(_literal_path_re(pointer))
        target = keyfile.pointer_target(check=False)
        if target:
            res.extend(_literal_path_re(target))
        res = tuple(res)
        _POINTER_CACHE["stamp"] = stamp
        _POINTER_CACHE["res"] = res
        return res
    except Exception:
        return ()


def secret_path_res():
    """Every protected-path regex R1 should test: the static table, plus the
    pointer pair resolved now. Use this, never SECRET_PATH_RES directly."""
    return tuple(SECRET_PATH_RES) + _pointer_secret_path_res()

# Paths that look secret-ish but are fine, so the code pre-filter must not hit.
SAFE_PATH_RES = [
    re.compile(r"\.(example|sample|template|dist|md|rst|txt\.example)$"),
    re.compile(r"\.env\.(example|sample|template)\b"),
    re.compile(r"(^|/)env\.example\b"),
    re.compile(r"\.pub$"),
]

# Ambiguous: a reader aimed at something that MIGHT hold a secret. Code cannot
# tell; Jev is asked, and only for these.
AMBIGUOUS_SECRET_TOKENS = (
    "secret", "secrets", "token", "password", "passwd", "credential",
    "keyring", "keystore", "htpasswd", "vault", "apikey", "api_key",
)

SECRET_VAR_RE = re.compile(r"\$\{?([A-Za-z_][A-Za-z_0-9]*)\}?")
SECRET_VAR_NAME_RE = re.compile(r"(SECRET|TOKEN|PASSWORD|PASSWD|API_?KEY|PRIVATE_KEY|CREDENTIAL)", re.I)

R1_SUGGESTION_POSIX = (
    "Do not print a secret. Load it into the environment instead, in the same "
    "shell as the command that needs it:\n"
    "    set -a; . ~/.config/jev-kit/env; set +a\n"
    "and confirm it is present WITHOUT revealing it:\n"
    "    [ -n \"$TYPESAFE_API_KEY\" ] && echo 'key loaded'\n"
    "If output might contain a key, pipe it through:\n"
    "    sed 's/apikey_[A-Za-z0-9_]*/[REDACTED]/g'"
)

# The same advice in commands that exist on Windows. A deny that tells a
# Windows session to run `set -a; . ~/.config/jev-kit/env` is advice it cannot
# follow in either of its shells, so the platform picks the wording.
R1_SUGGESTION_WINDOWS = (
    "Do not print a secret. Load it into the environment instead, in the same "
    "shell as the command that needs it. In PowerShell, read it WITHOUT a "
    "reader cmdlet, so nothing can reach the transcript:\n"
    "    [IO.File]::ReadAllLines(\"$env:APPDATA\\jev-kit\\env\") | "
    "ForEach-Object { $n,$v = $_ -split '=',2; Set-Item \"env:$n\" $v }\n"
    "and confirm it is present WITHOUT revealing it:\n"
    "    if ($env:TYPESAFE_API_KEY) { 'key loaded' }\n"
    "If output might contain a key, pipe it through:\n"
    "    ... | ForEach-Object { $_ -replace 'apikey_[A-Za-z0-9_]*','[REDACTED]' }\n"
    "cmd has no equivalent one-liner: use PowerShell for this, or have the "
    "human set the variable."
)

R1_SUGGESTION = R1_SUGGESTION_WINDOWS if is_windows() else R1_SUGGESTION_POSIX


def _is_safe_path(tok):
    return any(r.search(tok) for r in SAFE_PATH_RES)


def _secret_path_in(tokens):
    for tok in tokens:
        if tok.startswith("-"):
            continue
        if _is_safe_path(tok):
            continue
        p = _expand(tok)
        for r in secret_path_res():
            if r.search(p):
                return tok
    return None


def _ambiguous_path_in(tokens):
    for tok in tokens:
        if tok.startswith("-") or _is_safe_path(tok):
            continue
        low = tok.lower()
        if "/" not in low and "." not in low:
            continue
        if any(t in low for t in AMBIGUOUS_SECRET_TOKENS):
            return tok
    return None


def _quiet_grep(args):
    """grep -q / -c / -l never puts the matched line in the transcript."""
    for a in args:
        if a.startswith("-") and not a.startswith("--"):
            if any(c in a[1:] for c in "qcl"):
                return True
        if a in ("--quiet", "--silent", "--count", "--files-with-matches"):
            return True
    return False


def prefilter_secret(ctx):
    tool = ctx["tool_name"]

    if tool in ("Read", "NotebookRead"):
        fp = str((ctx["tool_input"] or {}).get("file_path") or "")
        if fp and not _is_safe_path(fp):
            p = _expand(fp)
            for r in secret_path_res():
                if r.search(p):
                    return Match(
                        "Read of a secret store (%s): its contents would land in the transcript" % fp,
                        R1_SUGGESTION,
                    )
            low = p.lower()
            if any(t in low for t in AMBIGUOUS_SECRET_TOKENS):
                return Match("Read of a path that may hold secrets (%s)" % fp, R1_SUGGESTION, ask=True,
                             extra={"target": fp, "kind": "read"})
        return None

    if tool not in SHELL_TOOLS:
        return None

    command = ctx["command"]
    if not command:
        return None

    for seg in ctx["segments"]:
        prog, args = program_of(seg)
        if prog is None:
            continue

        # a reader aimed at a known secret store
        if prog in READERS or _windows_reader(prog):
            if prog in ("grep", "egrep", "rg", "ag") and _quiet_grep(args):
                continue
            hit = _secret_path_in(args)
            if hit:
                return Match(
                    "`%s` on a secret store (%s) would print its contents" % (prog, hit),
                    R1_SUGGESTION,
                )
            amb = _ambiguous_path_in(args)
            if amb:
                return Match(
                    "`%s` on a path that may hold secrets (%s)" % (prog, amb),
                    R1_SUGGESTION, ask=True, extra={"target": amb, "kind": "reader"},
                )

        # echo/printf/printenv of a secret-named variable
        if prog in ("echo", "printf"):
            for name in SECRET_VAR_RE.findall(seg):
                if SECRET_VAR_NAME_RE.search(name):
                    return Match(
                        "`%s` would print $%s, whose name says it holds a secret" % (prog, name),
                        R1_SUGGESTION,
                    )
        if prog == "printenv":
            if not args:
                return Match("bare `printenv` dumps every variable, secrets included", R1_SUGGESTION)
            for a in args:
                if SECRET_VAR_NAME_RE.search(a):
                    return Match("`printenv %s` would print a secret value" % a, R1_SUGGESTION)

        # an unfiltered environment dump
        if prog in ("env", "set", "export", "declare") and not args:
            if prog in ("env", "set"):
                return Match("bare `%s` dumps every variable, secrets included" % prog, R1_SUGGESTION)
        if prog == "export" and args == ["-p"]:
            return Match("`export -p` dumps every exported variable, secrets included", R1_SUGGESTION)
        if prog == "declare" and args and args[0] in ("-x", "-p"):
            return Match("`declare %s` dumps every variable, secrets included" % args[0], R1_SUGGESTION)

        # curl -v with an Authorization header on the command line
        if prog in ("curl", "http", "wget"):
            verbose = any(a in ("-v", "--verbose", "--trace", "--trace-ascii", "-i", "--include") for a in args)
            has_auth = bool(re.search(r"authorization\s*:", seg, re.I))
            if verbose and has_auth:
                return Match(
                    "`%s` in verbose mode echoes the Authorization header, including the token" % prog,
                    "Drop -v/--verbose when a credential is on the command line, or move the token into a "
                    "variable and pipe the output through sed 's/apikey_[A-Za-z0-9_]*/[REDACTED]/g'.",
                )
    return None


def questions_secret(ctx, match):
    state = {
        "command": ctx["command"][:2000],
        "tool_name": ctx["tool_name"],
        "target": match.extra.get("target", ""),
    }
    qs = {
        "prints_a_secret": {
            "type": "choice",
            "instructions": {
                "question": (
                    "Would running this tool call put a SECRET VALUE (an API key, "
                    "token, password, private key, or credential) into the session "
                    "transcript, where it would be recorded?"
                ),
                "focus": (
                    "The test is whether a secret VALUE gets printed. Mentioning the "
                    "NAME of a secret variable, checking that one is set without "
                    "printing it, or reading an example/template file are all safe."
                ),
            },
            "criteria": {
                "yes": {
                    "what": "The output of this call would contain the secret value itself.",
                    "not_for": "A call that only names a variable, counts matches, or tests emptiness.",
                    "examples": [
                        "cat ~/.config/jev-kit/env",
                        "echo $TYPESAFE_API_KEY",
                        "grep -n API ~/secrets/prod.env",
                    ],
                },
                "no": {
                    "what": "No secret value would be printed.",
                    "not_for": "A call that does print the value, even incidentally.",
                    "examples": [
                        "grep -rn TOKEN_NAME src/config.py",
                        "[ -n \"$GS_JIRA_API_TOKEN\" ] && echo set",
                        "cat .env.example",
                    ],
                },
                "unclear": {
                    "what": "Not enough information to tell.",
                    "not_for": "Use only when truly stuck.",
                    "examples": [],
                },
            },
        }
    }
    return state, qs


def deny_secret(answers):
    a = (answers or {}).get("prints_a_secret") or {}
    return (a.get("choice") or "") == "yes"


# --- R2: the claude-api skill ------------------------------------------------

R2_SUGGESTION = (
    "Do not load the `claude-api` skill for a price or model-id lookup: it is "
    "all-or-nothing and one load measured 324,006 input tokens. Read your local "
    "note of the model ids and rates instead, and never compute a cost by "
    "multiplying tokens by a rate -- if the number is not already recorded, say so."
)


def _skill_name(ctx):
    ti = ctx["tool_input"] or {}
    return str(ti.get("skill") or ti.get("name") or "").strip().lstrip("/")


def prefilter_claude_api(ctx):
    if ctx["tool_name"] != "Skill":
        return None
    if _skill_name(ctx) != "claude-api":
        return None
    purpose = str((ctx["tool_input"] or {}).get("args") or "").strip()
    if purpose:
        return Match("Skill(claude-api) with a stated purpose", R2_SUGGESTION, ask=True,
                     extra={"purpose": purpose})
    # No purpose in the payload: nothing to judge, so this can only warn.
    return Match("Skill(claude-api) with no stated purpose in the payload", R2_SUGGESTION,
                 extra={"downgrade_to": "warn"})


def questions_claude_api(ctx, match):
    state = {"skill": "claude-api", "args": match.extra.get("purpose", "")[:2000]}
    qs = {
        "purpose": {
            "type": "choice",
            "instructions": {
                "question": (
                    "A session is about to load a large API reference skill. From the "
                    "arguments given, what is it being loaded FOR?"
                ),
                "focus": "Distinguish a one-fact lookup from genuine API depth.",
            },
            "criteria": {
                "price_or_model_id_lookup": {
                    "what": "Looking up a price, a rate, a token count, a cost, or a model id/name.",
                    "not_for": "Work that needs the API's semantics rather than one of its numbers.",
                    "examples": [
                        "what did that run cost",
                        "which model id is Sonnet 4.5",
                        "per-token price for Opus",
                    ],
                },
                "api_depth": {
                    "what": "Migration, tool-use semantics, streaming, caching behaviour, SDK usage.",
                    "not_for": "A single price or model id.",
                    "examples": [
                        "migrate this client to the new tool-use format",
                        "how does prompt caching interact with streaming",
                    ],
                },
                "unclear": {
                    "what": "Not enough information to tell.",
                    "not_for": "Use only when truly stuck.",
                    "examples": [],
                },
            },
        }
    }
    return state, qs


def deny_claude_api(answers):
    a = (answers or {}).get("purpose") or {}
    return (a.get("choice") or "") == "price_or_model_id_lookup"


# --- R3: whole test suite / uncapped parallel build ---------------------------

_PYTEST_SELECTOR_FLAGS = ("-k", "-m", "--lf", "--last-failed", "--ff", "--co", "--collect-only")
_JS_RUNNERS = {"vitest", "jest", "mocha", "ava"}


def _has_path_arg(args, exts=(".py", ".js", ".ts", ".tsx", ".jsx", ".mjs")):
    for a in args:
        if a.startswith("-"):
            continue
        if "::" in a or a.endswith(exts) or "/" in a:
            return True
    return False


def prefilter_wide_run(ctx):
    if ctx["tool_name"] not in SHELL_TOOLS:
        return None
    for seg in ctx["segments"]:
        prog, args = program_of(seg)
        if prog is None:
            continue

        if prog in ("python", "python3") and args[:2] == ["-m", "pytest"]:
            prog, args = "pytest", args[2:]
        if prog == "uv" and args[:2] == ["run", "pytest"]:
            prog, args = "pytest", args[2:]

        if prog == "pytest":
            if _has_path_arg(args) or any(a in _PYTEST_SELECTOR_FLAGS for a in args):
                continue
            capped = any(a.startswith("-n") or a.startswith("--numprocesses") for a in args)
            return Match(
                "`pytest` with no path or selector runs the WHOLE suite on a 4-vCPU box",
                "Run only what the change touched, plus a whole-project typecheck:\n"
                "    pytest tests/test_<thing>.py -x -q%s" % ("" if capped else "\nand cap parallelism: -n2"),
            )

        if prog in _JS_RUNNERS or (prog in ("npm", "pnpm", "yarn", "npx") and args[:1] in (["test"], ["run"])):
            joined = " ".join(args)
            if prog in ("npm", "pnpm", "yarn") and args[:1] == ["run"] and args[1:2] != ["test"]:
                continue
            if prog == "npx" and not (args[1:2] and args[1] in _JS_RUNNERS or args[:1] and args[0] in _JS_RUNNERS):
                continue
            if _has_path_arg(args):
                continue
            if "--maxWorkers" in joined or "--max-workers" in joined or "--pool" in joined or "--threads" in joined:
                continue
            return Match(
                "`%s %s` runs the whole test suite with uncapped workers (4 shared vCPUs, 8GB RAM)"
                % (prog, joined.strip()),
                "Name the test file, and cap workers:\n"
                "    %s %s -- <path/to/test> --maxWorkers=2" % (prog, (args[0] if args else "test")),
            )

        if prog == "make":
            for a in args:
                if a == "-j" or (a.startswith("-j") and not a[2:].isdigit()):
                    return Match(
                        "`make -j` with no number takes every core on a 4-vCPU shared box",
                        "Cap it explicitly: make -j2 (and `nice -n 10 make -j2` for a long build).",
                    )

        if prog == "cargo" and args[:1] and args[0] in ("build", "test", "check", "clippy"):
            if not any(a == "-j" or a.startswith("-j") or a.startswith("--jobs") for a in args):
                return Match(
                    "`cargo %s` with no -j uses every core on a 4-vCPU shared box" % args[0],
                    "Cap it explicitly: cargo %s -j2" % args[0],
                )
    return None


# --- R4: long work on a bare shell -------------------------------------------

R4_SUGGESTION = (
    "This box is reached only over SSH, so a dropped connection kills anything "
    "attached to the shell. Run it in the named tmux session with stdin closed:\n"
    "    tmux new-session -d -s work 'cd <dir> && <command> </dev/null "
    ">>~/logs/work.log 2>&1'\n"
    "then poll with `tmux has-session -t work` / tail the log."
)

# Unambiguously slow: no need to ask anything.
_SLOW_CERTAIN = (
    ("playwright", "install"),
    ("uv", "sync"),
    ("pnpm", "install"),
    ("npm", "install"),
    ("npm", "ci"),
    ("yarn", "install"),
    ("docker", "build"),
    ("docker", "pull"),
    ("apt", "install"),
    ("apt-get", "install"),
    ("apt", "upgrade"),
    ("apt-get", "upgrade"),
)


def _in_tmux(command, ctx):
    if "tmux " in command or command.strip().startswith("tmux"):
        return True
    if (ctx["tool_input"] or {}).get("run_in_background"):
        return True
    return False


def prefilter_long_run(ctx):
    if ctx["tool_name"] not in SHELL_TOOLS:
        return None
    command = ctx["command"]
    if not command or _in_tmux(command, ctx):
        return None

    for seg in ctx["segments"]:
        prog, args = program_of(seg)
        if prog is None:
            continue
        if any(a in ("--help", "-h", "--version", "--dry-run", "-n") for a in args):
            continue
        sub = args[0] if args else ""

        if prog == "sudo" and args:
            prog, args = args[0], args[1:]
            sub = args[0] if args else ""

        for p, s in _SLOW_CERTAIN:
            if prog == p and sub == s:
                return Match("`%s %s` routinely runs for minutes" % (p, s), R4_SUGGESTION)
        if prog == "npx" and args[:2] == ["playwright", "install"]:
            return Match("`npx playwright install` routinely runs for minutes", R4_SUGGESTION)

        ambiguous = (
            (prog in ("pip", "pip3") and sub == "install")
            or (prog in ("python", "python3") and args[:3] == ["-m", "pip", "install"])
            or (prog == "uv" and sub == "pip")
            or (prog == "git" and sub == "clone")
            or (prog == "cargo" and sub in ("build", "install"))
            or (prog == "make" and not args)
            or (prog in ("wget", "curl") and re.search(r"\.(tar\.gz|tgz|zip|iso|deb|whl|bin)\b", seg))
        )
        if ambiguous:
            return Match(
                "`%s %s` may run longer than two minutes on a bare SSH shell" % (prog, sub),
                R4_SUGGESTION, ask=True, extra={"segment": seg[:400]},
            )
    return None


def questions_long_run(ctx, match):
    state = {"command": ctx["command"][:2000], "segment": match.extra.get("segment", "")}
    qs = {
        "runs_over_two_minutes": {
            "type": "noul",
            "instructions": (
                "Is this shell command likely to run for longer than two minutes "
                "on a 4-vCPU cloud server with a normal home broadband-speed "
                "network link?"
            ),
            "criteria": {
                "true": (
                    "It installs or builds something substantial, downloads a large "
                    "artifact, or clones a large repository."
                ),
                "false": (
                    "It installs one small pure-Python package, clones a tiny repo, "
                    "is a no-op because everything is cached, or is a quick check."
                ),
            },
        }
    }
    return state, qs


def deny_long_run(answers):
    a = (answers or {}).get("runs_over_two_minutes") or {}
    return (a.get("noul") or 0.0) >= 0.5


# --- R5: sudo ----------------------------------------------------------------

_PKG_INSTALLERS = {"apt", "apt-get", "dpkg", "snap", "aptitude"}
_PKG_SUBS = {"install", "reinstall", "-i", "--install"}

R5_SUGGESTION = (
    "`sudo` is passwordless here, which is capability, not authorisation. It is "
    "for installing a named system package someone asked for, and never for "
    "anything under $HOME -- root-owned files there break the next ordinary run. "
    "/etc, /usr, /opt, systemd units, the firewall and SSH config are out of scope."
)


def prefilter_sudo(ctx):
    if ctx["tool_name"] not in SHELL_TOOLS:
        return None
    for seg in ctx["segments"]:
        toks = words(seg)
        if not toks:
            continue
        # Command position only: a quoted argument or a search pattern that
        # merely contains the word is not an invocation.
        prog, _args = program_of(seg)
        if prog != "sudo":
            continue
        idx = None
        for i, t in enumerate(toks):
            if t.rsplit("/", 1)[-1] == "sudo":
                idx = i
                break
        if idx is None:
            continue
        rest = [t for t in toks[idx + 1:] if not t.startswith("-") or t in _PKG_SUBS]
        flags = toks[idx + 1:]

        # any sudo touching a path under $HOME
        for t in flags:
            p = _expand(t)
            if p.startswith(HOME + "/") or p == HOME or t.startswith("~"):
                return Match(
                    "`sudo` on a path under $HOME (%s) leaves root-owned files behind" % t,
                    R5_SUGGESTION,
                )

        prog = rest[0].rsplit("/", 1)[-1] if rest else ""
        if prog in _PKG_INSTALLERS:
            subs = [t for t in rest[1:]]
            if subs and subs[0] in _PKG_SUBS and len(subs) > 1:
                continue  # named package install: allowed
        return Match(
            "`sudo %s` is not a named system package install" % (" ".join(rest[:3]) or "<nothing>"),
            R5_SUGGESTION,
        )
    return None


# --- R6: GUI / browser on a headless box -------------------------------------

_GUI_PROGRAMS = {
    "xdg-open", "open", "sensible-browser", "gnome-open", "kde-open", "x-www-browser",
    "www-browser", "firefox", "google-chrome", "chrome", "chromium", "chromium-browser",
    "wslview", "explorer.exe", "eog", "xdg-mime", "nautilus", "gio",
}

R6_SUGGESTION = (
    "There is no desktop on this box: no GUI, no browser, no X server, $DISPLAY "
    "unset. Print the URL or path and let the user open it on their own machine. "
    "If the task genuinely needs a browser, use Playwright headless (Chromium only)."
)

# The same advice for a machine that is Windows AND headless -- a Server Core
# box, a build agent. It never mentions $DISPLAY or an X server, because
# neither exists there. R6 is off by default on every platform, so reaching
# this text at all means rules.json on this machine turned it on, which is
# somebody saying the box has no desktop.
R6_SUGGESTION_WINDOWS = (
    "This machine is configured as one that should not open a GUI or a browser "
    "(R6 is off by default; rules.json here turns it on). Print the URL or path "
    "and let the user open it themselves. If the task genuinely needs a browser, "
    "use Playwright headless."
)


def prefilter_gui(ctx, windows=None):
    if ctx["tool_name"] not in SHELL_TOOLS:
        return None
    win = is_windows(windows)
    for seg in ctx["segments"]:
        prog, args = program_of(seg)
        if prog in _GUI_PROGRAMS:
            if prog == "gio" and args[:1] != ["open"]:
                continue
            if prog in ("chromium", "google-chrome", "chrome") and any(
                a.startswith("--headless") for a in args
            ):
                continue
            if win:
                return Match("`%s` tries to open a GUI or browser" % prog,
                             R6_SUGGESTION_WINDOWS)
            return Match("`%s` tries to open a GUI or browser on a headless server" % prog, R6_SUGGESTION)
    return None


# --- R7: destructive / outward-facing ----------------------------------------

R7_SUGGESTION = (
    "Ask first for anything hard to reverse or outward-facing (rewriting history, "
    "force-pushing, deleting branches or files wholesale). Uncommitted work is work "
    "that can be lost: prefer a WIP commit to discarding, and `--force-with-lease` "
    "to a bare force push."
)


def prefilter_destructive(ctx):
    if ctx["tool_name"] not in SHELL_TOOLS:
        return None
    for seg in ctx["segments"]:
        prog, args = program_of(seg)
        if prog == "git" and args:
            sub = args[0]
            joined = " ".join(args)
            if sub == "push" and re.search(r"(^|\s)(--force|-f)(\s|$)", " " + joined):
                return Match("`git push --force` overwrites published history", R7_SUGGESTION)
            if sub == "reset" and "--hard" in args:
                return Match("`git reset --hard` discards uncommitted work irreversibly", R7_SUGGESTION)
            if sub == "branch" and any(a == "-D" or a == "--delete" and "--force" in args for a in args):
                return Match("`git branch -D` force-deletes a branch", R7_SUGGESTION)
            if sub == "clean" and any("f" in a and a.startswith("-") for a in args):
                return Match("`git clean -f` deletes untracked files irreversibly", R7_SUGGESTION)
            if sub in ("filter-branch", "filter-repo"):
                return Match("`git %s` rewrites history" % sub, R7_SUGGESTION)
        if prog == "rm":
            recursive = any(a.startswith("-") and ("r" in a.lower() or a in ("--recursive",)) for a in args)
            if not recursive:
                continue
            for a in args:
                if a.startswith("-"):
                    continue
                p = _expand(a).rstrip("/")
                if p in ("/", HOME) or a in ("*", "~", "$HOME"):
                    return Match("`rm -rf %s` would delete a whole tree" % a, R7_SUGGESTION)
                if p.startswith(HOME + "/") and os.path.exists(os.path.join(p, ".git")):
                    return Match("`rm -rf %s` would delete a whole git repository" % a, R7_SUGGESTION)
    return None


# --- R9: committing a secret -------------------------------------------------

R9_SUGGESTION = (
    "Never commit secrets: .env files, private keys, credentials*, *.key, tokens. "
    "Add the path to .gitignore instead. If one is ALREADY tracked, flag it to the "
    "owner rather than quietly rewriting history to hide it."
)


def prefilter_commit_secret(ctx):
    """Two belts, both pure code, both offline.

    1. A secret PATH being staged (`git add .env`, `git add server.key`).
    2. A credential-shaped LITERAL in the command text itself -- a key pasted
       into a commit message, a `printf '...' > .env && git add .env`, a token
       in a `git commit -m`. The patterns come from jev-commit's local belt
       (airlock/belt.py), high-precision tier only, with its placeholder
       suppression intact so `sk-your_key_here` and a line mentioning
       "example" do not fire.

    NOTHING here reads or sends a diff. jev-commit asks Jev about the staged
    hunks, which is inherent to the question it asks and is exactly what this
    rule does not do. The whole check is regexes over a string the hook was
    handed anyway, and only the first four characters of a match are ever
    recorded.
    """
    if ctx["tool_name"] not in SHELL_TOOLS:
        return None

    command = ctx.get("command") or ""
    git_write = False

    for seg in ctx["segments"]:
        prog, args = program_of(seg)
        if prog != "git" or not args:
            continue
        if args[0] not in ("add", "commit", "stage"):
            continue
        git_write = True
        hit = _secret_path_in(args[1:])
        if hit:
            return Match("`git %s` would stage a secret file (%s)" % (args[0], hit), R9_SUGGESTION)

    if not git_write:
        return None

    try:
        from . import belt
        credential = belt.first_blocking_hit(command)
    except Exception:
        credential = None
    if credential:
        return Match(
            "this command stages or commits something carrying a credential "
            "(%s, starts `%s`)" % (credential["kind"], credential["redacted"]),
            R9_SUGGESTION,
        )
    return None


# --- R10: general risk, the catch-all ----------------------------------------
#
# Ported in spirit from leepokai/jev-guard's ACTION_QUESTIONS (`src/guard.js`,
# MIT, see docs/CREDITS.md), which asks a `risk` score on
# EVERY non-read-only tool call. Asking on every call is exactly the cost this
# project exists to avoid, so the port keeps the question and drops the
# blanket: R10 runs only when
#
#   (a) no other rule matched this call at all, and
#   (b) a pure code pre-filter marks the command as potentially consequential.
#
# Every named rule above encodes a specific policy from the box's CLAUDE.md.
# R10 is the residue: the commands that are plainly capable of doing something
# outside this working tree but that no specific rule covers. It is `warn` and
# ONLY warn -- it never denies, whatever the answer, because a catch-all
# heuristic denying things it cannot name is how a guard becomes something
# people turn off.

# `user_requested` at or above this suppresses the warn entirely. The same
# one-directional rule as everywhere else: it can only ever soften.
R10_USER_REQUESTED_SOFTEN_AT = 0.75

# The risk levels, lowest first. The Score answer is a float index into this
# list, so the ORDER is load-bearing and adding a level changes what the
# threshold below means.
R10_RISK_LEVELS = [
    "none: read-only, or fully reversible inside the working directory given in "
    "`cwd` or a temporary directory. Nothing outside this checkout is changed. "
    "Writing a file under cwd, running a container that only reads, listing a "
    "database.",
    "low: changes something outside the working directory, but it is recreatable "
    "in minutes with no outside consequence: a cache, a scratch container, a "
    "local index, a file the tool that wrote it would rebuild.",
    "moderate: changes durable local state that a person would have to restore "
    "deliberately: stopping or disabling a service somebody relies on, removing "
    "a container or volume holding data, writing over a config file outside the "
    "checkout, deleting files matched by a wildcard high in the tree.",
    "high: reaches off this machine, or is not undone by deleting what it made: "
    "uploading data to another host, publishing a package or a release, writing "
    "to a shared or production database, force-changing state other people "
    "depend on.",
]

# Fires at `moderate` (index 2) and above. The threshold sits at the midpoint
# between `low` and `moderate` rather than on the level itself, because the
# score is a continuous index and an answer leaning between two levels should
# be read the way it leans.
R10_FIRE_AT = 1.5

R10_SUGGESTION = (
    "This is not something any specific rule covers, so nothing is being blocked. "
    "It does look like it reaches outside this working tree. Worth a second's "
    "thought about whether it is reversible, and whether the person running this "
    "session actually asked for it."
)

_R10_PUBLISH = {
    ("npm", "publish"), ("pnpm", "publish"), ("yarn", "publish"),
    ("poetry", "publish"), ("uv", "publish"), ("cargo", "publish"),
    ("gem", "push"), ("twine", "upload"), ("docker", "push"),
    ("flyctl", "deploy"), ("fly", "deploy"), ("vercel", "deploy"),
    ("netlify", "deploy"),
}

_R10_DB_PROGRAMS = {"psql", "mysql", "mariadb", "sqlite3", "mongosh", "mongo",
                    "redis-cli", "clickhouse-client", "cqlsh", "sqlcmd", "duckdb"}
_R10_DB_WRITE_RE = re.compile(
    r"\b(insert\s+into|update\s+\w|delete\s+from|drop\s+(table|database|schema|index)"
    r"|truncate|alter\s+table|create\s+(table|database|schema)|grant\s|revoke\s"
    r"|flushall|flushdb|copy\s+\w+\s+from)\b"
    # the document-store and key-value shapes, which are not SQL
    r"|\.(drop|dropDatabase|deleteMany|deleteOne|insertMany|insertOne"
    r"|updateMany|updateOne|remove|renameCollection)\s*\(", re.I)

_R10_SERVICE_PROGRAMS = {"systemctl", "service", "launchctl", "initctl", "rc-service"}
_R10_SERVICE_VERBS = {"start", "stop", "restart", "reload", "enable", "disable",
                      "mask", "unmask", "daemon-reload", "kill", "load", "unload"}
_R10_DOCKER_VERBS = {"run", "rm", "rmi", "stop", "kill", "restart", "prune",
                     "system", "volume", "network", "swarm", "compose", "down", "up"}

# A grouped docker verb carries its real verb in the NEXT word: `docker system
# df`, `docker compose ps` and `docker volume ls` only read, and matching them
# on the group word alone is the shape that made R10 noisy.
_R10_DOCKER_GROUP_VERBS = {"system", "volume", "network", "swarm", "compose",
                           "image", "container", "builder", "context", "node",
                           "service", "stack"}
# Verbs that only read, at the top level or after a group word.
_R10_DOCKER_READ_VERBS = {"ps", "logs", "inspect", "images", "stats", "top",
                          "port", "version", "info", "diff", "history",
                          "events", "search", "ls", "config", "df"}

_R10_MASS_PROGRAMS = {"rm", "mv", "cp", "chmod", "chown", "chgrp", "truncate", "shred"}

# Directories a write is uninteresting in: anything temporary, plus the caches
# and stores every tool on this box writes to as a matter of course.
_R10_TEMP_PREFIXES = ("/tmp/", "/var/tmp/", "/dev/", "/proc/", "/sys/")
_R10_BORING_SUFFIXES = ("/.cache", "/.npm", "/.pnpm-store", "/.cargo", "/.local/state",
                        "/.venv", "/node_modules")

# scp/rsync's remote form: [user@]host:path, where the part before the colon
# carries no slash (so a local `./dir:name` and a `https://...` URL are not
# mistaken for a host).
_R10_REMOTE_RE = re.compile(r"^(?:[A-Za-z0-9._-]+@)?[A-Za-z0-9.-]+:")
_R10_LOCALHOST = ("localhost", "127.0.0.1", "::1", "0.0.0.0")


def _r10_is_temp(path):
    if not path:
        return True
    if path.startswith(_R10_TEMP_PREFIXES) or path in ("/tmp", "/var/tmp"):
        return True
    return False


def _r10_outside_cwd(path, cwd):
    """True when `path` is an absolute location that is neither inside the
    session's cwd nor somewhere uninteresting. A relative path is always
    treated as inside the cwd -- that is what a relative path means."""
    if not path or path.startswith("-"):
        return False
    p = _expand(path)
    if not p.startswith("/"):
        return False
    if _r10_is_temp(p):
        return False
    for boring in _R10_BORING_SUFFIXES:
        if boring + "/" in p or p.endswith(boring):
            return False
    if cwd and (p == cwd or p.startswith(cwd.rstrip("/") + "/")):
        return False
    return True


def _r10_redirect_targets(segment):
    """Absolute paths a segment redirects stdout/stderr into. Deliberately
    crude: a false positive here only costs one warn-only question."""
    out = []
    for m in re.finditer(r"(?<![0-9<>])>>?\s*([^\s;|&<>]+)", segment or ""):
        out.append(m.group(1).strip("'\""))
    return out


def _r10_is_remote_target(token):
    """`host:path` or `user@host:path`, the scp/rsync remote form, excluding
    anything pointing back at this machine."""
    if not token or token.startswith("-"):
        return False
    if "://" in token:
        return False
    head = token.split(":", 1)[0]
    if "/" in head or not _R10_REMOTE_RE.match(token):
        return False
    host = head.rsplit("@", 1)[-1]
    return host not in _R10_LOCALHOST


def _r10_url_is_remote(token):
    if not token.startswith(("http://", "https://", "ftp://", "ftps://", "sftp://")):
        return False
    rest = token.split("://", 1)[1]
    host = rest.split("/", 1)[0].split("@")[-1].split(":")[0]
    return host not in _R10_LOCALHOST


def _r10_glob_high_in_tree(token):
    """A wildcard at a shallow level of the tree: `~/*`, `/etc/*`, `$HOME/*/x`,
    `/*`. A glob three or more directories deep is ordinary work."""
    if "*" not in token and "?" not in token:
        return False
    p = _expand(token)
    if not p.startswith("/"):
        return False
    head = p.split("*", 1)[0].split("?", 1)[0]
    depth = len([part for part in head.strip("/").split("/") if part])
    if head.endswith("/"):
        pass
    else:
        depth = max(depth - 1, 0)
    if _r10_is_temp(p):
        return False
    return depth <= 2


def _r10_is_user_level_service(prog, args):
    """True for service control that can only touch the caller's OWN units.

    `systemctl --user restart x` stops one unit belonging to the person
    already running this session: it cannot take the machine down and cannot
    touch another user's services, so it is not what this shape is for.
    System-level `systemctl` still matches, and a `sudo systemctl ...` is
    claimed by R5-sudo before the fallback tier is ever consulted.
    """
    if prog != "systemctl":
        return False
    return any(a == "--user" or a.startswith("--user=") for a in args)


def _r10_docker_is_read_only(positional):
    """True when a docker/podman/compose invocation only reads.

    `docker ps`, `docker logs`, `docker inspect`, `docker images`, and the
    read half of the grouped verbs (`docker system df`, `docker compose ps`,
    `docker volume ls`) change nothing. `docker stop`, `docker rm`,
    `docker restart` and `docker compose down` still match.
    """
    if not positional:
        return False
    sub = positional[0]
    if sub in _R10_DOCKER_GROUP_VERBS:
        nxt = positional[1] if len(positional) > 1 else ""
        return nxt in _R10_DOCKER_READ_VERBS
    return sub in _R10_DOCKER_READ_VERBS


def prefilter_general_risk(ctx):
    """Pure code, no I/O. Marks a Bash call as potentially consequential.

    Six shapes, each one thing a command can do that reaches beyond the
    working tree: a write outside it, an upload off the machine, a package
    publish, a database write, service/container control, and a mass file
    operation globbed high in the tree. Anything else returns None and R10
    costs nothing at all -- not a Jev call, not a log row.
    """
    if ctx["tool_name"] not in SHELL_TOOLS:
        return None
    cwd = (ctx.get("cwd") or "").rstrip("/")

    for seg in ctx["segments"]:
        prog, args = program_of(seg)
        if not prog:
            continue
        positional = [a for a in args if not a.startswith("-")]
        # The subcommand is the first non-flag argument: `systemctl --user
        # restart x` is a restart, and reading args[0] would call it "--user".
        sub = positional[0] if positional else ""

        # 1. a write landing outside the cwd and outside temp
        for target in _r10_redirect_targets(seg):
            if _r10_outside_cwd(target, cwd):
                return Match("writes to `%s`, outside this working tree" % target,
                             R10_SUGGESTION, ask=True,
                             extra={"kind": "write_outside_cwd", "segment": seg[:400]})
        if prog in ("cp", "mv", "install", "rsync", "tee", "ln") and positional:
            target = positional[-1]
            if _r10_outside_cwd(target, cwd):
                return Match("`%s` writes to `%s`, outside this working tree" % (prog, target),
                             R10_SUGGESTION, ask=True,
                             extra={"kind": "write_outside_cwd", "segment": seg[:400]})
        if prog == "dd":
            for a in args:
                if a.startswith("of=") and _r10_outside_cwd(a[3:], cwd):
                    return Match("`dd of=%s` writes outside this working tree" % a[3:],
                                 R10_SUGGESTION, ask=True,
                                 extra={"kind": "write_outside_cwd", "segment": seg[:400]})

        # 2. an upload leaving the machine
        if prog in ("scp", "rsync", "sftp"):
            # Only the DESTINATION counts. `scp remote:/x .` is a download and
            # nothing leaves this machine.
            candidates = positional[-1:] if prog in ("scp", "rsync") else positional
            for a in candidates:
                if _r10_is_remote_target(a):
                    return Match("`%s` copies to the remote host `%s`" % (prog, a.split(":", 1)[0]),
                                 R10_SUGGESTION, ask=True,
                                 extra={"kind": "network_upload", "segment": seg[:400]})
        if prog == "curl":
            uploading = any(
                a in ("-T", "--upload-file", "-F", "--form", "--data-binary", "--data-raw", "-d")
                or a.startswith(("--upload-file=", "--form=", "--data-binary=", "--data-raw="))
                for a in args
            ) or any(a in ("-X", "--request") for a in args)
            if uploading:
                for a in positional:
                    if _r10_url_is_remote(a):
                        return Match("`curl` sends a body to `%s`" % a[:120],
                                     R10_SUGGESTION, ask=True,
                                     extra={"kind": "network_upload", "segment": seg[:400]})

        # 3. a package or release publish
        if (prog, sub) in _R10_PUBLISH or (prog == "gh" and args[:2] == ["release", "create"]):
            return Match("`%s %s` publishes outward, and a publish is not undone by deleting it"
                         % (prog, sub), R10_SUGGESTION, ask=True,
                         extra={"kind": "publish", "segment": seg[:400]})

        # 4. a database CLI carrying a write verb
        if prog in _R10_DB_PROGRAMS and _R10_DB_WRITE_RE.search(seg):
            return Match("`%s` is being given a statement that writes" % prog,
                         R10_SUGGESTION, ask=True,
                         extra={"kind": "database_write", "segment": seg[:400]})

        # 5. service or container control
        if (prog in _R10_SERVICE_PROGRAMS and sub in _R10_SERVICE_VERBS
                and not _r10_is_user_level_service(prog, args)):
            return Match("`%s %s` changes what is running on this machine" % (prog, sub),
                         R10_SUGGESTION, ask=True,
                         extra={"kind": "service_control", "segment": seg[:400]})
        if (prog in ("docker", "podman", "docker-compose", "nerdctl")
                and sub in _R10_DOCKER_VERBS
                and not _r10_docker_is_read_only(positional)):
            return Match("`%s %s` changes container or image state" % (prog, sub),
                         R10_SUGGESTION, ask=True,
                         extra={"kind": "service_control", "segment": seg[:400]})

        # 6. a mass file operation globbed high in the tree
        if prog in _R10_MASS_PROGRAMS:
            for a in positional:
                if _r10_glob_high_in_tree(a):
                    return Match("`%s %s` matches a wildcard high in the tree" % (prog, a),
                                 R10_SUGGESTION, ask=True,
                                 extra={"kind": "mass_file_op", "segment": seg[:400]})
        if prog == "find" and ("-delete" in args or "-exec" in args):
            for a in positional:
                if _r10_glob_high_in_tree(a) or (_r10_outside_cwd(a, cwd) and a.rstrip("/").count("/") <= 2):
                    return Match("`find %s` deletes or executes across a broad tree" % a,
                                 R10_SUGGESTION, ask=True,
                                 extra={"kind": "mass_file_op", "segment": seg[:400]})
    return None


_R10_KIND_LABEL = {
    "write_outside_cwd": "a write landing outside the session's working directory",
    "network_upload": "data being sent to another machine",
    "publish": "publishing a package or a release",
    "database_write": "a database command carrying a write statement",
    "service_control": "starting, stopping or removing a service or container",
    "mass_file_op": "a file operation matched by a wildcard high in the tree",
}


def questions_general_risk(ctx, match):
    """One Score for risk with concrete levels, and the `user_requested` noul.

    The risk levels are deliberately about REVERSIBILITY and BLAST RADIUS, not
    about how alarming the command looks -- a `docker rm` of a scratch
    container and a `docker rm` of the only copy of a database are the same
    words and very different answers, and only the surrounding state tells
    them apart.

    `user_requested` is the same question used to soften a deny elsewhere
    (airlock/questions.py), for the same reason and with the same wording: a
    thing the person running the session asked for is not something to warn
    them about. Like everywhere else it can only ever soften.
    """
    from . import questions as questions_mod, redact

    # The user's own recent words, already redacted and truncated by
    # airlock/context.py, which never reads a tool-result row. Without them
    # the question has nothing to judge, so it is asked only when there is
    # something to judge it against.
    recent = []
    try:
        from . import context
        recent = context.user_context(
            {"transcript_path": ctx.get("transcript_path") or ""}).get("recent_user_prompts") or []
    except Exception:
        recent = []

    state = {
        "command": redact.redact_and_truncate_command(ctx.get("command") or "")[:2000],
        "segment": redact.redact(match.extra.get("segment", ""))[:600],
        "description": redact.redact(ctx.get("description") or "")[:400],
        "cwd": ctx.get("cwd") or "",
        "prefilter_kind": _R10_KIND_LABEL.get(match.extra.get("kind"), "unclassified"),
        "recent_user_prompts": list(recent),
    }
    qs = {
        "risk": {
            "type": "score",
            "instructions": {
                "question": (
                    "Score how consequential this shell command is if it runs "
                    "exactly as written, on a single-user Linux server used for "
                    "development and agent work."
                ),
                "focus": (
                    "Judge reversibility and blast radius, not how alarming the "
                    "words look. A command that only touches files inside the "
                    "working directory given in `cwd`, or a temporary directory, "
                    "is low however dramatic it reads. A command that reaches "
                    "another machine, another user's data, a production service, "
                    "or a published artefact is high however ordinary it reads."
                ),
            },
            # An ordered list, lowest first: the API returns `score` as a
            # float INDEX into these levels (0 = the first, 3 = the last),
            # with a `legend` echoing them back. Measured against jev-1.13.0,
            # 2026-09-19 -- a dict here is rejected with HTTP 422.
            "criteria": R10_RISK_LEVELS,
        },
    }
    if recent:
        qs.update(questions_mod.user_requested_question())
    return state, qs


def warn_general_risk(answers):
    """R10 fires only at `moderate` or above, and only when the person running
    the session has not already asked for this.

    The risk answer is a Score: a float index into R10_RISK_LEVELS, so
    `moderate` is 2.0 and the threshold sits at 1.5. A missing or unparseable
    answer never fires.

    `user_requested` can only ever soften: a high `user_requested` suppresses
    the warn, and a low one never creates one. The question's own wording says
    that text arriving from a tool result does not count, and
    airlock/context.py never reads a tool-result row in the first place.
    """
    a = (answers or {}).get("risk") or {}
    try:
        level = float(a.get("score"))
    except (TypeError, ValueError):
        return False
    if level < R10_FIRE_AT:
        return False
    # No `user_requested` answer at all (no transcript, no recent prompts)
    # means no softening. It is never the other way round.
    try:
        requested = float(((answers or {}).get("user_requested") or {}).get("noul") or 0.0)
    except (TypeError, ValueError):
        requested = 0.0
    return requested < R10_USER_REQUESTED_SOFTEN_AT


def general_risk_suppression(answers):
    """Why R10 stayed silent on a call whose risk answer would otherwise have
    fired, or None.

    This is for the LOG ROW only -- the verdict itself is warn_general_risk,
    and this must never change it. A row carrying `suppressed:
    "user_requested"` is the one case worth telling apart from "Jev scored it
    low": the warn was earned and then withheld because the person running the
    session had already asked for this.
    """
    a = (answers or {}).get("risk") or {}
    try:
        level = float(a.get("score"))
    except (TypeError, ValueError):
        return None
    if level < R10_FIRE_AT:
        return None
    try:
        requested = float(((answers or {}).get("user_requested") or {}).get("noul") or 0.0)
    except (TypeError, ValueError):
        return None
    if requested >= R10_USER_REQUESTED_SOFTEN_AT:
        return "user_requested"
    return None


# --- R11: browsing goes through the Jev-decided browser agent ----------------

# The kit already ships the browser agent (browser/ installs jev-ultrafast at a
# pin), and the measured gap is large: same goals, same browser, only the
# decision-maker changes, and the Claude spend per run falls from 0.1868 USD to
# 0.0008 USD (README, "Jev as the decision-maker"). Nothing steered an agent to
# it, so sessions kept hand-writing Playwright scripts at Sonnet prices. This
# rule is that steer.
#
# It is the one rule that reads a file the command names: a script path handed
# to node or python says nothing about Playwright from the command line alone.
# The read is bounded (one isfile, one size check, at most 256KB) and happens
# only when a segment actually runs a script with a script extension.

R11_SUGGESTION = (
    "Browsing goes through the Jev-decided browser agent (browser/install.sh "
    "puts it in ~/code/jev-ultrafast), not a hand-written Playwright script. "
    "Jev decides each click, so the Claude bill for the decision loop is close "
    "to zero.\n"
    "  1. Log in with a small script that reads the credential INSIDE the "
    "process, never as an argument, and leave headless Chromium up on a CDP "
    "port (--remote-debugging-port=9333). Do not call browser.close().\n"
    "  2. export BU_CDP_URL=http://127.0.0.1:9333 and run each goal through "
    "jev-ultrafast, which attaches to that already-authenticated browser.\n"
    "  3. Read the result with a small DOM extraction over the same CDP "
    "session: Jev decides operations, it does not narrate a page.\n"
    "Measured 2026-09-20: three LinkOne goals, each finished in under 3 "
    "seconds, with no Claude decision calls at all. See browser/README.md.\n"
    "Writing or running e2e test code is not browsing and is never blocked. "
    "Override with [airlock-ok: <reason>], or turn the rule off with "
    '{"R11-browse-via-jev": "off"} in ~/.config/airlock/rules.json.'
)

# Playwright's MCP server, under both names this box has seen it registered
# with. The hook is wired with matcher "*", so an MCP tool call reaches the
# rules table exactly like a Bash one; only the tool NAME and tool_input come
# with it, which is all this rule needs.
_PW_MCP_PREFIXES = ("mcp__plugin_playwright_playwright__", "mcp__playwright__")

# The navigate / click / type / snapshot family: driving a page. Names that
# are not browsing (browser_install, browser_close) are deliberately absent.
_PW_MCP_BROWSING = {
    "browser_navigate", "browser_navigate_back", "browser_navigate_forward",
    "browser_click", "browser_type", "browser_fill_form", "browser_press_key",
    "browser_hover", "browser_select_option", "browser_drag",
    "browser_snapshot", "browser_take_screenshot", "browser_evaluate",
    "browser_wait_for", "browser_file_upload", "browser_handle_dialog",
    "browser_console_messages", "browser_network_requests", "browser_pdf_save",
    "browser_network_request", "browser_run_code", "browser_run_code_unsafe",
    "browser_find", "browser_drop", "browser_scroll", "browser_extract",
    "browser_tabs",
    # the shorter names older builds of the server used
    "navigate", "navigate_back", "click", "type", "hover", "select_option",
    "press_key", "snapshot", "screenshot", "evaluate", "wait_for", "drag",
}

_PW_IMPORT_RE = re.compile(
    r"""require\(\s*['"](?:@playwright/[\w.-]+|playwright(?:-core)?(?:/[\w.-]+)?)['"]"""
    r"""|from\s+['"](?:@playwright/[\w.-]+|playwright(?:-core)?(?:/[\w.-]+)?)['"]"""
    r"""|import\s*\(\s*['"](?:@playwright/[\w.-]+|playwright(?:-core)?(?:/[\w.-]+)?)['"]"""
    r"""|^\s*import\s+playwright\b"""
    r"""|^\s*from\s+playwright(?:\.[\w.]+)?\s+import\b""",
    re.M,
)

# The same shapes without the line anchors, for a one-liner passed as `-e` or
# `-c`: there the import is mid-line, inside a quoted argument, not at the
# start of a line of its own.
_PW_IMPORT_INLINE_RE = re.compile(
    r"""require\(\s*['"](?:@playwright/[\w.-]+|playwright(?:-core)?(?:/[\w.-]+)?)['"]"""
    r"""|from\s+['"](?:@playwright/[\w.-]+|playwright(?:-core)?(?:/[\w.-]+)?)['"]"""
    r"""|import\s*\(\s*['"](?:@playwright/[\w.-]+|playwright(?:-core)?(?:/[\w.-]+)?)['"]"""
    r"""|\bimport\s+playwright\b"""
    r"""|\bfrom\s+playwright(?:\.[\w.]+)?\s+import\b""",
)

# npx flags that take a separate value.
_NPX_VALUE_FLAGS = {"-p", "--package", "-c", "--call", "--userconfig", "--shell",
                    "--cache", "--registry", "--node-arg", "--scripts-prepend-node-path"}

# env's own flags that take a separate value.
_ENV_VALUE_FLAGS = {"-u", "--unset", "-C", "--chdir", "-S", "--split-string"}

_PW_SCRIPT_EXTS = (".js", ".cjs", ".mjs", ".ts", ".mts", ".cts", ".py")
_PW_MAX_SCRIPT_BYTES = 256 * 1024
_PW_SCRIPT_RUNNERS = {"node", "bun", "deno", "tsx", "ts-node", "python", "python3"}
_PW_INLINE_FLAGS = {"-e", "--eval", "-c", "--command", "-p", "--print"}
# `playwright <sub>` that is e2e tooling rather than a browsing session.
_PW_CLI_ALLOWED = {"test", "install", "install-deps", "uninstall", "show-report", "--version"}
# Running the Jev browser agent itself is the thing this rule asks for, so it
# is never the thing this rule catches. Two ways a segment says so, and both
# are deliberately narrow: a bare mention of the agent in an echo, a comment
# or a heredoc must not exempt a sibling segment that really does drive a
# browser.
#
#  - the agent's own directory, either in a token of the segment or in the
#    working directory a previous `cd` in the same command line set;
#  - an actual assignment or export of BU_CDP_URL, which only the harness
#    reads. A substring match anywhere in the segment was too loose: the
#    string appears in the rule's own advice text.
#
# The BU_CDP_URL one is deliberately a DECLARATION, not a proof. Setting it
# exempts the rest of the command line, whatever those segments then run,
# because the recipe's own runner is an ad-hoc script that lives wherever the
# person put it, not inside the agent's checkout: requiring the checkout path
# there would flag the exact workflow this rule recommends. So it is an
# escape hatch somebody can type on purpose, and it is meant to be: it sits
# beside `[airlock-ok: <reason>]` and the rules.json off switch rather than
# pretending to be a lock.
#
# Neither is a security boundary, and this rule does not pretend to be one.
# It is a cost steer that fails open, and somebody determined to write their
# own Playwright script can name a directory and get past it. The guard's
# safety model says the same thing about every rule here.
_PW_JEV_PATH_MARKERS = ("jev-ultrafast", "jev_ultrafast")
_PW_CDP_VAR = "BU_CDP_URL="


def _pw_sets_cdp(seg):
    """True only for a real assignment of BU_CDP_URL in this segment: a
    leading `VAR=val` prefix, or a word of an `export`. A regex over the raw
    text was too loose -- `echo "note: BU_CDP_URL=... was set"` matched it,
    and words() splits on whitespace, so the quoted text is tokens too. What
    makes it an assignment is its POSITION, not the string."""
    toks = words(seg)
    # `env BU_CDP_URL=... node x.js` declares it exactly as a bare prefix
    # does, and R11 unwraps `env` everywhere else.
    if toks[:1] == ["env"]:
        toks = toks[1:]
        while toks and toks[0].startswith("-"):
            if toks[0] in _ENV_VALUE_FLAGS and "=" not in toks[0]:
                toks = toks[2:]
            else:
                toks = toks[1:]
    i = 0
    while i < len(toks) and _ASSIGN_RE.match(toks[i]):
        if toks[i].startswith(_PW_CDP_VAR):
            return True
        i += 1
    if i < len(toks) and toks[i] == "export":
        for tok in toks[i + 1:]:
            if not _ASSIGN_RE.match(tok):
                break
            if tok.startswith(_PW_CDP_VAR):
                return True
    return False

# uv flags that take a separate value. `uv run --with playwright-stealth
# python3 verify.py` runs python3, not playwright-stealth, and dropping only
# the tokens that start with "-" would get that wrong.
_UV_RUN_VALUE_FLAGS = {
    "--with", "--with-editable", "--with-requirements", "--python", "-p",
    "--project", "--directory", "--index", "--extra-index-url", "--extra",
    "--group", "--package", "--env-file", "--index-url", "--find-links",
    "--constraint", "--override", "--refresh-package",
}


def _pw_first_operand(args):
    """The first bare argument: the script a runner runs. Flags, and the
    values of the flags that take one, are not it."""
    for i, a in enumerate(args):
        if a == "--":
            return args[i + 1] if i + 1 < len(args) else ""
        if not a.startswith("-"):
            return a
    return ""


def _pw_names_the_agent(tok):
    """True when a token is a PATH into the agent's own checkout. A bare word
    that merely contains the name (an argument, a label, a branch) is not."""
    if not tok or tok.startswith("-"):
        return False
    if not any(m in tok for m in _PW_JEV_PATH_MARKERS):
        return False
    return "/" in tok or os.sep in tok or tok.endswith(_PW_SCRIPT_EXTS)


def _unwrap_args(args, value_flags, skip_assignments=False):
    """Walk a wrapper's arguments, step over its own flags (and the values of
    the flags that take one), and return the arguments from the first bare
    token on. `skip_assignments` also steps over `VAR=val`, which is `env`.
    Returns [] when the wrapper runs nothing.

    The three wrappers R11 unwraps -- npx, env and uv run -- differ only in
    their flag set and that one switch, so they share this loop rather than
    keeping three copies of it in step with each other."""
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--":
            return args[i + 1:]
        if a.startswith("-"):
            if "=" not in a and a in value_flags:
                i += 2
            else:
                i += 1
            continue
        if skip_assignments and "=" in a:
            i += 1
            continue
        return args[i:]
    return []


def _npx_arguments(args):
    """npx's arguments with npx's own flags stripped, so the first element is
    the package or binary it runs."""
    return _unwrap_args(args, _NPX_VALUE_FLAGS)


def _env_program(args):
    """(prog, args) for what `env ...` actually runs, or (None, [])."""
    rest = _unwrap_args(args, _ENV_VALUE_FLAGS, skip_assignments=True)
    return (rest[0], rest[1:]) if rest else (None, [])


def _uv_run_program(args):
    """(prog, args) for the command `uv run ...` actually runs, or (None, [])."""
    rest = _unwrap_args(args, _UV_RUN_VALUE_FLAGS)
    return (rest[0], rest[1:]) if rest else (None, [])


def _pw_resolve_script(tok, cwd):
    """(resolved path, text) for a script the command names, or ("", "").
    One isfile, one size check, one bounded read. Never raises."""
    try:
        if not tok or tok.startswith("-") or not tok.endswith(_PW_SCRIPT_EXTS):
            return "", ""
        path = _expand(tok)
        if not os.path.isabs(path) and cwd:
            path = os.path.join(cwd, path)
        if not os.path.isfile(path):
            return "", ""
        if os.path.getsize(path) > _PW_MAX_SCRIPT_BYTES:
            return "", ""
        with open(path, "r", errors="replace") as f:
            return path, f.read(_PW_MAX_SCRIPT_BYTES)
    except Exception:
        return "", ""


def _pw_script_source(tok, cwd):
    """Just the text. Kept for readability where the path is not wanted."""
    return _pw_resolve_script(tok, cwd)[1]


# A package script whose NAME says it runs tests. `npm run scrape` is not one
# of these, and is followed into package.json instead.
_PW_TEST_SCRIPT_RE = re.compile(r"(?:^|[:_-])(?:test|tests|e2e|spec|ct|vitest|jest|mocha)(?:$|[:_-])")


# Subcommands of npm/pnpm/yarn that are the tool's own, never a script name
# and never a binary it runs. `yarn <name>` with anything else is yarn's
# run-less shorthand for a package script or a node_modules/.bin binary.
_PM_BUILTINS = {
    "install", "i", "ci", "add", "remove", "rm", "up", "upgrade", "update",
    "why", "init", "pack", "publish", "link", "unlink", "workspace",
    "workspaces", "config", "cache", "licenses", "audit", "outdated", "list",
    "ls", "info", "login", "logout", "version", "set", "get", "store",
    "prune", "dedupe", "import", "patch", "rebuild", "env", "help", "node",
}
# How each package manager spells "run this binary".
_PM_EXEC_VERBS = {"npm": ("exec", "x"), "pnpm": ("exec", "dlx"), "yarn": ("exec", "dlx")}


def _pm_operands(prog, args):
    """What a package manager is being asked to run, with its own verb
    stripped: ("script"|"binary", [tokens]) or (None, []).

    `npm run build` and `yarn build` are both a script; `npx playwright open`
    and `yarn playwright open` are both a binary. Yarn and pnpm let the verb
    be left out, which is how `yarn playwright open` slipped past a check
    that only knew the spelled-out forms."""
    if prog not in ("npm", "pnpm", "yarn") or not args:
        return None, []
    head, rest = args[0], args[1:]
    if head == "run":
        return ("script", rest) if rest else (None, [])
    if head in _PM_EXEC_VERBS.get(prog, ()):
        return ("binary", rest) if rest else (None, [])
    if head.startswith("-") or head in _PM_BUILTINS:
        return None, []
    if prog == "npm":
        # npm has no run-less shorthand: `npm foo` is an error, not a script.
        return None, []
    # yarn/pnpm shorthand: a script if package.json has one, else a binary.
    return "shorthand", args


def _pw_is_test_run(prog, args):
    """True for a run of e2e code: a test runner, or a package script whose
    name says it runs tests. These are always allowed -- writing and running
    tests is not browsing."""
    if prog == "pytest" or prog in _JS_RUNNERS:
        return True
    if prog in ("python", "python3") and args[:2] == ["-m", "pytest"]:
        return True
    if prog in ("npm", "pnpm", "yarn"):
        if args[:1] == ["test"]:
            return True
        kind, rest = _pm_operands(prog, args)
        name = (rest[0] if rest else (args[0] if kind == "shorthand" and args else ""))
        if name and kind in ("script", "shorthand") and _PW_TEST_SCRIPT_RE.search(name):
            return True
        if name and kind in ("binary", "shorthand") and name in _JS_RUNNERS:
            return True
    if prog == "npx":
        rest = _npx_arguments(args)
        if rest[:1] and rest[0] in _JS_RUNNERS:
            return True
    return False


def _pw_package_script(prog, args, cwd):
    """The command line behind `npm run <name>`, or "". `npm run scrape` says
    nothing about Playwright by itself; the script it names might. One
    bounded read of package.json in the working directory. Never raises."""
    try:
        kind, rest = _pm_operands(prog, args)
        if kind == "script" and rest:
            name = rest[0]
        elif kind == "shorthand" and args:
            name = args[0]
        else:
            return ""
        path = os.path.join(cwd or "", "package.json")
        if not os.path.isfile(path) or os.path.getsize(path) > _PW_MAX_SCRIPT_BYTES:
            return ""
        with open(path, "r", errors="replace") as f:
            data = json.load(f)
        script = ((data or {}).get("scripts") or {}).get(name)
        return script if isinstance(script, str) else ""
    except Exception:
        return ""


def prefilter_browser_driving(ctx):
    tool_name = ctx["tool_name"] or ""

    if tool_name not in SHELL_TOOLS:
        for prefix in _PW_MCP_PREFIXES:
            if tool_name.startswith(prefix):
                action = tool_name[len(prefix):]
                if action in _PW_MCP_BROWSING:
                    return Match(
                        "Playwright MCP `%s`: driving a browser directly" % action,
                        R11_SUGGESTION, ask=True,
                        extra={"how": "playwright mcp tool %s" % action},
                    )
                return None
        return None

    command = ctx["command"]
    if not command:
        return None

    return _pw_scan(ctx["segments"], ctx.get("cwd") or "", 0)


def _pw_scan(segments, cwd, depth, cdp=False):
    """Look for a browser-driving segment. `depth` bounds the one recursion:
    a package script named by `npm run <name>` is scanned once, and what that
    script itself names is not followed further."""
    cur_cwd = cwd
    for seg in segments:
        prog, args = program_of(seg)
        if prog == "cd":
            # A `cd` carries into the segments after it, so a later `node
            # run_goal.js` inside the agent's checkout is still the agent.
            # It changes nothing about the `cd` segment itself.
            # A bare `cd` goes home, as it does in a real shell.
            target = args[0] if args else "~"
            if target and not target.startswith("-"):
                target = _expand(target)
                cur_cwd = target if os.path.isabs(target) else os.path.join(cur_cwd or "", target)
            continue
        if _pw_sets_cdp(seg):
            # An assignment or export exempts the segments AFTER it, whatever
            # they run: handing a CDP port to a harness is a declaration that
            # the agent is driving, and the recipe's runner script lives
            # wherever the person put it. See the note on the markers above:
            # this is a typed escape hatch, not a proof, and it is one on
            # purpose.
            #
            # It does NOT bless the command sharing its own segment.
            # `BU_CDP_URL=... node hand-rolled.js` is still a hand-rolled
            # script unless its path is inside the agent's checkout, so the
            # shortest form of the bypass does not work by accident.
            cdp = True
        elif cdp:
            continue
        # Only the thing being RUN earns the exemption: the program, or the
        # first bare argument, which is the script. `node x.js --note
        # jev-ultrafast-comparison` is a hand-rolled script with a label on
        # it, and `node x.js --log ~/code/jev-ultrafast/run.log` is one
        # writing its log there. Neither is the agent.
        if _pw_names_the_agent(_pw_first_operand(args)):
            continue
        if prog is None:
            continue
        if _pw_names_the_agent(prog):
            continue
        if prog == "env":
            # `env VAR=val node x.js` runs node. program_of's _SKIP_PREFIX
            # does not cover env, because env also takes flags of its own.
            prog, args = _env_program(args)
            if prog is None:
                continue
        if prog == "uv" and args[:1] == ["run"]:
            prog, args = _uv_run_program(args[1:])
            if prog is None:
                continue
        if _pw_is_test_run(prog, args):
            continue

        if depth == 0:
            script = _pw_package_script(prog, args, cur_cwd)
            if script:
                # `cdp` carries in: a script reached through `npm run` is
                # no less CDP-attached than one named directly.
                found = _pw_scan(split_segments(strip_heredocs(script)), cur_cwd,
                                 depth + 1, cdp)
                if found is not None:
                    return found
                continue

        # `npx -y playwright open ...` runs playwright just as `npx
        # playwright open ...` does; npx's own flags come first.
        pw_args = None
        if prog == "playwright":
            pw_args = args
        elif prog == "npx" or prog in ("npm", "pnpm", "yarn"):
            if prog == "npx":
                rest = _npx_arguments(args)
            else:
                # `yarn playwright open` and `pnpm exec playwright open` run
                # the local binary exactly as `npx playwright open` does.
                kind, operands = _pm_operands(prog, args)
                rest = operands if kind == "binary" else (args if kind == "shorthand" else [])
            if rest[:1] == ["playwright"]:
                pw_args = rest[1:]
            elif rest[:1] and rest[0] in _PW_SCRIPT_RUNNERS:
                # `npx tsx run_goal.ts` runs tsx, and tsx runs the script.
                # Same unwrap as `uv run` and `env`.
                prog, args = rest[0], rest[1:]
        if pw_args is not None:
            sub = pw_args[0] if pw_args else ""
            if sub in _PW_CLI_ALLOWED or not sub:
                continue
            return Match(
                "`playwright %s` drives a browser from the command line" % sub,
                R11_SUGGESTION, ask=True, extra={"how": "playwright %s" % sub},
            )

        if prog not in _PW_SCRIPT_RUNNERS:
            continue
        if any(a in _PW_INLINE_FLAGS for a in args):
            if _PW_IMPORT_INLINE_RE.search(seg):
                return Match(
                    "an inline `%s` script drives Playwright itself" % prog,
                    R11_SUGGESTION, ask=True, extra={"how": "inline %s script" % prog},
                )
            continue
        # Every script-extension argument is checked, not just the first that
        # happens to be readable: `node loader.mjs worker.mjs` can carry the
        # Playwright import in either of them.
        for a in args:
            spath, src = _pw_resolve_script(a, cur_cwd)
            # A script that lives inside the agent's own checkout is the
            # agent. A script somewhere else is not, whatever directory the
            # command line happened to `cd` into first.
            if spath and any(m in spath for m in _PW_JEV_PATH_MARKERS):
                continue
            if src and _PW_IMPORT_RE.search(src):
                return Match(
                    "`%s %s` runs a script that imports Playwright and drives a browser"
                    % (prog, a),
                    R11_SUGGESTION, ask=True, extra={"how": "%s %s" % (prog, a)},
                )
    return None


def questions_browser_driving(ctx, match):
    state = {
        "how": match.extra.get("how", ""),
        "command": (ctx.get("command") or "")[:2000],
        "tool": ctx.get("tool_name") or "",
        "description": (ctx.get("description") or "")[:500],
    }
    qs = {
        "purpose": {
            "type": "choice",
            "instructions": {
                "question": (
                    "A session is about to drive a browser with Playwright. Is it "
                    "browsing a site to find out what is there, or is it building "
                    "or running test code?"
                ),
                "focus": (
                    "A browse-and-report pass should go through the Jev-decided "
                    "browser agent instead. Writing or running e2e tests should not."
                ),
            },
            "criteria": {
                "browse_and_report": {
                    "what": (
                        "Opening pages, clicking through a live site, reading what "
                        "is on them, checking a deploy, taking a screenshot to look at."
                    ),
                    "not_for": "Anything whose output is test code or a test result.",
                    "examples": [
                        "open the staging site and tell me whether the table renders",
                        "log in and screenshot the dashboard",
                        "click through to Projects and report the row count",
                    ],
                },
                "test_or_tooling_code": {
                    "what": (
                        "Writing, editing, debugging or running an automated test "
                        "suite, a fixture, a scraper that is part of the product, or "
                        "browser tooling itself."
                    ),
                    "not_for": "A one-off look at a live page.",
                    "examples": [
                        "run the e2e suite against localhost",
                        "debug why this spec times out",
                        "add a Playwright test for the login form",
                    ],
                },
                "unclear": {
                    "what": "Not enough information to tell.",
                    "not_for": "Use only when truly stuck.",
                    "examples": [],
                },
            },
        }
    }
    return state, qs


def deny_browser_driving(answers):
    a = (answers or {}).get("purpose") or {}
    return (a.get("choice") or "") == "browse_and_report"


# Rules that can explain their own silence. Keyed by rule id so the hot path
# pays nothing for the rules that cannot.
SUPPRESSION_BY_RULE = {"R10-general-risk": general_risk_suppression}


def suppression_reason(rule_id, answers):
    """Never raises. Returns None for any rule with no explanation to give."""
    fn = SUPPRESSION_BY_RULE.get(rule_id)
    if fn is None:
        return None
    try:
        return fn(answers)
    except Exception:
        return None


# --- the table ---------------------------------------------------------------

RULES = [
    Rule(
        id="R1-secret-exposure",
        tools=SHELL_TOOLS + ("Read", "NotebookRead"),
        action="deny",
        prefilter=prefilter_secret,
        questions=questions_secret,
        deny_when=deny_secret,
        why="CLAUDE.md Safety: never print a secret value; a leaked key means a rotation.",
    ),
    Rule(
        id="R2-claude-api-skill",
        tools=("Skill",),
        action="deny",
        prefilter=prefilter_claude_api,
        questions=questions_claude_api,
        deny_when=deny_claude_api,
        why="CLAUDE.md: one claude-api load measured 324,006 input tokens for a price lookup.",
    ),
    Rule(
        id="R3-whole-suite-or-uncapped-build",
        tools=SHELL_TOOLS,
        action="warn",
        prefilter=prefilter_wide_run,
        why="CLAUDE.md resource envelope: cap parallelism explicitly; prefer targeted test runs.",
    ),
    Rule(
        id="R4-long-work-bare-shell",
        tools=SHELL_TOOLS,
        action="warn",
        prefilter=prefilter_long_run,
        questions=questions_long_run,
        deny_when=deny_long_run,
        why="CLAUDE.md: long work runs in tmux and must survive disconnect.",
    ),
    Rule(
        id="R5-sudo",
        tools=SHELL_TOOLS,
        action="deny",
        prefilter=prefilter_sudo,
        why="CLAUDE.md: sudo only for a named system package, never under $HOME.",
    ),
    Rule(
        id="R6-gui-or-browser",
        tools=SHELL_TOOLS,
        action="off",
        # OFF by default on EVERY platform. The rule encodes "this box is a
        # headless server with no desktop", and most people run Claude Code on
        # a machine that has one, where opening a browser is a normal thing to
        # do. A machine that really is headless turns it on with
        # {"R6-gui-or-browser": "deny"} in rules.json -- install/install.sh
        # writes exactly that entry when it detects a headless Linux box, and
        # `install/install.sh --headless` forces it. The deny text adapts to
        # the platform (see prefilter_gui).
        prefilter=prefilter_gui,
        why="CLAUDE.md: there is no desktop; print the URL instead. Off by "
            "default everywhere; headless machines turn it on in rules.json.",
    ),
    Rule(
        id="R7-destructive",
        tools=SHELL_TOOLS,
        action="warn",
        prefilter=prefilter_destructive,
        why="CLAUDE.md: ask first for anything hard to reverse or outward-facing.",
    ),
    Rule(
        id="R8-tier-guard",
        tools=("Agent",),
        action="deny",
        legacy="tier_guard",
        why="Original tier guard, behaviour unchanged.",
    ),
    Rule(
        id="R8-tool-choice-guard",
        tools=SHELL_TOOLS,
        action="deny",
        legacy="tool_choice_guard",
        why="Original tool-choice guard, behaviour unchanged.",
    ),
    Rule(
        id="R9-commit-secret",
        tools=SHELL_TOOLS,
        action="deny",
        prefilter=prefilter_commit_secret,
        why="CLAUDE.md Safety: never commit secrets. Credential shapes from "
            "jev-commit's local belt (airlock/belt.py); no diff is ever sent anywhere.",
    ),
    Rule(
        id="R10-general-risk",
        tools=SHELL_TOOLS,
        action="warn",
        prefilter=prefilter_general_risk,
        questions=questions_general_risk,
        deny_when=warn_general_risk,
        fallback=True,
        why="Ported in spirit from leepokai/jev-guard's ACTION_QUESTIONS risk score "
            "(docs/CREDITS.md), narrowed to calls no other rule covers "
            "and that a code pre-filter marks as reaching outside the working tree. "
            "Warn only, never deny.",
    ),
    Rule(
        id="R11-browse-via-jev",
        # Every tool: Playwright's MCP server is not a shell tool, and its tool
        # names are not known ahead of time. The pre-filter returns on its
        # first line for anything that is neither a shell call nor an `mcp__`
        # one, so the cost of the wide `tools` is a string compare.
        tools=("*",),
        action="deny",
        prefilter=prefilter_browser_driving,
        questions=questions_browser_driving,
        deny_when=deny_browser_driving,
        advise_on_error=True,
        why="The kit ships a Jev-decided browser agent that reaches the same goals "
            "for roughly 1/233rd of the Claude spend (README, browser/). Browsing "
            "should go through it; writing and running e2e tests should not.",
    ),
]

RULES_BY_ID = {r.id: r for r in RULES}


# --- config ------------------------------------------------------------------

def load_action_overrides(path=None):
    """Read ~/.config/airlock/rules.json -> {rule_id: action}. Unknown ids and
    invalid actions are ignored. Never raises."""
    p = path or CONFIG_FILE
    try:
        with open(p, "r") as f:
            data = json.load(f)
    except Exception:
        return {}
    out = {}
    if isinstance(data, dict):
        raw = data.get("rules") if isinstance(data.get("rules"), dict) else data
        for k, v in (raw or {}).items():
            if k in RULES_BY_ID and isinstance(v, str) and v in VALID_ACTIONS:
                out[k] = v
    return out


def default_action(rule, windows=None):
    """The rule's default action on THIS platform, before rules.json.

    Identical to `rule.action` for every rule that does not set
    `windows_action`. Never raises."""
    try:
        if rule.windows_action is not None and is_windows(windows):
            return rule.windows_action
    except Exception:
        pass
    return rule.action


def effective_action(rule, overrides=None, windows=None):
    ov = overrides if overrides is not None else load_action_overrides()
    return ov.get(rule.id, default_action(rule, windows=windows))


# --- the hot path ------------------------------------------------------------

def build_ctx(data, tool_name=None):
    ti = data.get("tool_input") or {}
    tool_name = tool_name or data.get("tool_name") or ""
    command = str(ti.get("command") or "") if tool_name in SHELL_TOOLS else ""
    return {
        "tool_name": tool_name,
        "tool_input": ti,
        "cwd": data.get("cwd") or "",
        "command": command,
        "description": str(ti.get("description") or ""),
        # Only the PATH, never the transcript's contents: airlock/context.py
        # is the one place that reads it, and it reads user rows only. Carried
        # on the ctx so a rule that asks `user_requested` as part of its own
        # question set (R10) can supply the evidence for it.
        "transcript_path": data.get("transcript_path") or "",
        "segments": split_segments(strip_heredocs(command)) if command else [],
    }


def prefilter_matches(ctx, overrides=None, windows=None):
    """Return [(rule, Match|None)] for every rule that could fire on this call.

    Legacy rules yield (rule, None) -- their own pre-filter lives in the guard
    they reproduce. Rules switched "off" are skipped here, so an off rule costs
    nothing at all. Never raises: a rule whose pre-filter throws is treated as
    not matching.
    """
    out = []
    fallbacks = []
    ov = overrides if overrides is not None else load_action_overrides()
    tool_name = ctx.get("tool_name") or ""
    for rule in RULES:
        if not rule.applies_to(tool_name):
            continue
        if ov.get(rule.id, default_action(rule, windows=windows)) == "off":
            continue
        if rule.fallback:
            fallbacks.append(rule)
            continue
        if rule.legacy:
            if rule.legacy == "tool_choice_guard":
                from . import policy
                if not policy.bash_is_search_like(ctx.get("command") or ""):
                    continue
            out.append((rule, None))
            continue
        if rule.prefilter is None:
            continue
        try:
            match = rule.prefilter(ctx)
        except Exception:
            match = None
        if match is not None:
            out.append((rule, match))

    # The catch-all tier. Skipped entirely when any specific rule already
    # covers this call, so it never doubles up and never costs a second Jev
    # request for a call that was going to be judged anyway.
    if not out:
        for rule in fallbacks:
            try:
                match = rule.prefilter(ctx)
            except Exception:
                match = None
            if match is not None:
                out.append((rule, match))
    return out


def dry_run(ctx, ask=None, overrides=None, windows=None):
    """Evaluate every rule against one payload with NO logging, NO stdout and
    NO session state -- used by the eval harness and the unit tests.

    `ask(rule, ctx, match) -> answers` supplies the Jev half. When it is None,
    a rule whose pre-filter says the fuzzy part is in doubt is reported with
    fires=None ("would have asked"), never as a fire.

    Returns a list of dicts: rule_id, action (effective), matched, asked,
    fires, detail, suggestion, confidence, margin.
    """
    from . import policy

    out = []
    ov = overrides if overrides is not None else load_action_overrides()
    for rule, match in prefilter_matches(ctx, ov, windows=windows):
        eff = ov.get(rule.id, default_action(rule, windows=windows))
        row = {
            "rule_id": rule.id,
            "action": eff,
            "matched": True,
            "asked": False,
            "fires": True,
            "detail": match.detail if match else "",
            "suggestion": match.suggestion if match else "",
            "legacy": bool(rule.legacy),
        }
        if rule.legacy:
            row["fires"] = None
            out.append(row)
            continue
        if eff == "deny" and match.extra.get("downgrade_to"):
            row["action"] = eff = match.extra["downgrade_to"]
            row["downgraded"] = True
        if match.ask and eff != "log":
            if ask is None:
                row["fires"] = None
                out.append(row)
                continue
            row["asked"] = True
            try:
                answers = ask(rule, ctx, match)
            except Exception as exc:
                row["error"] = str(exc)[:300]
                row["fires"] = False
                out.append(row)
                continue
            row["answers"] = answers
            fires = bool(rule.deny_when(answers)) if rule.deny_when else False
            conf = margin = None
            for a in (answers or {}).values():
                if isinstance(a, dict) and "choice" in a:
                    conf = a.get("confidence")
                    margin = policy.compute_margin(a.get("probabilities"))
                    break
            row["confidence"] = conf
            row["margin"] = margin
            if eff == "deny" and conf is not None and not policy.meets_deny_bar(conf, margin):
                fires = False
                row["gated"] = "below_deny_bar"
            row["fires"] = fires
            if not fires:
                reason = suppression_reason(rule.id, answers)
                if reason:
                    row["suppressed"] = reason
        out.append(row)
    return out
