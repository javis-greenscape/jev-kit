"""Observed actions through Browser Harness; one CDP session, no per-step subprocess."""

import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path

from browser_harness.admin import ensure_daemon
from browser_harness.helpers import cdp

DEFAULT_OFFSCREEN_MAX = 100


def offscreen_max():
    """How many off-viewport links a snapshot may offer, from JEV_OFFSCREEN_MAX.

    Upstream offers none: only what is in the viewport. Offering the nearest
    ones is what made an article-length page navigable at all, but every extra
    row lengthens the element table the chooser reads, and filling the whole
    250-row budget with them cost about a third of every decision's latency.
    Hence a budget of their own. `0` restores upstream's behaviour, and a
    negative number means "as many as the 250 allows".

    100 is measured, not guessed, and the curve is not monotonic: see
    SPIKE-NOTES.md, "How many off-screen links". A cap too small to reach the
    link a goal wants is worse than no cap at all, because it fills the table
    with neighbours of the target and none of them is the target."""
    raw = os.environ.get("JEV_OFFSCREEN_MAX", "")
    try:
        return int(raw)
    except (TypeError, ValueError):
        return DEFAULT_OFFSCREEN_MAX


def read_state(goal=""):
    """snapshot.js with the off-screen budget and the run's goal compiled in.

    The goal is the only thing the snapshot needs a model for otherwise: off-viewport
    candidates are ranked by how much their accessible name looks like the goal before they
    are ranked by distance, which is plain token overlap, deterministic and free."""
    source = Path(__file__).with_name("snapshot.js").read_text()
    source = re.sub(
        r"const OFFSCREEN_LIMIT=-?\d+;", "const OFFSCREEN_LIMIT=%d;" % offscreen_max(), source, count=1
    )
    return re.sub(
        r'const GOAL_TEXT="";',
        lambda _match: "const GOAL_TEXT=%s;" % json.dumps(goal or ""),
        source,
        count=1,
    )


def marker_of(source):
    return f"(() => {{ const state={source}; return state?.marker ?? null; }})()"


# Atomically read visible content and controls, preserving actual DOM node identity.
READ_STATE = read_state()
MARKER = marker_of(READ_STATE)
# The scroll offsets a wheel at (550, 650) could move: the page, and every element under the cursor.
SCROLL_POSITION = (
    "(() => { const s=[scrollX,scrollY]; let e=document.elementFromPoint(550,650); "
    "while (e) { s.push(e.scrollTop,e.scrollLeft); e=e.parentElement; } return s; })()"
)

class StalePage(ValueError):
    """A decision no longer refers to the observed page."""


class Browser:
    def __init__(self, url, goal=""):
        # The goal reaches the snapshot so it can rank off-viewport candidates by it. It is
        # baked into both reads, because the marker is computed from the same ordered table.
        self.read_state = read_state(goal)
        self.marker = marker_of(self.read_state)
        ensure_daemon()
        self.target = cdp("Target.createTarget", url="about:blank", background=True)["targetId"]
        self.session = cdp("Target.attachToTarget", targetId=self.target, flatten=True)["sessionId"]
        self.call("Emulation.setDeviceMetricsOverride", width=1120, height=780, deviceScaleFactor=1, mobile=False)
        # Keep rAF/menus rendering in an owned background tab, without activating the user's Chrome tab.
        self.call("Emulation.setFocusEmulationEnabled", enabled=True)
        self.call("Page.navigate", url=url)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if self.evaluate("document.readyState") == "complete":
                break
            time.sleep(0.02)

    def set_goal(self, goal):
        """Rank off-viewport candidates by `goal` from the next observe on, in the same tab.

        A planner runs many steps against one tab, each ranked by its own step text, and must
        not reopen the page to change the ranking: that would lose menus, modals, filled fields
        and any other state kept at a stable URL."""
        self.read_state = read_state(goal)
        self.marker = marker_of(self.read_state)

    def call(self, method, **params):
        return cdp(method, session_id=self.session, **params)

    def evaluate(self, expression):
        response = self.call("Runtime.evaluate", expression=expression, returnByValue=True)
        if response.get("exceptionDetails"):
            raise StalePage("Document changed during evaluation")
        return response.get("result", {}).get("value")

    def observe(self, screenshot=True):
        if getattr(self, "after_input", None):
            action, self.after_input = self.after_input, None
            # This is read-only and happens after execution was logged, even if navigation interrupts it.
            try:
                self.call(
                    "Runtime.evaluate",
                    expression="""(action => new Promise(resolve => {
                      const field=window.__jevFast?.nodes.get(action.node);
                      const autocomplete=action.kind==='fill' && field?.getAttribute('role')==='combobox';
                      let frames=0, stopped=false;
                      const finish=()=>{stopped=true;resolve()};
                      setTimeout(finish,autocomplete ? 200 : 50);
                      const ready=()=>{
                        if (stopped) return;
                        const ids=(field?.getAttribute('aria-controls')||field?.getAttribute('aria-owns')||'')
                          .split(/\\s+/).filter(Boolean);
                        const roots=ids.length ? ids.map(id=>document.getElementById(id)).filter(Boolean) : [document];
                        const options=roots.flatMap(root=>[...root.querySelectorAll('[role="option"]')]);
                        if (++frames>=2 && (!autocomplete || options.some(e=>{
                          const r=e.getBoundingClientRect();
                          return r.width && r.height && r.bottom>0 && r.top<innerHeight &&
                            e.checkVisibility({checkOpacity:true,checkVisibilityCSS:true});
                        }))) finish();
                        else requestAnimationFrame(ready);
                      };
                      requestAnimationFrame(ready);
                    }))(""" + json.dumps(action) + ")",
                    awaitPromise=True,
                    returnByValue=True,
                )
            except RuntimeError:
                pass
        for attempt in range(10):
            try:
                return browser_operation(
                    {
                        "operation": "observe",
                        "session": self.session,
                        "screenshot": screenshot,
                        "read_state": self.read_state,
                    }
                )
            except StalePage:
                if attempt == 9:
                    raise
                time.sleep(0.02)
        raise StalePage("Page did not settle")

    def fresh(self, page, action=None):
        if action is not None and action["kind"] in {"click", "select"}:
            node = action["node"]
            if type(node) is not int:
                return False
            current = self.evaluate(
                "(() => { const c=window.__jevFast; "
                f"return c ? [c.pageKey(),c.guard(c.nodes.get({node}))] : null; }})()"
            )
            return current == [page["page_key"], page["guards"].get(str(node))]
        return self.evaluate(getattr(self, "marker", MARKER)) == page["marker"]

    def act(self, action, page, text=None):
        if not self.fresh(page, action):
            raise StalePage("Page changed since this decision. Observe again.")
        if action["kind"] == "wait":
            time.sleep(0.1)
        result = browser_operation({"operation": "act", "session": self.session, "action": action, "text": text})
        self.after_input = action if action["kind"] != "wait" else None
        return result

    def close(self):
        if self.target:
            cdp("Target.closeTarget", targetId=self.target)
            self.target = None


def fingerprint(state):
    content = {k: state[k] for k in ("url", "text", "actions", "scroll")}
    return hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest()


def browser_operation(request):
    operation = request["operation"]
    session = request["session"]

    def call(method, **params):
        return cdp(method, session_id=session, **params)

    def evaluate(expression):
        result = call("Runtime.evaluate", expression=expression, returnByValue=True)
        if result.get("exceptionDetails"):
            if operation == "act" and request["action"]["kind"] == "select":
                raise RuntimeError("Dropdown execution was interrupted; inspect before retrying.")
            raise StalePage("Document changed during evaluation")
        return result.get("result", {}).get("value")

    if operation == "act":
        action = request["action"]
        kind = action["kind"]
        if kind == "scroll":
            # Chromium drops the first wheel event dispatched after every navigation, so the first
            # scroll of each page silently did nothing, counted as an unchanged step, and three of
            # them blocked the run. Confirm the scroll landed, and dispatch once more if it did not.
            # The dropped event is dropped, not deferred, so the retry cannot scroll twice.
            before = evaluate(SCROLL_POSITION)
            for _ in range(2):
                call("Input.dispatchMouseEvent", type="mouseWheel", x=550, y=650, deltaX=0, deltaY=action["delta"])
                deadline = time.monotonic() + 0.25
                while time.monotonic() < deadline:
                    if evaluate(SCROLL_POSITION) != before:
                        return {"executed": action["id"]}
                    time.sleep(0.02)
        elif kind != "wait":
            if type(action["node"]) is not int:
                raise ValueError("Invalid observed node")
            # Code-owned node IDs refer to actual observed elements, never model-generated selectors.
            target = evaluate("""(action => {
              const e=window.__jevFast?.nodes.get(action.node);
              if (!e?.isConnected || e.matches(':disabled') || e.closest('[aria-disabled="true"],[inert]') ||
                  !e.checkVisibility({checkOpacity:true,checkVisibilityCSS:true})) return null;
              if (action.kind==='fill' && (e.readOnly || e.getAttribute('aria-readonly')==='true')) return null;
              // Observed off-viewport: bring it into view, then resolve geometry and hit-test as usual.
              if (action.offscreen) e.scrollIntoView({block:'center',inline:'center',behavior:'instant'});
              // A link that wraps onto two lines has a bounding box whose centre can sit over
              // the next table cell; try the centre of each line box before the whole box.
              const point=()=>{
                for (const r of [...e.getClientRects(), e.getBoundingClientRect()]) {
                  const x=r.x+r.width/2, y=r.y+r.height/2;
                  if (!r.width || !r.height || x<0 || y<0 || x>=innerWidth || y>=innerHeight) continue;
                  if (e.contains(document.elementFromPoint(x,y))) return {x,y};
                }
                return null;
              };
              // A target observed on screen can still be clipped by a scrolled table or covered
              // by a sticky header by the time it is clicked. Bring it to the centre once and
              // hit-test again, rather than calling the page stale and choosing the same
              // element again forever.
              let at=point();
              if (!at) { e.scrollIntoView({block:'center',inline:'center',behavior:'instant'}); at=point(); }
              if (!at) return null;
              const {x,y}=at;
              if (action.kind==='select') {
                if (e.tagName!=='SELECT' || ![...e.options].some(o=>o.value===action.value &&
                    !o.disabled && !o.closest('optgroup[disabled]'))) return null;
                e.value=action.value;
                e.dispatchEvent(new Event('input',{bubbles:true}));
                e.dispatchEvent(new Event('change',{bubbles:true}));
              }
              return {x,y};
            })(""" + json.dumps(action) + ")")
            if target is None:
                if kind == "select":
                    raise RuntimeError("Dropdown execution was not confirmed; inspect before retrying.")
                raise StalePage("Target changed or is covered. Observe again.")
            if kind != "select":
                x, y = target["x"], target["y"]
                for event in ("mousePressed", "mouseReleased"):
                    call("Input.dispatchMouseEvent", type=event, x=x, y=y, button="left", clickCount=1)
                if kind == "fill":
                    call(
                        "Input.dispatchKeyEvent",
                        type="keyDown",
                        key="a",
                        code="KeyA",
                        modifiers=4 if sys.platform == "darwin" else 2,
                        commands=["selectAll"],
                    )
                    call(
                        "Input.dispatchKeyEvent",
                        type="keyUp",
                        key="a",
                        code="KeyA",
                        modifiers=4 if sys.platform == "darwin" else 2,
                    )
                    call("Input.insertText", text=request["text"])
        return {"executed": action["id"]}

    info = evaluate(request.get("read_state") or READ_STATE)
    if info is None:
        raise StalePage("Document is navigating")
    info["fingerprint"] = fingerprint(info)
    if request.get("screenshot", True):
        info["screenshot"] = call("Page.captureScreenshot", format="jpeg", quality=72)["data"]
    return info
