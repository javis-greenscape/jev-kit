"""The half of `browse` that runs inside the jev-ultrafast clone.

browse/server.py starts this with the clone's own interpreter and the clone as
its working directory, writes one JSON request to stdin, and reads one JSON
object from the last line of stdout. It is a separate process so that the
server can stay standard-library only, and so that a per-call timeout can kill
the agent outright.

Requests:
  {"op": "browse", "goal", "start_url", "extract"?, "screenshot_path"?}
  {"op": "stop_daemon"}     stop the browser_harness daemon named by BU_NAME

Everything the agent or its libraries print goes to stderr. The result line
is the only thing on stdout, and a failure is a result too: {"error": "..."}.
"""
import base64
import json
import os
import sys
import time


def _evaluate(browser, expression, attempts=10):
    """Runtime.evaluate, retried while a navigation is still settling."""
    last = None
    for _ in range(attempts):
        try:
            return browser.evaluate(expression)
        except Exception as exc:  # StalePage while the document changes
            last = exc
            time.sleep(0.1)
    raise last


def browse(request):
    from jev_ultrafast import Agent
    from jev_ultrafast.demo import load_environment

    # The clone's own .env, for the text-model settings. setdefault, so the
    # key and BU_CDP_URL the server handed over always win.
    load_environment()
    if not os.environ.get("TEXT_MODEL_API_KEY") and not os.environ.get("TEXT_MODEL_PROVIDER"):
        # Typing into a field needs a text model. With none configured, fall
        # back to the local `claude` CLI adapter the patches add.
        os.environ["TEXT_MODEL_PROVIDER"] = "claude-cli"

    with Agent(request["start_url"], request["goal"]) as agent:
        for _state in agent.run():
            pass
        state = agent.snapshot()
        browser = agent.browser
        result = {
            "status": state["status"],
            "steps": len(state["decisions"]),
            "final_url": _evaluate(browser, "location.href"),
            "title": _evaluate(browser, "document.title"),
            "text": _evaluate(browser, "document.body ? document.body.innerText : ''"),
        }
        selector = request.get("extract")
        if selector:
            result["extracted"] = _evaluate(
                browser,
                "(() => { try { return [...document.querySelectorAll(%s)]"
                ".map(e => (e.innerText || e.textContent || '').trim()).join('\\n'); }"
                " catch (e) { return 'invalid selector: ' + e.message; } })()"
                % json.dumps(selector),
            )
        path = request.get("screenshot_path")
        if path:
            shot = browser.call("Page.captureScreenshot", format="png")
            with open(path, "wb") as f:
                f.write(base64.b64decode(shot["data"]))
            os.chmod(path, 0o600)
            result["screenshot_path"] = path
        return result


def stop_daemon():
    from browser_harness import admin

    admin.restart_daemon(os.environ.get("BU_NAME") or None)
    return {"stopped": True}


def main():
    # Keep stdout for the one result line; anything else printed lands on stderr.
    out = os.fdopen(os.dup(1), "w")
    os.dup2(2, 1)
    try:
        request = json.loads(sys.stdin.read())
        op = request.get("op")
        if op == "browse":
            result = browse(request)
        elif op == "stop_daemon":
            result = stop_daemon()
        else:
            result = {"error": "unknown op %r" % (op,)}
    except BaseException as exc:
        result = {"error": "%s: %s" % (type(exc).__name__, str(exc)[:500])}
    out.write(json.dumps(result) + "\n")
    out.flush()
    return 1 if "error" in result else 0


if __name__ == "__main__":
    sys.exit(main())
