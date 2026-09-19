# CLAUDE.md

**Installing this?** Read **"Install (for a person or an agent)"** at the top
of [README.md](README.md) and follow it: the clone command, the one install
command, the single thing to ask the human for (a `TYPESAFE_API_KEY`, never
to be printed), how to verify, how to switch modes, the kill switch and
rollback.

**Do not** turn on compaction, or arm `enforce` on a machine that is not
yours, without asking first. See the README's "If you are an AI agent"
paragraph.

**Working on the code?** `python3 -m unittest discover -s tests` must pass
(500 tests). The rules table is `airlock/rules.py`; everything fails open.

## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

Rules:
- For codebase questions, first run `graphify query "<question>"` when graphify-out/graph.json exists. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).
