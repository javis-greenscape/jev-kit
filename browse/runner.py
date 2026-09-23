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
  {"op": "plan", "goal", "start_url", "extract"?, "screenshot_path"?, "links"?,
   "plan_model"?, "budget_s"?}
                            a warm Claude planner names each step, the agent runs it
                            (jev_ultrafast/planner.py)
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
        # The operation head's confidence per decision, for tuning the DONE/BLOCKED gate
        # (jev_ultrafast/agent.py) from recorded runs rather than from a guess.
        "confidence": [d.get("confidence") for d in decisions],
        "rechecks": state.get("rechecks") or [],
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


def _read(browser, request, elements):
    """The page as the caller sees it: url, title, text, and whatever else was asked for."""
    result = {
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
        result["links"] = _links({"elements": elements})
    return result


def _agent_step(request, goal, start_url, rank_goal, browser=None, deadline=None):
    """Run the Jev agent for one goal; the page it ends on, plus its state.

    With `browser`, the agent runs in that tab as it stands and leaves it open: the caller
    owns it. Without, the agent opens start_url in a tab of its own and closes it. With
    `deadline` (a time.monotonic() value), the agent stops between actions once it passes."""
    from jev_ultrafast import Agent

    with Agent(start_url, goal, rank_goal=rank_goal, browser=browser) as agent:
        for _state in agent.run():
            if deadline is not None and time.monotonic() >= deadline:
                break
        state = agent.snapshot()
        result = _read(agent.browser, request, state.get("elements"))
    result["status"] = state["status"]
    result["steps"] = len(state["decisions"])
    return result, state


def browse(request):
    started = time.perf_counter()
    result, state = _agent_step(request, request["goal"], request["start_url"], request.get("rank_goal"))
    result["timing"] = _timing(state, round((time.perf_counter() - started) * 1000))
    return result


def _look_in(browser, request, rank_goal, screenshot=False):
    """The page in `browser` as it stands, its element table ranked by `rank_goal`, nothing
    clicked and nothing reloaded."""
    from jev_ultrafast.model import action_space

    browser.set_goal(rank_goal)
    page = browser.observe(screenshot=False)
    elements = action_space(page["actions"])[0]
    wanted = dict(request, links=True)
    if not screenshot:
        wanted["screenshot_path"] = None
    return _read(browser, wanted, elements)


def _check_done(browser, task):
    """The code-owned check behind the planner's DONE: one Jev Noul on the page as it stands."""
    from jev_ultrafast.model import task_complete

    browser.set_goal(task)
    return task_complete(browser.observe(screenshot=False), task)


# One warm planner child per model, for the life of this worker. Started on the first `plan`
# request, never before: a session that never plans never pays for a Claude child.
_PLANNERS = {}


def planner_for(model):
    from jev_ultrafast import planner as plan
    from jev_ultrafast.text_model_claude_standing import StandingTextModel

    model = model or os.environ.get("JEV_PLANNER_MODEL") or plan.DEFAULT_MODEL
    if model not in _PLANNERS:
        _PLANNERS[model] = StandingTextModel(model=model, system_prompt=plan.SYSTEM_PROMPT)
    return _PLANNERS[model]


def plan(request):
    """One planned task in ONE tab, opened once and closed once.

    Every planner turn, the FIND listings and the DONE check included, runs against the same
    tab, so whatever the page keeps without changing its URL (an open menu or modal, an
    expanded section, a filled field, a single-page app's state) survives from step to step."""
    from jev_ultrafast import planner as plan_module
    from jev_ultrafast.browser import Browser

    started = time.perf_counter()
    planner = planner_for(request.get("plan_model"))
    budget_s = float(request.get("budget_s") or 170)
    task = request["goal"]
    states, checks = [], []
    browser = None

    def step(goal, _url, rank_goal, deadline):
        page, state = _agent_step(dict(request, links=True, screenshot_path=None), goal, None, rank_goal,
                                  browser=browser, deadline=deadline)
        states.append(state)
        return page

    def look(_url, rank_goal):
        return _look_in(browser, request, rank_goal)

    def verify(goal):
        check = _check_done(browser, goal)
        checks.append(check)
        return check["probability"]

    try:
        browser = Browser(request["start_url"], goal=task)
        try:
            outcome = plan_module.run(task, request["start_url"], planner, step, look, budget_s,
                                      verify=verify)
            # Read the tab once more before it closes: a redirect or live update after the last
            # step would otherwise return an older page than the one the DONE check passed.
            final = dict(outcome["page"])
            final.update(_look_in(browser, request, task,
                                  screenshot=bool(request.get("screenshot_path"))))
        finally:
            browser.close()
    finally:
        # A fresh conversation for the next task, started now in the background so the next
        # call still finds a warm child: one task's hops are noise in the next one's context.
        planner.new_session()
    for key in ("status", "steps"):
        final.pop(key, None)
    if not request.get("links"):
        final.pop("links", None)
    decisions = [d for s in states for d in (s.get("decisions") or [])]
    stopped = outcome["plan"]["stopped"]
    final["status"] = "done" if stopped == "planner said DONE" else "blocked"
    if final["status"] == "blocked":
        final["reason"] = stopped
    final["steps"] = len(decisions)
    final["plan"] = outcome["plan"]
    final["timing"] = _timing({"decisions": decisions,
                               "text_calls": [t for s in states for t in (s.get("text_calls") or [])],
                               "rechecks": [r for s in states for r in (s.get("rechecks") or [])]},
                              round((time.perf_counter() - started) * 1000))
    final["timing"]["done_checks"] = [{"probability": c.get("probability"), "latency_ms": c.get("latency_ms")}
                                      for c in checks]
    return final


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
    if op == "plan":
        return plan(request)
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
