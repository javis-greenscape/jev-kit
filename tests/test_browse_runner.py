"""browse/runner.py's plan op, with jev_ultrafast replaced by fakes.

The runner imports jev_ultrafast lazily, inside each op, so these tests put stand-in modules in
sys.modules for the duration of a call. No browser, no Jev, no Claude child."""
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from browse import runner  # noqa: E402


class FakeBrowser:
    made = []

    def __init__(self, url, goal=""):
        self.url, self.goals, self.closed = url, [goal], 0
        FakeBrowser.made.append(self)

    def set_goal(self, goal):
        self.goals.append(goal)

    def observe(self, screenshot=False):
        return {"actions": [], "url": self.url}

    def evaluate(self, expression):
        return {"location.href": self.url, "document.title": "T"}.get(expression, "")

    def close(self):
        self.closed += 1


class FakeAgent:
    browsers = []

    def __init__(self, url, goal, rank_goal=None, browser=None):
        FakeAgent.browsers.append(browser)
        self.browser = browser

    def run(self):
        return iter(())

    def snapshot(self):
        return {"status": "done", "decisions": [], "elements": []}

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass


class FakePlanner:
    def __init__(self):
        self.sessions = 0

    def new_session(self):
        self.sessions += 1


def fake_modules(run):
    package = types.ModuleType("jev_ultrafast")
    package.Agent = FakeAgent
    browser = types.ModuleType("jev_ultrafast.browser")
    browser.Browser = FakeBrowser
    model = types.ModuleType("jev_ultrafast.model")
    model.action_space = lambda actions: ([],)
    model.task_complete = lambda state, task: {"probability": 0.2, "latency_ms": 5}
    planner = types.ModuleType("jev_ultrafast.planner")
    planner.run = run
    package.browser, package.model, package.planner = browser, model, planner
    return {"jev_ultrafast": package, "jev_ultrafast.browser": browser,
            "jev_ultrafast.model": model, "jev_ultrafast.planner": planner}


class TestPlanUsesOneTab(unittest.TestCase):
    def setUp(self):
        FakeBrowser.made.clear()
        FakeAgent.browsers.clear()
        self.planner = FakePlanner()

    def plan(self, run):
        with mock.patch.dict(sys.modules, fake_modules(run)), \
                mock.patch.object(runner, "planner_for", return_value=self.planner):
            return runner.plan({"goal": "Reach the axle article", "start_url": "https://w/start"})

    def test_every_turn_runs_in_the_one_tab_and_it_closes_once(self):
        def run(task, start_url, planner, step, look, budget_s, verify=None):
            look(start_url, task)
            step("Click A", "https://w/elsewhere", "A", None)
            look("https://w/elsewhere", "B")          # a FIND
            step("Click B", "https://w/elsewhere", "B", None)
            probability = verify(task)
            return {"page": {"final_url": "https://w/axle"},
                    "plan": {"stopped": "reached the 12-turn cap", "p": probability}}

        out = self.plan(run)
        self.assertEqual(len(FakeBrowser.made), 1)
        tab = FakeBrowser.made[0]
        self.assertEqual(tab.url, "https://w/start")
        self.assertEqual(FakeAgent.browsers, [tab, tab])
        self.assertEqual(tab.closed, 1)
        # FIND and the DONE check re-rank the same tab rather than opening a new one.
        self.assertIn("B", tab.goals)
        self.assertEqual(self.planner.sessions, 1)
        self.assertEqual(out["status"], "blocked")
        self.assertEqual(out["reason"], "reached the 12-turn cap")
        self.assertEqual(out["timing"]["done_checks"], [{"probability": 0.2, "latency_ms": 5}])

    def test_the_tab_closes_when_the_loop_raises(self):
        def run(*_args, **_kwargs):
            raise RuntimeError("boom")

        with self.assertRaises(RuntimeError):
            self.plan(run)
        self.assertEqual(FakeBrowser.made[0].closed, 1)
        self.assertEqual(self.planner.sessions, 1)

    def test_a_verified_done_is_done(self):
        def run(task, start_url, planner, step, look, budget_s, verify=None):
            return {"page": {"final_url": "https://w/axle"}, "plan": {"stopped": "planner said DONE"}}

        out = self.plan(run)
        self.assertEqual(out["status"], "done")
        self.assertNotIn("reason", out)


if __name__ == "__main__":
    unittest.main()
