#!/usr/bin/env python3
"""A stdio MCP server with one tool, `browse`.

`browse` hands a goal to the Jev-decided browser agent (the jev-ultrafast
clone that browser/install.sh pins) and returns what the page says afterwards.
Jev chooses each step, so the calling session pays for one tool call rather
than a navigate / snapshot / click loop of its own. That is the whole point of
it: faster and cheaper browsing. It is not a security control.

The transport is MCP over stdio: JSON-RPC 2.0, one message per line, and the
methods a client needs for one tool (`initialize`, `notifications/initialized`,
`ping`, `tools/list`, `tools/call`). Standard library only, because this file
is launched by whatever `python3` the MCP client finds. The agent itself runs
in a child process inside the clone's own environment (browse/runner.py), which
is also what makes the per-call timeout a hard one: the child's whole process
group is killed when the time is up.

This process owns the Chromium lifecycle, and only its own:

  * If `BU_CDP_URL` is set in the environment and answers, that browser is
    used as it is and never closed from here. Somebody chose it deliberately.
  * Otherwise a headless Chromium of this server's own is started on a free
    port with its own temporary profile, reused by every call this process
    serves, and closed when the process exits. If it has died since, a fresh
    one is started and the dead one's profile directory is removed, so
    neither browsers nor profiles pile up.
  * Other Chromiums on the box are ignored. A Playwright MCP browser, another
    Claude session's own `browse` server, an ordinary desktop Chrome: this
    process never attaches to one, never kills one, and never refuses to work
    because one exists. The default `http://127.0.0.1:9333` is not probed for
    the same reason: whatever answers there is somebody else's browser unless
    a person said otherwise by setting `BU_CDP_URL`.

Everything fails closed with a message and never hangs. A bad request, a
missing clone, a missing key, a timeout and a crash in the agent all come back
as an `isError` result. Nothing here raises out of the read loop.

The TypeSafe key is resolved exactly as the rest of the kit resolves it
(airlock/keyfile.py, whose module docstring is where the order is written
down). It reaches the child in its environment, never on a command line, and
it is scrubbed from any text this server returns.

Environment:
  JEV_ULTRAFAST_DIR    the clone (then AIRLOCK_BROWSER_DIR, which is what
                       browser/install.sh reads; default ~/code/jev-ultrafast)
  BU_CDP_URL           a Chromium to attach to instead of starting one. Only
                       an explicitly set value is honoured; unset means start
                       our own, never probe the old default
  JEV_BROWSE_TIMEOUT   seconds allowed per call (default 90)
  JEV_BROWSE_CHROMIUM  the Chromium binary to start, if the Playwright cache
                       and PATH are not where it lives
  JEV_BROWSE_NO_SANDBOX  set to 1 to start Chromium with --no-sandbox. Never
                       the default and never a silent fallback: on a distro
                       whose AppArmor denies user namespaces to an unprofiled
                       binary (Ubuntu 23.10+), Chromium refuses to start, the
                       error says so, and a person decides
"""
import glob
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from airlock import keyfile  # noqa: E402

SERVER_NAME = "jev-kit-browse"
SERVER_VERSION = "0.1.0"
PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")

RUNNER = Path(__file__).resolve().with_name("runner.py")
DEFAULT_TIMEOUT_S = 90.0
CHROMIUM_START_S = 15.0
TEXT_LIMIT_BYTES = 8 * 1024

TOOL = {
    "name": "browse",
    "description": (
        "Browse the web with the Jev-decided browser agent. Give it a goal in "
        "plain words; Jev chooses each click and keystroke, then the page is "
        "read back. Returns JSON: final_url, title, status, steps, elapsed_ms, "
        "text (visible page text, trimmed to 8 KB), plus extracted and "
        "screenshot_path when asked for. Use this instead of driving "
        "Playwright MCP tools step by step. Example: goal=\"open "
        "https://example.com and report the main heading\", extract=\"h1\"."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "goal": {
                "type": "string",
                "description": "What to do on the site, in plain words.",
            },
            "start_url": {
                "type": "string",
                "description": "Where to start. Optional when the goal contains a URL.",
            },
            "extract": {
                "type": "string",
                "description": "A CSS selector whose text is returned as `extracted`, "
                               "in addition to the page text.",
            },
            "screenshot": {
                "type": "boolean",
                "default": False,
                "description": "Write a PNG of the final page and return its path.",
            },
        },
        "required": ["goal"],
        "additionalProperties": False,
    },
}


class BrowseError(Exception):
    """A failure with a message fit to hand back to the caller."""


class BrowseTimeout(BrowseError):
    """The per-call time ran out and the agent was killed."""


def log(message):
    # stdout belongs to the protocol. Everything else goes to stderr.
    try:
        sys.stderr.write("browse: %s\n" % message)
        sys.stderr.flush()
    except Exception:
        pass


# --- configuration -----------------------------------------------------------

def clone_dir():
    for var in ("JEV_ULTRAFAST_DIR", "AIRLOCK_BROWSER_DIR"):
        value = os.environ.get(var)
        if value:
            return Path(os.path.expanduser(value))
    return Path.home() / "code" / "jev-ultrafast"


def resolve_clone():
    clone = clone_dir()
    if not (clone / "jev_ultrafast" / "agent.py").is_file():
        raise BrowseError(
            "the jev-ultrafast clone was not found at %s. Run browser/install.sh "
            "in the jev-kit checkout (%s), or point JEV_ULTRAFAST_DIR at the clone."
            % (clone, REPO_ROOT)
        )
    return clone


def resolve_key():
    try:
        key = keyfile.get_api_key()
    except Exception:
        key = None
    if not key:
        raise BrowseError(
            "no TypeSafe key. Put a TYPESAFE_API_KEY=... line in ~/.config/jev-kit/env "
            "(mode 600), or set TYPESAFE_API_KEY in this server's environment. "
            "Jev decides every step, so nothing can run without it."
        )
    return key


def call_timeout_s():
    try:
        value = float(os.environ.get("JEV_BROWSE_TIMEOUT", ""))
    except ValueError:
        return DEFAULT_TIMEOUT_S
    return value if value > 0 else DEFAULT_TIMEOUT_S


def state_dir():
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(os.path.expanduser(base)) / "jev-kit" / "browse"


def runner_command(clone):
    """The clone's own interpreter when `uv sync` has made one, else `uv run`.
    The venv is preferred because it starts faster and never touches the
    network; `uv run` is what makes a clone that was never synced work."""
    for rel in (".venv/bin/python", ".venv/Scripts/python.exe"):
        python = clone / rel
        if python.is_file():
            return [str(python), str(RUNNER)]
    uv = shutil.which("uv")
    if uv:
        return [uv, "run", "--project", str(clone), "python", str(RUNNER)]
    raise BrowseError(
        "%s has no .venv and `uv` is not on PATH. Run `uv sync` in the clone, "
        "or install uv (https://docs.astral.sh/uv/)." % clone
    )


# --- the child process -------------------------------------------------------

def _kill_group(proc):
    try:
        if hasattr(os, "killpg"):
            os.killpg(proc.pid, signal.SIGKILL)
        else:
            proc.kill()
    except Exception:
        pass
    try:
        # communicate, not wait: it reaps the child and closes our pipe ends.
        proc.communicate(timeout=5)
    except Exception:
        pass


def run_runner(request, env, clone, timeout_s):
    """Run browse/runner.py inside the clone and return the dict it prints.
    Raises BrowseError on a timeout, a crash, or output that is not JSON."""
    try:
        proc = subprocess.Popen(
            runner_command(clone), cwd=str(clone), env=env,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, start_new_session=True,
        )
    except OSError as exc:
        raise BrowseError("could not start the browser agent: %s" % exc)
    try:
        out, err = proc.communicate(json.dumps(request), timeout=timeout_s)
    except subprocess.TimeoutExpired:
        _kill_group(proc)
        raise BrowseTimeout(
            "timed out after %.0f s (JEV_BROWSE_TIMEOUT). The agent was stopped; "
            "try a narrower goal or a start_url closer to it." % timeout_s
        )
    except BaseException:
        # A signal arriving mid-call must not leave the agent running.
        _kill_group(proc)
        raise
    try:
        result = json.loads(out.strip().splitlines()[-1])
        if not isinstance(result, dict):
            raise ValueError("not an object")
    except Exception:
        tail = (err or out or "").strip().splitlines()[-3:]
        raise BrowseError(
            "the browser agent exited with status %s and no result. %s"
            % (proc.returncode, " | ".join(tail))
        )
    if result.get("error"):
        raise BrowseError(str(result["error"]))
    return result


# --- Chromium ----------------------------------------------------------------

def cdp_answers(url, timeout_s=2.0):
    try:
        with urllib.request.urlopen(url.rstrip("/") + "/json/version", timeout=timeout_s) as r:
            return r.status == 200
    except Exception:
        return False


def _revision(path):
    m = re.search(r"chromium-(\d+)", path)
    return int(m.group(1)) if m else 0


def find_chromium():
    explicit = os.environ.get("JEV_BROWSE_CHROMIUM")
    if explicit:
        return os.path.expanduser(explicit)
    roots = [os.environ.get("PLAYWRIGHT_BROWSERS_PATH"),
             str(Path.home() / ".cache" / "ms-playwright"),
             str(Path.home() / "Library" / "Caches" / "ms-playwright")]
    for root in filter(None, roots):
        found = []
        for pattern in ("chromium-*/chrome-linux*/chrome",
                        "chromium-*/chrome-mac*/Chromium.app/Contents/MacOS/Chromium"):
            found.extend(glob.glob(os.path.join(os.path.expanduser(root), pattern)))
        if found:
            return max(found, key=_revision)
    for name in ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable"):
        path = shutil.which(name)
        if path:
            return path
    return None


def free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def running_chromiums():
    """[(pid, command line)] from `pgrep -a chrom`. Empty when pgrep is absent."""
    try:
        out = subprocess.run(["pgrep", "-a", "chrom"], capture_output=True, text=True,
                             timeout=5).stdout
    except Exception:
        return []
    rows = []
    for line in out.splitlines():
        pid, _, cmd = line.strip().partition(" ")
        if pid.isdigit():
            rows.append((int(pid), cmd))
    return rows


class Chromium(object):
    """The one headless Chromium this server process may own."""

    def __init__(self):
        self.proc = None
        self.url = None
        self.profile = None

    def owned(self):
        return self.proc is not None and self.proc.poll() is None

    def _foreign(self):
        rows = []
        own = self.proc.pid if self.owned() else None
        for pid, cmd in running_chromiums():
            if own is not None:
                try:
                    if os.getpgid(pid) == own:
                        continue
                except OSError:
                    continue
            rows.append((pid, cmd))
        return rows

    def ensure(self):
        """The CDP URL to use, starting this server's own Chromium if needed.

        Never attaches to, and never kills, a browser this process did not
        start. The one exception is an explicitly set `BU_CDP_URL`, which is a
        person naming a browser to share."""
        if self.owned() and cdp_answers(self.url):
            return self.url
        if self.proc is not None:
            # Ours, but gone or unreachable. close() reaps it and removes its
            # profile directory, so a restart cannot leave either behind.
            log("this server's Chromium is no longer answering; starting a fresh one")
        self.close()
        configured = os.environ.get("BU_CDP_URL")
        if configured and cdp_answers(configured):
            return configured
        # Anything else running is somebody else's: a Playwright MCP browser,
        # another session's browse server, a desktop Chrome. Noted, then
        # ignored. The old refusal made every call fail whenever one existed.
        foreign = self._foreign()
        if foreign:
            log("%d other Chromium process(es) on this box; starting our own anyway"
                % len(foreign))
        binary = find_chromium()
        if not binary or not os.path.isfile(binary):
            raise BrowseError(
                "no Chromium binary found. Install one with `npx playwright install "
                "chromium`, or set JEV_BROWSE_CHROMIUM to a Chromium or Chrome binary."
            )
        port = free_port()
        self.profile = tempfile.mkdtemp(prefix="jev-browse-profile-")
        command = [binary, "--headless=new", "--remote-debugging-port=%d" % port,
                   "--user-data-dir=%s" % self.profile, "--no-first-run",
                   "--no-default-browser-check"]
        if os.environ.get("JEV_BROWSE_NO_SANDBOX") == "1":
            command.append("--no-sandbox")
        command.append("about:blank")
        stderr_path = os.path.join(self.profile, "chromium-stderr.log")
        try:
            with open(stderr_path, "wb") as stderr:
                proc = self.proc = subprocess.Popen(
                    command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                    stderr=stderr, start_new_session=True,
                )
        except OSError as exc:
            self.close()
            raise BrowseError("could not start Chromium (%s): %s" % (binary, exc))
        self.url = "http://127.0.0.1:%d" % port
        deadline = time.monotonic() + CHROMIUM_START_S
        while time.monotonic() < deadline and proc.poll() is None:
            if cdp_answers(self.url, timeout_s=1.0):
                log("started headless Chromium pid %d on %s" % (proc.pid, self.url))
                return self.url
            time.sleep(0.1)
        try:
            with open(stderr_path, "r", errors="replace") as f:
                said = f.read(64 * 1024)
        except OSError:
            said = ""
        self.close()
        if "No usable sandbox" in said:
            raise BrowseError(
                "Chromium (%s) refused to start: no usable sandbox. On Ubuntu 23.10+ "
                "AppArmor denies user namespaces to a binary with no profile. Give the "
                "binary an AppArmor profile, or accept an unsandboxed browser by setting "
                "JEV_BROWSE_NO_SANDBOX=1 in this server's `env`. See browse/README.md."
                % binary)
        raise BrowseError("Chromium (%s) did not open its CDP port within %.0f s."
                          % (binary, CHROMIUM_START_S))

    def close(self):
        proc, self.proc = self.proc, None
        if proc is not None and proc.poll() is None:
            try:
                if hasattr(os, "killpg"):
                    os.killpg(proc.pid, signal.SIGTERM)
                else:
                    proc.terminate()
                proc.wait(timeout=5)
            except Exception:
                _kill_group(proc)
        profile, self.profile = self.profile, None
        if profile:
            shutil.rmtree(profile, ignore_errors=True)
        self.url = None


# --- the tool ----------------------------------------------------------------

_URL_IN_GOAL = re.compile(r"https?://[^\s<>\"'`]+")


def parse_arguments(args):
    if not isinstance(args, dict):
        raise BrowseError("arguments must be an object with a `goal`.")
    unknown = sorted(set(args) - set(TOOL["inputSchema"]["properties"]))
    if unknown:
        raise BrowseError("unknown argument(s): %s. `browse` takes goal, start_url, "
                          "extract and screenshot." % ", ".join(unknown))
    goal = args.get("goal")
    if not isinstance(goal, str) or not goal.strip():
        raise BrowseError("`goal` is required and must be a non-empty string.")
    for name in ("start_url", "extract"):
        if args.get(name) is not None and not isinstance(args[name], str):
            raise BrowseError("`%s` must be a string." % name)
    screenshot = args.get("screenshot", False)
    if not isinstance(screenshot, bool):
        raise BrowseError("`screenshot` must be true or false.")
    start_url = (args.get("start_url") or "").strip()
    if not start_url:
        m = _URL_IN_GOAL.search(goal)
        if not m:
            raise BrowseError("give `start_url`, or put an http(s) URL in the goal: "
                              "the agent needs a page to start on.")
        start_url = m.group(0).rstrip(".,;:!?)]}")
    if not re.match(r"https?://", start_url, re.I):
        raise BrowseError("`start_url` must be an http:// or https:// URL.")
    return {"goal": goal.strip(), "start_url": start_url,
            "extract": (args.get("extract") or "").strip() or None,
            "screenshot": screenshot}


def trim_text(text, limit=TEXT_LIMIT_BYTES):
    raw = (text or "").encode("utf-8")
    if len(raw) <= limit:
        return text or "", False
    return raw[:limit].decode("utf-8", errors="ignore"), True


class Browse(object):
    def __init__(self):
        self.chromium = Chromium()
        self.calls = 0
        self.daemon_used = False

    def daemon_name(self):
        # browser_harness keeps one daemon per BU_NAME. A name of our own
        # keeps this server off a daemon some other session attached to a
        # different browser.
        return "jevkit-browse-%d" % os.getpid()

    def child_env(self, key, cdp_url):
        env = dict(os.environ)
        env["TYPESAFE_API_KEY"] = key
        env["BU_CDP_URL"] = cdp_url
        env.pop("BU_CDP_WS", None)
        env["BU_NAME"] = self.daemon_name()
        return env

    def call(self, args):
        started = time.monotonic()
        deadline = started + call_timeout_s()
        params = parse_arguments(args)
        clone = resolve_clone()
        key = resolve_key()
        try:
            cdp_url = self.chromium.ensure()
            request = dict(params, op="browse")
            if params["screenshot"]:
                directory = state_dir()
                directory.mkdir(parents=True, exist_ok=True, mode=0o700)
                self.calls += 1
                request["screenshot_path"] = str(directory / (
                    "browse-%s-%d-%d.png" % (time.strftime("%Y%m%d-%H%M%S"),
                                             os.getpid(), self.calls)))
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise BrowseTimeout("timed out after %.0f s (JEV_BROWSE_TIMEOUT) before "
                                    "the agent could start." % call_timeout_s())
            # The daemon starts on the runner's first use, so a call that
            # raises from here on still leaves one to stop.
            self.daemon_used = True
            result = run_runner(request, self.child_env(key, cdp_url), clone, remaining)
        except BrowseTimeout:
            # The killed agent leaves its tab behind. A browser we own is
            # cheaper to restart than to clean; one we do not own is left be.
            self.chromium.close()
            raise
        except BrowseError as exc:
            raise BrowseError(str(exc).replace(key, "[REDACTED]"))
        text, truncated = trim_text(result.get("text"))
        out = {
            "final_url": result.get("final_url"),
            "title": result.get("title"),
            "status": result.get("status"),
            "steps": result.get("steps"),
            "elapsed_ms": int((time.monotonic() - started) * 1000),
            "text": text,
        }
        if truncated:
            out["text_truncated"] = True
        if params["extract"]:
            out["extracted"] = trim_text(result.get("extracted"))[0]
        if params["screenshot"]:
            out["screenshot_path"] = result.get("screenshot_path")
        return json.loads(json.dumps(out).replace(key, "[REDACTED]"))

    def shutdown(self):
        if self.daemon_used:
            # Stop the harness daemon whenever one may exist, whoever owns the
            # browser. It outlives an external browser we never close, so
            # ownership is the wrong test: a call was made, so stop it.
            try:
                clone = resolve_clone()
                env = dict(os.environ, BU_NAME=self.daemon_name())
                run_runner({"op": "stop_daemon"}, env, clone, 10)
            except Exception:
                pass
        self.chromium.close()


# --- JSON-RPC ----------------------------------------------------------------

def _result(msg_id, result):
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _error(msg_id, code, message):
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def tool_result(text, is_error=False):
    return {"content": [{"type": "text", "text": text}], "isError": bool(is_error)}


class Server(object):
    def __init__(self, browse=None):
        self.browse = browse or Browse()

    def handle(self, msg):
        """One JSON-RPC message in, one response out, or None for a
        notification. Never raises."""
        if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0" \
                or not isinstance(msg.get("method"), str):
            msg_id = msg.get("id") if isinstance(msg, dict) else None
            return _error(msg_id, -32600, "Invalid Request")
        method, msg_id = msg["method"], msg.get("id")
        if "id" not in msg:
            return None  # a notification: notifications/initialized, cancelled, ...
        params = msg.get("params")
        if params is None:
            params = {}
        if not isinstance(params, dict):
            return _error(msg_id, -32602, "params must be an object")
        try:
            if method == "initialize":
                wanted = params.get("protocolVersion")
                version = wanted if wanted in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0]
                return _result(msg_id, {
                    "protocolVersion": version,
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                })
            if method == "ping":
                return _result(msg_id, {})
            if method == "tools/list":
                return _result(msg_id, {"tools": [TOOL]})
            if method == "tools/call":
                if params.get("name") != TOOL["name"]:
                    return _error(msg_id, -32602, "Unknown tool: %r" % (params.get("name"),))
                return _result(msg_id, self.call_tool(params.get("arguments") or {}))
            return _error(msg_id, -32601, "Method not found: %s" % method)
        except Exception as exc:
            return _error(msg_id, -32603, "Internal error: %s" % type(exc).__name__)

    def call_tool(self, args):
        try:
            return tool_result(json.dumps(self.browse.call(args), ensure_ascii=False))
        except BrowseError as exc:
            return tool_result("browse failed: %s" % exc, is_error=True)
        except Exception as exc:
            return tool_result("browse failed: unexpected %s" % type(exc).__name__,
                               is_error=True)

    def handle_line(self, line):
        """A line of input in, a list of responses out."""
        try:
            msg = json.loads(line)
        except Exception:
            return [_error(None, -32700, "Parse error")]
        if isinstance(msg, list):
            if not msg:
                return [_error(None, -32600, "Invalid Request")]
            return [r for r in (self.handle(m) for m in msg) if r is not None]
        response = self.handle(msg)
        return [] if response is None else [response]

    def serve(self, stdin, stdout):
        try:
            for line in stdin:
                if not line.strip():
                    continue
                for response in self.handle_line(line):
                    stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
                    stdout.flush()
        except (KeyboardInterrupt, BrokenPipeError):
            pass
        finally:
            try:
                self.browse.shutdown()
            except Exception:
                pass


def main():
    server = Server()

    def stop(_signum, _frame):
        raise KeyboardInterrupt()

    for sig in (signal.SIGTERM, getattr(signal, "SIGHUP", None)):
        if sig is not None:
            try:
                signal.signal(sig, stop)
            except Exception:
                pass
    server.serve(sys.stdin, sys.stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main())
