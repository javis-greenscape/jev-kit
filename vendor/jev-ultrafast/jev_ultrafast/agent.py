"""The complete agent loop. Typed choices, observable state, bounded execution."""

import base64
import time
from pathlib import Path

from .browser import Browser, StalePage
from .model import action_space, decide, field_context, field_text
from .questions import MAX_STEPS, STOP_CONFIDENCE_DEFAULTS, stop_threshold  # noqa: F401

# The DONE/BLOCKED confidence gates (STOP_CONFIDENCE_DEFAULTS, stop_threshold) live in
# questions.py, so the planner can gate its own DONE on the same bar without importing the
# browser stack. Re-exported here, where the gate is applied.


MAX_STALE_STREAK = 8


class Agent:
    def __init__(self, url, goals, *, record_dir=None, screenshots=False, rank_goal=None, browser=None):
        task = goals.strip() if isinstance(goals, str) else "\n".join(goals).strip()
        if not task:
            raise ValueError("Supply a task")
        plan = [task]
        self.pending_text = None
        # The goal reaches Browser only so snapshot.js can rank off-viewport candidates by
        # it. A caller that hands this agent one step of a longer task (a planner naming the
        # next click) can pass the whole task as `rank_goal`, so the ranking still sees what
        # the run is for. Nothing else uses it, and unset is the old behaviour exactly.
        # A caller that passes `browser` owns that tab: the agent runs in it as it stands (no
        # navigation to `url`) and leaves it open on close, so a planner can run every step of
        # one task in the same tab and keep the page's own state between steps.
        self.owns_browser = browser is None
        if browser is None:
            self.browser = Browser(url, goal=(rank_goal or task))
        else:
            browser.set_goal(rank_goal or task)
            self.browser = browser
        self.record_dir = Path(record_dir) if record_dir else None
        self.screenshots = screenshots or bool(record_dir)
        try:
            page = self.browser.observe(screenshot=self.screenshots)
        except Exception:
            self.close()
            raise
        self.state = dict(
            browser=self.browser,
            goal="\n".join(plan),
            page=page,
            decision=None,
            history=[],
            status="ready",
            plan=plan,
            plan_index=0,
            decisions=[],
            text_calls=[],
            elapsed_ms=0,
            started_at=None,
            record=bool(self.record_dir),
        )
        if self.record_dir:
            self.record_dir.mkdir(parents=True, exist_ok=True)
            (self.record_dir / "000000.jpg").write_bytes(base64.b64decode(page["screenshot"]))

    def snapshot(self):
        return {
            **{k: v for k, v in self.state.items() if k != "browser"},
            "elements": action_space(self.state["page"]["actions"])[0],
        }

    def command(self, name, body=None):
        body = body or {}
        state = self.state
        if name == "tick":
            try:
                self.command("predict", {})
                return self.command("act", {"fingerprint": state["page"]["fingerprint"]})
            except StalePage:
                state["decision"] = None
                state["status"] = "ready"
                # A target that goes stale every time it is chosen is never executed, so it never
                # reaches history and the no-progress stop never sees it. Count consecutive stale
                # attempts and stop the run instead of spending the whole decision budget.
                state["stale_streak"] = state.get("stale_streak", 0) + 1
                if state["stale_streak"] >= MAX_STALE_STREAK:
                    state["status"] = "blocked"
                    state["stopped"] = "the chosen target went stale %d times in a row" % MAX_STALE_STREAK
                    state["elapsed_ms"] = round((time.perf_counter() - state["started_at"]) * 1000)
                    return self.snapshot()
                self._reobserve()
                state["elapsed_ms"] = round((time.perf_counter() - state["started_at"]) * 1000)
                return self.snapshot()
        elif name == "predict":
            if not state["browser"]:
                raise ValueError("Start a demo first")
            if state["started_at"] is None:
                state["started_at"] = time.perf_counter()
            if not state["browser"].fresh(state["page"]):
                self._reobserve()
            state["decision"] = None
            if state["status"] in {"done", "blocked"}:
                raise ValueError("This run has stopped. Start a fresh demo.")
            if len(state["decisions"]) >= MAX_STEPS * 2:
                raise ValueError("Reached the demo's model-call budget")
            state["decision"] = decide(state["page"], state["goal"], state["history"])
            state["decisions"].append(
                {
                    **state["decision"],
                    "fingerprint": state["page"]["fingerprint"],
                    "elapsed_ms": round((time.perf_counter() - state["started_at"]) * 1000),
                }
            )
            state["status"] = "predicted"
        elif name == "act":
            decision, page = state["decision"], state["page"]
            if not decision or body.get("fingerprint") != page["fingerprint"]:
                raise ValueError("Observe and choose before acting")
            # Consume once, before any mutation or model call. A retry cannot double-click.
            state["decision"] = None
            selected = decision["choice"]
            if selected in {"DONE", "BLOCKED"}:
                if not state["browser"].fresh(page):
                    state["status"] = "ready"
                    raise StalePage("Page changed since the decision. Choose again.")
                confidence = decision.get("confidence")
                if (
                    not state.get("stop_rechecked")
                    and isinstance(confidence, (int, float))
                    and confidence < stop_threshold(selected)
                ):
                    # Not accepted yet: look again and ask again, once.
                    state["stop_rechecked"] = True
                    state.setdefault("rechecks", []).append(
                        {"operation": selected, "confidence": confidence, "url": page["url"]}
                    )
                    state["status"] = "ready"
                    state["page"] = state["browser"].observe(screenshot=self.screenshots)
                    state["elapsed_ms"] = round((time.perf_counter() - state["started_at"]) * 1000)
                    return self.snapshot()
                state["status"] = "done" if selected == "DONE" else "blocked"
                state["plan_index"] = int(selected == "DONE")
                state["elapsed_ms"] = round((time.perf_counter() - state["started_at"]) * 1000)
                return self.snapshot()
            action = next(a for a in page["actions"] if a["id"] == selected)
            if len(state["history"]) >= MAX_STEPS:
                state["status"] = "blocked"
                raise ValueError(f"Stopped at the {MAX_STEPS}-action demo budget")
            text, helper = None, None
            if action["kind"] == "fill":
                if not state["browser"].fresh(page):
                    raise StalePage("Page changed before text generation. Choose again.")
                context = field_context(state["goal"], action, page, state["history"])
                if self.pending_text and self.pending_text[0] == context:
                    _, text, helper = self.pending_text
                else:
                    text, helper = field_text(context)
                    self.pending_text = (context, text, helper)
                    state["text_calls"].append({**helper, "field": action["label"], "value": text})
            # Browser.act checks freshness immediately before input, including after text generation.
            state["browser"].act(action, page, text=text)
            self.pending_text = None
            state["elapsed_ms"] = round((time.perf_counter() - state["started_at"]) * 1000)
            state["stale_streak"] = 0
            # Record execution before observing. A stale post-action observation must not erase the action.
            state["history"].append(
                {
                    "step": len(state["history"]) + 1,
                    "action": action["label"],
                    "kind": action["kind"],
                    "choice": selected,
                    "probability": decision["probabilities"][selected],
                    "confidence": decision["confidence"],
                    "latency_ms": decision["latency_ms"],
                    "text": text,
                    "text_helper": helper["model"] if helper else None,
                    "text_latency_ms": helper["latency_ms"] if helper else 0,
                    "operation": decision["operation"],
                    "target": decision["target"],
                    "page_changed": None,
                    "url": page["url"],
                    "usage": decision["usage"],
                    "executed_ms": round((time.perf_counter() - state["started_at"]) * 1000),
                    "elapsed_ms": state["elapsed_ms"],
                }
            )
            state["page"] = state["browser"].observe(screenshot=self.screenshots)
            state["elapsed_ms"] = round((time.perf_counter() - state["started_at"]) * 1000)
            changed = state["page"]["fingerprint"] != page["fingerprint"]
            if changed:
                # Only an action that changed the page re-arms the gate. A WAIT or a no-op click
                # does not, or a low-confidence BLOCKED, WAIT, BLOCKED, ... cycle would recheck
                # forever and run the decision budget out.
                state["stop_rechecked"] = False
            state["history"][-1].update(
                page_changed=changed,
                url=state["page"]["url"],
                elapsed_ms=state["elapsed_ms"],
            )
            if state["record"]:
                (self.record_dir / f"{state['elapsed_ms']:06d}.jpg").write_bytes(
                    base64.b64decode(state["page"]["screenshot"])
                )
            repeated = state["history"][-3:]
            state["status"] = (
                "blocked"
                if len(repeated) == 3 and all(h["page_changed"] is False and h["kind"] != "wait" for h in repeated)
                else "ready"
            )
        else:
            raise ValueError("Unknown command")
        return self.snapshot()

    def _reobserve(self):
        """Replace the observed page with the one the tab shows now.

        The page can change with no action of ours (a timer, a late render, a redirect). A
        page that differs is a new page for the DONE/BLOCKED gate as much as one an action
        produced, so it re-arms the gate the same way."""
        state = self.state
        before = state["page"]["fingerprint"]
        state["page"] = state["browser"].observe(screenshot=self.screenshots)
        if state["page"]["fingerprint"] != before:
            state["stop_rechecked"] = False

    def run(self):
        while self.state["status"] not in {"done", "blocked"}:
            yield self.command("tick")

    def close(self):
        if getattr(self, "owns_browser", True):
            self.browser.close()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()
