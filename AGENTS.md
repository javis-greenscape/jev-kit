# AGENTS.md

**Installing this?** Read **"Install (for a person or an agent)"** at the top
of [README.md](README.md) and follow it. It has the clone command, the one
install command, the single thing to ask the human for (a `TYPESAFE_API_KEY`,
which you must never print), how to verify, how to switch modes, the kill
switch and rollback.

**On native Windows (no WSL)**, follow
[docs/INSTALL-WINDOWS.md](docs/INSTALL-WINDOWS.md) instead: the installer is
Python rather than bash, the warm daemon does not exist there, and `es.exe`
(voidtools Everything) replaces `plocate` for file search -- detect it, never
install it.

**Do not** turn on compaction, or arm `enforce` on a machine that is not
yours, without asking first. The README's "If you are an AI agent" paragraph
lists the rest.

**Working on the code?** `python3 -m unittest discover -s tests` must pass
(659 tests on Linux; the same suite on Windows Python
skips 84 POSIX-only tests and passes the rest). The rules table is `airlock/rules.py`; everything fails open.
