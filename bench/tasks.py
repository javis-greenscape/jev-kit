"""Task definitions and ground-truth/correctness checks for the airlock A/B
bench (Part B). Ground truth for T1/T2 is computed fresh at bench-run time
(file counts drift); T3-T5 ground truth is a fixed fact about a pinned repo
and function, checked once at import time via a real Read, not re-derived per
trial.

Every checker takes the session's FINAL ASSISTANT TEXT (joined) plus, for T5,
the per-trial scratch file path, and returns True/False. Checkers are
deliberately lenient substring/regex tests, not full NLU -- the bench cares
whether the right fact reached the final answer, not exact phrasing.
"""
import re
import subprocess
from pathlib import Path

HOME = str(Path.home())
AUTO_MAIL = Path(HOME) / "code" / "auto-mail"
T3_FILE_REL = "src/stages/stage4_claim.py"
T3_FUNC = "_list_optional_root"
T5_FILE_REL = "src/silence_alert.py"
T5_FUNC = "_parse_iso"


def _run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=30)


def compute_t1_truth():
    """Count of .xlsm files under $HOME, plocate cross-checked with fd."""
    plocate_out = _run([
        "plocate", "-d", str(Path(HOME) / ".cache" / "plocate" / "home.db"),
        "--regex", r"\.xlsm$",
    ])
    plocate_n = len([line for line in plocate_out.stdout.splitlines() if line.strip()])
    fd_out = _run(["fd", "-HI", "--type", "f", "-e", "xlsm", ".", HOME])
    fd_n = len([line for line in fd_out.stdout.splitlines() if line.strip()])
    return {"plocate": plocate_n, "fd": fd_n, "agree": plocate_n == fd_n, "truth": fd_n}


def compute_t2_truth():
    """Rare-filename search: ROUTES-STAGE-NOTES.md, or anything matching
    'routes-stage' in its name. Falls back to whatever plocate finds."""
    out = _run(["plocate", "-d", str(Path(HOME) / ".cache" / "plocate" / "home.db"), "-i", "routes-stage"])
    matches = [line.strip() for line in out.stdout.splitlines() if line.strip()]
    return {"matches": matches, "count": len(matches)}


def _verify_t3_ground_truth():
    """One-time sanity check (not per-trial): the pinned function still lives
    where we think it does, so a code drift in auto-mail doesn't silently
    invalidate the bench's ground truth."""
    path = AUTO_MAIL / T3_FILE_REL
    text = path.read_text()
    lines = text.splitlines()
    def_line = None
    for i, line in enumerate(lines, start=1):
        if line.startswith("def %s(" % T3_FUNC):
            def_line = i
            break
    call_line = None
    for i, line in enumerate(lines, start=1):
        if "%s(" % T3_FUNC in line and not line.lstrip().startswith("def "):
            call_line = i
            break
    return {"file": T3_FILE_REL, "def_line": def_line, "call_line": call_line}


T1_TASK = {
    "id": "T1",
    "kind": "file_find",
    "prompt": "List every .xlsm file under /home/user and tell me how many there are.",
    "cwd": HOME,
}

T2_TASK = {
    "id": "T2",
    "kind": "file_find",
    "prompt": (
        "Find the file called ROUTES-STAGE-NOTES.md or anything with "
        "'routes-stage' in its name anywhere under my home directory."
    ),
    "cwd": HOME,
}

T3_TASK = {
    "id": "T3",
    "kind": "code_structure",
    "prompt": (
        "In the repository at %s, where is the function %s defined, and what calls it?"
        % (AUTO_MAIL, T3_FUNC)
    ),
    "cwd": str(AUTO_MAIL),
}

T4_TASK = {
    "id": "T4",
    "kind": "delegation_lookup",
    "prompt": (
        "Use a sub-agent to find which file in %s defines the function %s, "
        "and report only the path." % (AUTO_MAIL, T3_FUNC)
    ),
    "cwd": str(AUTO_MAIL),
}

# T5's prompt is a template: {scratch_path} is filled in per trial by run.py,
# which first copies src/silence_alert.py to a fresh scratch location so
# concurrent/sequential trials never edit the same file.
T5_TASK = {
    "id": "T5",
    "kind": "delegation_scoped",
    "prompt_template": (
        "Use a sub-agent to add a one-line docstring to the function %s in "
        "the file at {scratch_path}." % T5_FUNC
    ),
    "cwd": "/tmp/jev-bench",
}

TASKS = [T1_TASK, T2_TASK, T3_TASK, T4_TASK, T5_TASK]


def check_t1(final_text, truth):
    n = truth["truth"]
    return bool(re.search(r"(?<!\d)%d(?!\d)" % n, final_text))


def check_t2(final_text, truth):
    matches = truth["matches"]
    if not matches:
        return bool(re.search(r"\bno\b.*\bfound\b|\bcould not find\b|\bnot found\b", final_text, re.IGNORECASE))
    for m in matches:
        name = Path(m).name
        if name in final_text or m in final_text:
            return True
    return False


def check_t3(final_text, truth):
    file_ok = T3_FILE_REL in final_text or T3_FILE_REL.split("/")[-1] in final_text
    caller_ok = bool(re.search(r"\brun\s*\(", final_text))
    return file_ok and caller_ok


def check_t4(final_text, truth):
    return T3_FILE_REL in final_text or (str(AUTO_MAIL / T3_FILE_REL)) in final_text


def check_t5(final_text, scratch_path):
    """Correct iff the scratch copy now has a docstring as the first
    statement of _parse_iso's body, AND the original repo file is untouched.

    "Untouched" is checked against a pinned baseline snapshot at
    /tmp/jev-bench/silence_alert.py, captured lazily on first use (bench/run.py
    never writes this path itself -- it only copies to per-trial
    silence_alert_<uuid>.py scratch files). If the pin doesn't exist yet, we
    create it from the current on-disk source, which is only safe when that
    source has no uncommitted changes (checked via git diff) -- otherwise a
    prior, already-corrupted state would get pinned as "good"."""
    pin_path = Path("/tmp/jev-bench/silence_alert.py")
    try:
        original = (AUTO_MAIL / T5_FILE_REL).read_text()
        if not pin_path.exists():
            diff = subprocess.run(
                ["git", "-C", str(AUTO_MAIL), "diff", "--quiet", T5_FILE_REL],
            )
            if diff.returncode != 0:
                return False, "no pinned baseline yet and source has uncommitted changes -- refusing to pin"
            pin_path.parent.mkdir(parents=True, exist_ok=True)
            pin_path.write_text(original)
        pinned = pin_path.read_text()
        if original != pinned:
            return False, "original repo file changed on disk"
    except Exception as exc:
        return False, "could not verify original file: %s" % exc

    try:
        text = Path(scratch_path).read_text()
    except Exception as exc:
        return False, "scratch file unreadable: %s" % exc

    lines = text.splitlines()
    for i, line in enumerate(lines):
        if line.startswith("def %s(" % T5_FUNC):
            for j in range(i + 1, min(i + 4, len(lines))):
                stripped = lines[j].strip()
                if not stripped:
                    continue
                if stripped.startswith('"""') or stripped.startswith("'''"):
                    return True, "docstring found"
                return False, "next non-blank line is not a docstring: %r" % stripped
            return False, "function body not found after def line"
    return False, "def %s not found in scratch file" % T5_FUNC


CHECKERS = {
    "T1": check_t1,
    "T2": check_t2,
    "T3": check_t3,
    "T4": check_t4,
    "T5": check_t5,
}
