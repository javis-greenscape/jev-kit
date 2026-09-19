"""Spike adapter: TYPE_TEXT via the local `claude` CLI (OAuth) instead of an OpenAI-compatible API.

This box has no Anthropic or OpenRouter API key. Claude access here is OAuth through the
`claude` CLI only. This adapter shells out to it, matching the exact (value, helper) return
shape of jev_ultrafast.model.field_text, so it can be swapped in without touching agent.py.

Not for production use as written: it pays ~1-3s of CLI process startup per call (measured
2026-09-19, see SPIKE-NOTES.md), and stdout occasionally wraps JSON in a markdown code fence,
which is stripped defensively below.
"""

import json
import os
import re
import subprocess
import time

from .questions import TEXT_VALUE

CLAUDE_CONFIG_DIR = os.environ.get("CLAUDE_TEXT_MODEL_CONFIG_DIR", os.path.expanduser("~/.claude"))
MODEL = os.environ.get("CLAUDE_TEXT_MODEL", "haiku")

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def field_text(context):
    """Same contract as jev_ultrafast.model.field_text: (value, helper_dict) or raises ValueError."""
    prompt = (
        TEXT_VALUE
        + "\n\nRespond with ONLY the JSON object, no markdown fence, no commentary.\n\n"
        + json.dumps(context)
    )
    started = time.perf_counter()
    try:
        result = subprocess.run(
            ["claude", "-p", "--model", MODEL, prompt],
            input="",
            capture_output=True,
            text=True,
            timeout=30,
            env={**os.environ, "CLAUDE_CONFIG_DIR": CLAUDE_CONFIG_DIR},
        )
    except (subprocess.SubprocessError, OSError) as exc:
        raise RuntimeError(f"claude CLI invocation failed: {exc}") from None
    if result.returncode != 0:
        raise RuntimeError(f"claude CLI exited {result.returncode}: {result.stderr[:300]}")
    raw = _FENCE.sub("", result.stdout.strip()).strip()
    try:
        output = json.loads(raw)
        value = output["text"]
        if set(output) != {"text"} or not isinstance(value, str) or not value.strip() or len(value) > 2000:
            raise ValueError()
    except (ValueError, KeyError, TypeError, json.JSONDecodeError):
        raise ValueError("Text helper returned no valid field value; nothing typed.") from None
    return value, {
        "model": f"claude-cli:{MODEL}",
        "latency_ms": round((time.perf_counter() - started) * 1000),
        "usage": {},
    }
