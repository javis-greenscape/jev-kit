"""The half of `browse` that runs inside the jev-ultrafast clone.

browse/server.py starts one of these per server process, with the clone's own
interpreter and the clone as its working directory, and then talks to it for
the life of the server: one JSON request per line on stdin, one JSON result
per line on stdout. It is a separate process so that the server can stay
standard-library only, and so that a per-call timeout can kill the agent
outright by killing its process group.

It is long-lived on purpose. A fresh process per call paid for the imports,
the browser_harness daemon connection and, worst of all, a cold text model
every time. Staying up keeps all three warm; the numbers are in
vendor/jev-ultrafast/SPIKE-NOTES.md, "Warm worker and warm Haiku".

Requests (one JSON object per line):
  {"op": "browse", "goal", "start_url", "extract"?, "screenshot_path"?,
   "rank_goal"?, "links"?}
  {"op": "warm"}            start the text model and the harness daemon now
  {"op": "ping"}
  {"op": "stop_daemon"}     stop the browser_harness daemon named by BU_NAME

Everything the agent or its libraries print goes to stderr. The result lines
are the only thing on stdout, and a failure is a result too: {"error": "..."}.
A failure never ends the loop; only EOF on stdin does.
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
    if last is None:
        raise RuntimeError("_evaluate called with attempts <= 0")
    raise last


def _timing(state, total_ms):
    """Where the wall time went, per call.

    `jev_ms` is one entry per decision the chooser made (TypeSafe, or whatever
    DECISION_PROVIDER selected); `text_ms` is one entry per text-model call.
    Both are worth having in the result even when nobody is benchmarking: a
    slow call is almost always one of the two."""
    decisions = state.get("decisions") or []
    text_calls = state.get("text_calls") or []
    return {
        "decisions": len(decisions),
        # One entry per decision, not per executed action: a decision the
        # executor threw away because the page moved under it shows up here
        # and nowhere else, which is the first thing to look at when a call is
        # slower than its step count explains.
        "ops": [d.get("operation") for d in decisions],
        "jev_ms": [d.get("latency_ms") for d in decisions],
        "text_ms": [t.get("latency_ms") for t in text_calls],
        "total_ms": total_ms,
        "agent_ms": state.get("elapsed_ms"),
        "jev_transport": sorted({d.get("transport") for d in decisions if d.get("transport")}),
    }


LINK_LABEL_CHARS = 120


def _links(state):
    """The final page's element table, the same rows Jev chose between.

    One entry per observed element, in the order snapshot.js offered them: the on-screen
    candidates first, then the off-viewport links ranked by how much their name looks like
    the run's `rank_goal`. Nothing is added and nothing is re-sorted here, so a caller that
    plans the next step reads exactly what the chooser read, not a second opinion about the
    page. Labels are trimmed because a link's accessible name can be a paragraph."""
    return [
        {"index": e["index"], "role": e.get("role"), "label": (e.get("label") or "")[:LINK_LABEL_CHARS]}
        for e in state.get("elements") or []
    ]


def browse(request):
    from jev_ultrafast import Agent

    started = time.perf_counter()
    with Agent(request["start_url"], request["goal"], rank_goal=request.get("rank_goal")) as agent:
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
        if request.get("links"):
            result["links"] = _links(state)
        result["timing"] = _timing(state, round((time.perf_counter() - started) * 1000))
        return result


def warm():
    """Pay the cold starts now, off the clock of the first real call.

    Three of them: importing the agent and its dependencies, the text model's
    own child process, and the browser_harness daemon."""
    import jev_ultrafast  # noqa: F401  (the expensive import chain)
    from browser_harness.admin import ensure_daemon

    warmed = []
    started = time.perf_counter()
    try:
        ensure_daemon()
        warmed.append("harness")
    except Exception as exc:
        print("browse-runner: harness daemon not warmed: %s" % exc, file=sys.stderr)
    if os.environ.get("TEXT_MODEL_PROVIDER") == "claude-standing":
        try:
            from jev_ultrafast.text_model_claude_standing import field_text, warm as warm_text

            warm_text()
            warmed.append("text-model")
            # Starting the child is not the whole cold start: its first
            # request pays for the session on top. One throwaway question now
            # is worth about a second off the first call that types anything.
            try:
                field_text({"goal": "Type the word ready into the field.",
                            "field": {"index": "1", "label": "Warm up",
                                      "role": "textbox", "value": ""},
                            "nearby_fields": [],
                            "page": {"title": "Warm up", "url": "about:blank"}})
                warmed.append("text-model-primed")
            except Exception as exc:
                print("browse-runner: text model not primed: %s" % exc, file=sys.stderr)
        except Exception as exc:
            print("browse-runner: text model not warmed: %s" % exc, file=sys.stderr)
    return {"warmed": warmed, "warm_ms": round((time.perf_counter() - started) * 1000)}


def stop_daemon():
    from browser_harness import admin

    admin.restart_daemon(os.environ.get("BU_NAME") or None)
    return {"stopped": True}


def handle(request):
    op = request.get("op")
    if op == "browse":
        return browse(request)
    if op == "warm":
        return warm()
    if op == "ping":
        return {"pong": True}
    if op == "stop_daemon":
        return stop_daemon()
    return {"error": "unknown op %r" % (op,)}


def main():
    # Keep stdout for the result lines; anything else printed lands on stderr.
    out = os.fdopen(os.dup(1), "w")
    os.dup2(2, 1)
    # The clone's own .env, for the text-model settings. setdefault, so
    # everything the server handed over in the environment always wins.
    try:
        from jev_ultrafast.demo import load_environment

        load_environment()
    except Exception as exc:
        print("browse-runner: no .env loaded: %s" % exc, file=sys.stderr)
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            result = handle(json.loads(line))
        except BaseException as exc:
            result = {"error": "%s: %s" % (type(exc).__name__, str(exc)[:500])}
        try:
            out.write(json.dumps(result) + "\n")
            out.flush()
        except BaseException:
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
