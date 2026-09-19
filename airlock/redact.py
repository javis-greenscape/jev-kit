"""Redaction: strip token-like strings before anything leaves the box or
reaches the shadow log.

Applied to every field of the state we send to Jev, and to every field we
write to the log, before either happens. Order matters only in that the
generic hex/base64 sweeps run last so they cannot eat a piece of an
already-specific match first (both outcomes redact the same substring, so in
practice order does not change the result, only readability of this file).
"""
import re

REDACTED = "[REDACTED]"

PROMPT_TRUNCATE = 4000
COMMAND_TRUNCATE = 2000

_PATTERNS = [
    # TypeSafe / vendor-style API keys
    re.compile(r"apikey_[A-Za-z0-9_]+"),
    # OpenAI-style secret keys
    re.compile(r"sk-[A-Za-z0-9_\-]{10,}"),
    # GitHub personal access tokens
    re.compile(r"ghp_[A-Za-z0-9]{20,}"),
    # Slack tokens: xoxb-, xoxp-, xoxa-, xoxr-, xoxs- ...
    re.compile(r"xox[a-zA-Z]-[A-Za-z0-9\-]+"),
    # JWTs: eyJ... . eyJ... . signature
    re.compile(r"eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+"),
    # Bearer <token>
    re.compile(r"Bearer\s+\S+"),
    # password=<value> or --password <value> (case-insensitive)
    re.compile(r"(?i)(?:--)?password[= ]+\S+"),
    # <ANYTHING>SECRET|TOKEN|PASSWORD|API_KEY<ANYTHING>=<value>
    re.compile(r"(?i)[A-Za-z0-9_]*(?:SECRET|TOKEN|PASSWORD|API_KEY)[A-Za-z0-9_]*=\S+"),
    # Bare 32+ char hex run (not already part of a matched token above)
    re.compile(r"(?<![A-Za-z0-9])[A-Fa-f0-9]{32,}(?![A-Za-z0-9])"),
    # Bare 32+ char base64-ish run
    re.compile(r"(?<![A-Za-z0-9+/=])[A-Za-z0-9+/]{32,}={0,2}(?![A-Za-z0-9+/=])"),
]


def redact(text):
    """Replace every token-like substring in text with [REDACTED]. Never raises."""
    if not text:
        return text
    try:
        out = str(text)
        for pattern in _PATTERNS:
            out = pattern.sub(REDACTED, out)
        return out
    except Exception:
        # Fail safe toward over-redaction, never toward leaking the input as-is.
        return REDACTED


def redact_and_truncate_prompt(text):
    return redact(text)[:PROMPT_TRUNCATE]


def redact_and_truncate_command(text):
    return redact(text)[:COMMAND_TRUNCATE]
