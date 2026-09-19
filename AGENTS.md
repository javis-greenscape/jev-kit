# AGENTS.md

**Installing this?** Read [docs/install.md](docs/install.md), especially its
**"If you are an AI agent"** section. It has the clone command, the one install
command, the single thing to ask the human for (a `TYPESAFE_API_KEY`, which you
must never print), how to verify with a real deny, how to switch modes, the
kill switch and rollback. The three-command version is the
[Quickstart](README.md#quickstart) in the README.

**On native Windows (no WSL)**, follow
[docs/INSTALL-WINDOWS.md](docs/INSTALL-WINDOWS.md) instead: the installer is
Python rather than bash, the warm daemon does not exist there, and `es.exe`
(voidtools Everything) replaces `plocate` for file search throughout --
detect it, never install it. **Under WSL**, both indexes are in play at
once: `plocate` still covers `$HOME` on the Linux side, but a root under
`/mnt/<drive>` is on the Windows host and needs `es` (the same Everything
client, reached under its bare name on PATH there) instead.

**Do not** turn on compaction, or arm `enforce` on a machine that is not
yours, without asking first. `docs/install.md` lists the rest.

**Orienting?** [README.md](README.md) is the two-minute version. The kit is
jev-kit; **Airlock is the tool-call guard**, one component of it. Per-component
detail is [docs/components.md](docs/components.md), the rules table is
[docs/rules.md](docs/rules.md), and every measured number with its method is
[docs/measurements.md](docs/measurements.md).

**Working on the code?** `python3 -m unittest discover -s tests` must pass
(999 tests on Linux; the same suite on native Windows Python skips the
POSIX-only ones and passes the rest, and no test requires Windows to pass).
`python3 tools/check_docs.py` must pass too: it resolves every relative link
in the README and `docs/`, and checks every Mermaid block. The rules table is
`airlock/rules.py`; everything fails open, and R6 is `off` by default on every
platform (a headless machine turns it on in `rules.json`; `airlock/headless.py`
detects one and `install/install.sh` writes the entry). The key-file resolution order
is written down in exactly one place, the module docstring of
`airlock/keyfile.py`; the pointer-file trust checks and what Windows cannot
check are in the same docstring.
