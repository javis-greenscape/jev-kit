# AGENTS.md

**Installing this?** Read [docs/install.md](docs/install.md), especially its
**"If you are an AI agent"** section. It has the clone command, the one install
command, and the single thing to ask the human for: a `TYPESAFE_API_KEY`, which
you must never print. It also covers how to verify with a real deny, how to
switch modes, the kill switch and rollback. The three-command version is the
[Quickstart](README.md#quickstart) in the README.

**On native Windows (no WSL)**, follow
[docs/INSTALL-WINDOWS.md](docs/INSTALL-WINDOWS.md) instead: the installer is
Python rather than bash, the warm daemon does not exist there, and `es.exe`
(voidtools Everything) replaces `plocate` for file search. Detect it, never
install it.

**Do not** turn on compaction, or arm `enforce` on a machine that is not
yours, without asking first. `docs/install.md` lists the rest.

**Orienting?** [README.md](README.md) is the two-minute version. The kit is
jev-kit; **Airlock is the tool-call guard**, one component of it. Per-component
detail is [docs/components.md](docs/components.md), the rules table is
[docs/rules.md](docs/rules.md), and every measured number with its method is
[docs/measurements.md](docs/measurements.md).

**Working on the code?** Three checks have to pass.

- `python3 -m unittest discover -s tests`. 1072 tests on Linux. The same suite
  on native Windows Python skips the POSIX-only ones and passes the rest, and
  no test requires Windows to pass.
- `python3 tools/check_docs.py`. It resolves every relative link in the README
  and `docs/`, and checks every Mermaid block.
- `python3 tools/check_prose.py README.md`. It flags machine-writing phrases,
  em dashes, long sentences, flat rhythm and walls of text. `--fix-hints`
  prints a plainer form where a mechanical one exists.

The rules table is `airlock/rules.py`. Everything fails open, and R6 is `off`
by default on every platform: a headless machine turns it on in `rules.json`,
`airlock/headless.py` detects one, and `install/install.sh` writes the entry.

The key-file resolution order is written down in exactly one place, the module
docstring of `airlock/keyfile.py`. The pointer-file trust checks, and what
Windows cannot check, are in the same docstring.
