"""logtriage: label log lines, redact-first, local rules before the model.

Ported in design from reachjalil/jevlogs (docs/community-vetting.md, item 9),
whose privacy architecture the vetting report called "the template we should be
copying". Three properties, in this order, and the order is the whole point:

  1. REDACT FIRST. Nothing downstream -- a local rule, a cache key, the model
     state, the emitted JSON -- ever sees the raw line. Not "we remember to
     redact before the API call"; the raw text does not survive past the first
     step at all.
  2. LOCAL RULES SECOND. A regex settles the routine cases for nothing: a
     health check, a 200, a debug line. Only what no local rule settles is
     worth a model call.
  3. THE MODEL LAST, on what is left.

Plus jevlogs' `protected` idea: a line matching a protected pattern is never
sent anywhere at all, whatever the rules say about it.
"""
from .triage import (  # noqa: F401
    LABELS,
    Triager,
    load_config,
    triage_line,
)
