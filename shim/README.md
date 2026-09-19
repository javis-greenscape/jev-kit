<!--
Copied in from the claude-cli-shim checkout so airlock carries one way to
run PageIndex's local indexing on a Claude subscription rather than an API
key. The source checkout is read-only as far as this repo is concerned.
Absolute home paths and the Claude account directory name were replaced with
$HOME and a `~/.claude` default; nothing else was changed.
-->

# claude-cli-shim

> **Experimental.** Tested: PageIndex local-mode indexing, by hand, once, on one
> machine (see [Measured numbers](#measured-numbers-2026-09-19-this-box) for the
> document and the box). Not tested: anything else. There are **no unit tests for
> this component at all**, no labelled cases, and chat through the shim is known
> not to work (see [Limits](#limits)). Treat it as a working experiment, not a
> supported component.

An OpenAI-compatible `/v1/chat/completions` + `/v1/models` HTTP shim, backed
by standing `claude -p` CLI child processes, so that OpenAI-shaped clients
(PageIndex, LiteLLM's `openai/` provider, anything speaking the
`OPENAI_BASE_URL` + `OPENAI_API_KEY` convention) can drive a **Claude
subscription login** instead of an Anthropic/OpenAI API key.

It exists to answer one question: can PageIndex's LOCAL mode (indexing and
chat) run on this box using the `claude` CLI's OAuth login
(`CLAUDE_CONFIG_DIR=~/.claude-<account>`) with no Anthropic/OpenAI/OpenRouter API
key present. Short answer: **indexing yes, chat no**. See Limits.

## What it is

`shim.py` is stdlib-only Python. It listens on `127.0.0.1` only and holds a
small pool (default 2) of long-lived `claude -p --input-format stream-json
--output-format stream-json --safe-mode --no-session-persistence --tools ""`
child processes per model (`haiku`, `sonnet`), modeled on the standing-child
pattern in `jev-ultrafast/jev_ultrafast/text_model_claude_standing.py` on
branch `claude-text-model`, read only and not modified. Each HTTP request:

1. flattens the OpenAI `messages` array into one prompt,
2. sends it as one stdin line to a pooled child, reads the matching
   `{"type":"result"}` event from the child's stdout,
3. wraps the result in an OpenAI `chat.completion` JSON shape, with
   `usage.prompt_tokens`/`completion_tokens` taken from the CLI's own
   reported `usage.input_tokens`/`output_tokens` when present.

`response_format: {"type": "json_object"}` is honored by appending an
instruction to respond with bare JSON and validating with `json.loads`;
on failure it retries once with the invalid output shown back to the model.
No true JSON mode or grammar constraint exists on this path. It is
instruction plus validation only.

Children are recycled after 40 requests each (`CLAUDE_SHIM_RECYCLE_AFTER`)
and torn down on `SIGINT`/`SIGTERM`/process exit via `atexit`.

## How to run

```
CLAUDE_CONFIG_DIR=$HOME/.claude-<account> python3 shim.py
```

Runs in the foreground, logs to stdout. Env vars:

- `CLAUDE_CONFIG_DIR` - which account's OAuth login to spend (default
  `~/.claude`; a box with several accounts sets its own value in
  `install/config.env`).
- `CLAUDE_SHIM_PORT`: default `8931`.
- `CLAUDE_SHIM_POOL_SIZE`: children per model, default `2`.
- `CLAUDE_SHIM_TIMEOUT`: per-request timeout seconds, default `60`.
- `CLAUDE_SHIM_RECYCLE_AFTER`: requests before a child is retired and
  replaced, default `40`.

Point a client at it:

```
export OPENAI_BASE_URL=http://127.0.0.1:8931/v1
export OPENAI_API_KEY=dummy-not-checked   # any non-empty string; never validated
```

Model names must contain `haiku` or `sonnet`, matched as a substring, and
anything else gets a 400. `GET /v1/models` lists the two served ids.

Streaming (`"stream": true`) is refused with a 400. PageIndex's local indexing
lane never sets it, in `pageindex/utils.py::llm_completion` or
`llm_acompletion`, so it was out of scope for this pass. See Limits.

## Measured numbers (2026-09-19, this box)

Test document: the 9-page Docling paper, `arxiv.org/pdf/2408.09869`,
indexed with `PageIndexClient(index={"model": "haiku", "storage_path": ...},
chat="sonnet")` (flash mode, the local-mode default).

- **Indexing**: completed. Wall time 38.2s end to end (`submit_document`,
  includes PDF parsing + all LLM calls). 7 of the run's LLM calls went
  through the shim for this document (per-node summaries + doc description;
  flash mode's tree structure itself is layout-derived, not LLM-derived, as
  Step 1 findings below record).
- **Overall shim stats across the whole session** (3 manual smoke-test
  calls + 7 indexing calls + 1 chat call = 11 calls): 87.3s total latency,
  mean **7.9s/call**. This mixes a mostly-cold pool with real page-text
  prompts; the three trivial smoke-test calls alone ran in 0.9–1.7s each,
  so per-call latency scales with prompt size (full page text goes into
  the summary/description prompts), not a fixed CLI floor.
- **Tree excerpt** (first two nodes, `get_tree(node_summary=True)`):
  ```
  Introduction (page 1): "Docling is an MIT-licensed open-source Python
  package for efficient PDF document conversion that uses specialized AI
  models (DocLayNet and TableFormer) for layout analysis and table
  recognition. ..."
  Getting Started (page 2): "Docling is a PDF processing tool that converts
  documents to JSON or Markdown format with fast, stable performance. ..."
  ```
  Full tree saved during the test run to `/tmp/pageindex-test/tree_full.json`
  (scratch, not part of this repo).
- **Chat**: asked "What tool does Docling use for table structure
  recognition, and which section discusses it?" via `client.chat(...,
  doc_id=...)` with `chat="sonnet"`. Returned an answer in 12.5s, but the
  model **did not retrieve the document**. It noticed the system prompt
  described PageIndex tools (`browse_documents`, `get_document_structure`,
  `get_page_content`) that this shim never wired up, said so explicitly,
  and then answered from general training knowledge ("Based on general
  knowledge (not the document itself): ... TableFormer ..."), correctly
  guessing the tool name but **not citing a real section** because it
  never read the document. This is a wrong answer for the intended use
  case rather than a crash. The shim returned 200 with real completion
  content, produced by an agent that had no tools.

## Limits

- **Chat does not work.** PageIndex's local chat lane
  (`pageindex/local_chat.py`) drives an **OpenAI Agents SDK agent loop**
  with real function/tool calling (`build_openai_tools` registers
  `browse_documents`/`get_document_structure`/`get_page_content` etc. as
  OpenAI tool schemas, and the `Runner` executes a multi-turn tool-call
  loop against them). This shim implements only plain single-turn
  completions. It does not parse the `tools` field of the request, it does
  not emit `tool_calls` in the response, and the underlying `claude -p
  --tools ""` child has tool use disabled entirely.

  A client that expects document retrieval via tool calls gets an ungrounded
  answer rather than an error, which is worse than a clean failure. Do not
  use this shim for the chat lane until tool calling is implemented.

  Making it work would need three things. Parsing `tools`/`tool_choice` from
  the request. Running the `claude -p` child *with* tools enabled, or
  emulating function calling by instructing the model to emit a structured
  call, parsing it out, and driving PageIndex's own tool implementations
  locally. And returning OpenAI `tool_calls` in the response so the Agents
  SDK's loop can execute them. That is materially more work than the indexing
  lane and was out of this pass's scope.
- **No streaming.** Not needed for indexing. `llm_completion` and
  `llm_acompletion` in `pageindex/utils.py` never pass `stream=True`. The
  chat lane may want it, which is moot until tool calling exists.
- **No vision.** Not needed. PageIndex local indexing, flash and standard
  modes alike, works from extracted PDF text and never page images, so this
  was never implemented.
- **JSON mode is instruction + validation, not a real constraint.** A
  model that ignores the instruction gets one retry, then whatever it
  produced is returned verbatim even if it is not valid JSON. PageIndex's
  own indexing prompts do not use `response_format` at all (they use plain
  completions and their own ```` ```json ```` fence parsing), so this path
  was exercised only by direct testing, not by the indexing lane itself.
- **Every call spends the Claude subscription of the account in
  `CLAUDE_CONFIG_DIR`** (default `~/.claude`, per this box's
  conventions). There is no metering here beyond `/healthz`'s call count
  and cumulative latency — treat volume accordingly. A full-document index
  of a 9-page PDF cost 7 LLM calls; call count scales with the number of
  tree nodes (roughly section count) plus one description call, not with
  page count directly.
- Not installed as a service; run it manually and stop it (Ctrl-C /
  `tmux kill-session`) when done. No auth on the HTTP endpoint beyond
  binding to loopback only. Do not expose this port beyond 127.0.0.1.

## Step 1 findings (PageIndex source, v0.2.18)

Indexing lane, `pageindex/local_api.py`, default `mode="flash"`.
Tree/structure extraction is **layout-based, not LLM-based**: it runs
pdfium/pymupdf heuristics in `pageindex/flash/main.py::extract_toc`, with zero
LLM calls.

LLM calls happen for three things only. Per-node summaries
(`flash/api.py::_summarize`). The tree "optimize" pass, where merge is
deterministic but "expand" does call the model in `optimize="full"`, the
local-mode default, at `pageindex/tree_optimize.py`. And one document
description call.

All of these go through `pageindex/utils.py::llm_completion` and
`llm_acompletion`, which call `litellm.completion`/`acompletion` with a
**plain single-turn (or short-history) prompt, no `response_format`, no
`tools`, no `stream=True`, no vision content**.

JSON responses are parsed by hand out of a ```` ```json ```` fence, by
`get_json_content`/`extract_json` in the same file. `mode="standard"`
(`page_index_classic.py`) uses the same `llm_completion` primitive, so the
same constraints hold there. That is exactly what let a minimal
non-streaming, non-tool-calling shim work for indexing.

Chat lane (`pageindex/local_chat.py`): built on the **OpenAI Agents SDK**
(`agents.Agent`/`agents.Runner`) with a `LitellmModel` or
`OpenAIResponsesModel` backend, and `build_openai_tools` in
`pageindex/integrations/openai_agents.py` registers real tool schemas for
document browsing. This is an actual multi-turn tool-calling agent loop,
not a single completion. The direct test above confirms it: without tool
calling the model cannot retrieve the document and free-associates instead.

Checked `litellm.provider_list` and `litellm.custom_provider_map` directly
(litellm 1.97+ as pinned by pageindex): no provider drives the `claude` CLI
or the Claude Agent SDK. The only agent-shaped entries are `a2a_agent` and
`litellm_agent`, generic agent-protocol providers unrelated to Claude Code.
PageIndex's own docs describe the OpenAI-compatible-endpoint route
(`OPENAI_API_KEY` + `OPENAI_BASE_URL`) as the way to point a non-LiteLLM-native
model at a client-run server. This shim is exactly that seam.

## Retrieval without PageIndex chat (tested 2026-09-19)

PageIndex's chat lane needs tool calling, which this shim does not provide. The combination that works is: index through the shim, then let Claude Code do the retrieval itself over the saved tree.

Test: `claude -p --model sonnet --effort low --safe-mode --allowedTools Read`, run in the directory holding `tree_full.json` and `paper.pdf`. The prompt is in `retrieval-prompt.md`: read the tree, pick nodes from titles and summaries, read only those PDF pages, quote and cite, refuse when the pages do not contain the answer. Three questions on the 9-page Docling report, 14.1 s wall for all three:

| Question | Result | Checked against |
|---|---|---|
| Table structure model and section | TableFormer, section 3.2, page 3, verbatim quote | Docling Markdown of the same PDF |
| M3 Max, 16 threads, pypdfium total time | 92 s, Table 1, page 5 | Table row "103 s 92 s" in the Docling Markdown |
| GPU memory of the VLM pipeline (not in the paper) | "NOT FOUND IN DOCUMENT", no answer from general knowledge | The paper has no VLM pipeline |

Limits: one small document with a coarse 6-node tree. On the unanswerable question the model read pages 2 to 5, most of the document, before refusing. That would be expensive on a long PDF, so cap pages per question in code rather than in the prompt. Not yet tried on a long document, which is the case PageIndex is meant for.

## Long-document test (2026-09-19): a vendor product manual

Source was a 1.8 MB HTML file (about 39,000 words, 251 headings). PageIndex local mode refuses anything but PDF ("only PDF files are supported in local mode"), so the HTML was printed to a 115-page A4 PDF with headless Playwright Chromium first.

| Step | Result |
|---|---|
| Indexing through this shim, `index=haiku` | 574 s wall, 139 model calls, one "Retrying LLM completion" warning, tree of 143 nodes (depth 2), 192 KB of JSON |
| Retrieval by Claude Code (`sonnet`, low effort, Read tool only, prompt in `retrieval-prompt.md` with a 6-page cap per question) | 4 of 4 correct, 74 s wall, 19 turns, 3 PDF pages read in total |

Four questions, each checked against a Docling Markdown conversion of the same HTML. Three were answerable from a single page deep in the document, at pages 78, 75 and 27. The fourth asked about a feature the product does not have, and was correctly refused from the tree alone without reading any page.

Cost note: the CLI reported `total_cost_usd` 0.52 for the retrieval session with 1.1 M cache-read tokens, because the 192 KB tree sits in context for all 19 turns. For routine use, give the model a titles-only tree first and fetch summaries only for candidate nodes. Indexing cost 139 Haiku calls on the subscription and is paid once per document revision.

Running the shim: it exited immediately when started as a detached tmux command with stdin closed; it ran correctly as an ordinary background process. Not investigated.
