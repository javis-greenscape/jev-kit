# browse: one MCP tool over the Jev-decided browser agent

`browse` is a stdio MCP server with a single tool. A session hands it a goal
in plain words. Jev chooses each click and keystroke, and the page comes back
as JSON. The session pays for one tool call, not for a navigate, snapshot and
click loop of its own.

Jev is here to make browsing faster and cheaper. It is not a security control,
and nothing in this directory claims to be one.

It sits beside the Playwright MCP servers and removes none of them. Airlock's
`R11-browse-via-jev` rule denies a Playwright MCP browsing call and names this
tool. [docs/rules.md](../docs/rules.md#r11-browse-via-jev-a-playwright-mcp-call-is-pointed-at-browse)
has that half.

## Install

The server drives the pinned clone that [browser/](../browser/README.md)
makes, so that comes first.

```bash
browser/install.sh        # the jev-ultrafast clone, once
browse/install.sh         # checks the clone, runs a real handshake, prints the block
```

`install/install.sh --browse-mcp` runs the second one, and `--all` includes it.
It is not part of the default set.

Nothing here edits `~/.claude.json`. The installer prints a block like this
one for you to add under `mcpServers`, with the absolute path on your machine:

```json
{
  "mcpServers": {
    "browse": {
      "type": "stdio",
      "command": "/home/you/code/jev-kit/browse/server.py",
      "args": [],
      "env": {}
    }
  }
}
```

`install/doctor.sh` runs the server through `initialize` and `tools/list` over
a real pipe and reports PASS. With no clone it reports a skip.

## The tool

| Input | Type | |
|---|---|---|
| `goal` | string, required | What to do, in plain words. |
| `start_url` | string | Where to start. Optional when the goal contains an `http(s)` URL. |
| `extract` | string | A CSS selector. The text of every match comes back as `extracted`. |
| `screenshot` | boolean, default false | Write a PNG of the final page and return its path. |

The result is JSON text:

| Field | |
|---|---|
| `final_url`, `title` | Where the agent ended up. |
| `status` | `done` when Jev judged the goal met, `blocked` when it could not go on. |
| `steps` | How many decisions Jev made. |
| `elapsed_ms` | Wall time for the whole call, Chromium start included. |
| `text` | Visible text of the final page, trimmed to 8 KB. `text_truncated` is set when it was cut. |
| `extracted` | Text at `extract`, only when you gave one. |
| `screenshot_path` | Only when asked for. A PNG under `$XDG_STATE_HOME/jev-kit/browse/`, else `~/.local/state/jev-kit/browse/`. |

Only `http://` and `https://` start pages are accepted.

A real run on the development box, 2026-09-21, driven over stdio:

```text
goal:    "open https://example.com and report the main heading"
extract: "h1"
```

```json
{
  "final_url": "https://example.com/",
  "title": "Example Domain",
  "status": "done",
  "steps": 1,
  "elapsed_ms": 2946,
  "text": "Example Domain\n\nThis domain is for use in documentation examples without needing permission. Avoid use in operations.\n\nLearn more",
  "extracted": "Example Domain"
}
```

That is one run, so treat the 2.9 seconds as an example and not a benchmark.
It includes starting Chromium.

## How it runs

`server.py` is standard library only. It speaks JSON-RPC 2.0 over stdio:
`initialize`, `notifications/initialized`, `ping`, `tools/list`, `tools/call`.

The agent itself runs in a child process, `runner.py`, inside the clone's own
environment. It uses the clone's `.venv` when `uv sync` has made one, and `uv
run` when it has not. A separate process is what makes the timeout a hard one,
because the whole process group is killed when the time is up.

The server owns the Chromium lifecycle, and only its own.

- If you set `BU_CDP_URL` and something answers there, it attaches to that and
  never closes it. This is how you hand it a browser that is already logged
  in. Unset means unset: the old `http://127.0.0.1:9333` default is not probed
  any more, because whatever answers there is somebody else's browser unless
  you said otherwise.
- Otherwise it starts a headless Chromium of its own on a free port, with its
  own temporary profile. Every call this server process serves reuses it, and
  it closes when the server exits. If it has died since, the next call starts
  a fresh one and removes the dead one's profile directory.
- Other Chromiums on the box are ignored. A Playwright MCP browser, a second
  Claude session running its own copy of this server, an ordinary desktop
  Chrome: none of them is attached to, none is killed, and none stops a call.
  Until 2026-09-23 any one of them made every call fail, which taught agents
  to go back to Playwright.

Typing into a field needs a text model, which the agent reads from the clone's
`.env`. With none configured the runner falls back to the `claude-cli` adapter
that the patches add.

## When it fails

It fails closed with a message and it never hangs. Every failure below comes
back as an `isError` result, and the read loop keeps going.

| What went wrong | What the message names |
|---|---|
| No clone | `browser/install.sh` and `JEV_ULTRAFAST_DIR` |
| No TypeSafe key | `~/.config/jev-kit/env` |
| The call ran past `JEV_BROWSE_TIMEOUT` (default 90 s) | the timeout. The agent is killed, and a Chromium this server owns is restarted. |
| No Chromium binary anywhere | `npx playwright install chromium` and `JEV_BROWSE_CHROMIUM` |
| Chromium has no usable sandbox | `JEV_BROWSE_NO_SANDBOX`, see below |
| A bad argument | the argument |

### `blocked` hands the browser back

`status: blocked` means Jev gave up on the goal. It chooses one action at a
time, out of what it can see in the viewport, so a task that needs several
hops is beyond it. A goal written as explicit steps gets further than a goal
written as an outcome. "Open the article, click the link to X, then click the
link to Y, scroll if the link is not in view" is the shape that works.

When it is beyond `browse` anyway, airlock notices. A PostToolUse hook,
`hooks/airlock_browse_unlock.py`, records a `blocked` result and an errored
call alike, and `R11-browse-via-jev` then warns instead of denying Playwright
MCP for the next thirty minutes of that session. There is nothing to do by
hand: try `browse` first, and a failure hands the browser back.
[docs/rules.md](../docs/rules.md#when-browse-gives-up-r11-stands-aside) has
the detail, including what a subagent shares with its parent.

Key resolution is the same as everywhere else in the kit, and the order is
written down once, in the module docstring of `airlock/keyfile.py`. The child
gets the key in its environment and never on a command line. It is scrubbed
from anything the tool returns.

## The sandbox

Chromium is started with its sandbox on. On Ubuntu 23.10 and later, AppArmor
denies user namespaces to a binary that has no profile. A Chromium from the
Playwright cache is such a binary, so there it refuses to start.

The server does not work round that for you. The error says what happened, and
`JEV_BROWSE_NO_SANDBOX=1` in the server's `env` block starts Chromium with
`--no-sandbox`. That means a page which exploits the renderer is no longer
contained. Playwright makes the same trade by default when it launches
Chromium itself, so this is no worse than the Playwright MCP server beside it.
It is still your decision and it is never the default. The better fix is an
AppArmor profile for the binary.

## Settings

| Variable | Default | |
|---|---|---|
| `JEV_ULTRAFAST_DIR` | `~/code/jev-ultrafast` | The clone. `AIRLOCK_BROWSER_DIR`, which `browser/install.sh` reads, is honoured after it. |
| `BU_CDP_URL` | unset | A Chromium to attach to instead of starting one. Only an explicit value counts. |
| `JEV_BROWSE_TIMEOUT` | `90` | Seconds allowed for a call. |
| `JEV_BROWSE_CHROMIUM` | newest in the Playwright cache, then `PATH` | The binary to start. |
| `JEV_BROWSE_NO_SANDBOX` | unset | `1` adds `--no-sandbox`. Read the section above first. |

## What leaves the machine

The same as the browser agent. The goal and the observed page state go to
TypeSafe for each decision. Field text goes to whichever text model the clone
is configured with. The page text and the screenshot stay on the machine.

Native Windows is not supported, because the browser agent is not ported
there.

## Tests

`tests/test_browse.py` covers the framing, the handshake, the schema and every
failure in the table above. The jev-ultrafast call is mocked. No test touches
the network or starts a browser.
