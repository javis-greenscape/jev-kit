# CLAUDE.md

**Installing this?** Read [docs/install.md](docs/install.md), especially its
**"If you are an AI agent"** section: the clone command, the one install
command, the single thing to ask the human for (a `TYPESAFE_API_KEY`, never to
be printed), how to verify with a real deny, how to switch modes, the kill
switch and rollback. The three-command version is the
[Quickstart](README.md#quickstart) in the README.

**Do not** turn on compaction, or arm `enforce` on a machine that is not
yours, without asking first. `docs/install.md` lists the rest.

**Orienting?** [README.md](README.md) is the two-minute version. The kit is
jev-kit; **Airlock is the tool-call guard**, one component of it. Per-component
detail is [docs/components.md](docs/components.md), the rules table is
[docs/rules.md](docs/rules.md), and every measured number with its method is
[docs/measurements.md](docs/measurements.md).

**Working on the code?** `python3 -m unittest discover -s tests` must pass
(573 tests). `python3 tools/check_docs.py` must pass too: it resolves every
relative link in the README and `docs/`, and checks every Mermaid block. The
rules table is `airlock/rules.py`; everything fails open. The key-file
resolution order is written down in exactly one place, the module docstring of
`airlock/keyfile.py`.

## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

Rules:
- For codebase questions, first run `graphify query "<question>"` when graphify-out/graph.json exists. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).
