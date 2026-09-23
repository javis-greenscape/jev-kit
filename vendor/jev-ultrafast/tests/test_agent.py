"""Offline contracts for a dynamic operation/target policy. No paid APIs."""

import json
import shutil
import subprocess
import time
from copy import deepcopy
from pathlib import Path
from unittest.mock import Mock

import pytest

from jev_ultrafast import agent as loop
from jev_ultrafast import model
from jev_ultrafast.browser import StalePage, browser_operation, fingerprint


def page():
    state = {
        "url": "https://example.test/",
        "title": "Search",
        "text": "Search",
        "scroll": {"y": 0},
        "actions": [
            {"id": "e1", "kind": "fill", "label": "Search", "role": "textbox", "value": "", "node": 10},
            {"id": "e2", "kind": "click", "label": "Open Search", "role": "textbox", "value": "", "node": 10},
            {"id": "e3", "kind": "click", "label": "Go", "role": "button", "value": "", "node": 20},
            {"id": "wait", "kind": "wait", "label": "Wait"},
        ],
    }
    state["fingerprint"] = fingerprint(state)
    return state


def choice(ids, selected):
    return {"choice": selected, "confidence": 1.0, "probabilities": {i: float(i == selected) for i in ids}}


def decision(action="e1"):
    return {
        "choice": action,
        "operation": "TYPE_TEXT",
        "target": "1",
        "confidence": 1.0,
        "probabilities": {action: 1.0},
        "latency_ms": 10,
        "usage": {},
    }


@pytest.mark.parametrize("mutation", ["unknown", "nan", "missing", "negative", "non_max", "confidence"])
def test_invalid_choice_is_rejected(mutation):
    a = choice(["a", "b"], "a")
    if mutation == "unknown":
        a["choice"] = "invented"
    elif mutation == "nan":
        a["probabilities"]["a"] = float("nan")
    elif mutation == "missing":
        del a["probabilities"]["b"]
    elif mutation == "negative":
        a["probabilities"]["b"] = -1
    elif mutation == "non_max":
        a["choice"] = "b"
    else:
        a["confidence"] = 5
    with pytest.raises(ValueError, match="Invalid TypeSafe"):
        model.validate_choice(a, {"a", "b"})


def test_one_index_per_node_with_operation_specific_targets():
    elements, targets, controls = model.action_space(page()["actions"])
    assert len(elements) == 2
    assert elements[0]["operations"] == ["TYPE_TEXT", "CLICK"]
    assert targets["TYPE_TEXT"]["1"]["id"] == "e1"
    assert targets["CLICK"]["1"]["id"] == "e2"
    assert targets["CLICK"]["2"]["id"] == "e3"
    assert "WAIT" in controls


def test_all_heads_are_one_request_and_only_matching_head_executes(monkeypatch):
    calls = []

    def post(_url, _key, body):
        calls.append(body)
        return {
            "model": "test",
            "answers": {
                "operation": choice(body["questions"]["operation"]["criteria"], "TYPE_TEXT"),
                "type_text_target": choice(["textbox: Search"], "textbox: Search"),
                "click_target": {"choice": "invented"},
            },
        }

    monkeypatch.setenv("TYPESAFE_API_KEY", "test")
    monkeypatch.setattr(model, "post_json", post)
    d = model.choose(page(), "Find a book", [])
    assert len(calls) == 1
    assert d["operation"] == "TYPE_TEXT" and d["target"] == "1" and d["choice"] == "e1"
    assert set(calls[0]["questions"]) == {"operation", "click_target", "type_text_target",
                                          "final_page", "needed_off_screen"}


def test_click_cannot_consume_a_text_target(monkeypatch):
    def post(_url, _key, body):
        return {
            "model": "test",
            "answers": {
                "operation": choice(body["questions"]["operation"]["criteria"], "CLICK"),
                "type_text_target": choice(["textbox: Search"], "textbox: Search"),
                "click_target": choice(["textbox: Search", "button: Go", "link: 999"], "link: 999"),
            },
        }

    monkeypatch.setenv("TYPESAFE_API_KEY", "test")
    monkeypatch.setattr(model, "post_json", post)
    with pytest.raises(ValueError, match="Invalid TypeSafe"):
        model.choose(page(), "Find a book", [])


def test_target_head_receives_control_state_and_only_its_own_rules(monkeypatch):
    p = page()
    p["actions"].insert(0, {
        "id": "toggle", "kind": "click", "label": "Free cancellation", "node": 30,
        "role": "checkbox", "checked": "true", "selected": False,
    })

    def post(_url, _key, body):
        questions = body["questions"]
        target = questions["click_target"]
        assert target["criteria"]["checkbox: Free cancellation"]["checked"] == "true"
        assert target["criteria"]["checkbox: Free cancellation"]["selected"] is False
        # One judgment per question: the operation rulebook is not repeated in a target head.
        assert target["instructions"]["rules"] == model.TARGET.format(operation="CLICK")
        assert questions["operation"]["instructions"]["rules"] == model.OPERATION
        return {
            "model": "test",
            "answers": {
                "operation": choice(questions["operation"]["criteria"], "CLICK"),
                "click_target": choice(target["criteria"], "button: Go"),
            },
        }

    monkeypatch.setenv("TYPESAFE_API_KEY", "test")
    monkeypatch.setattr(model, "post_json", post)
    d = model.choose(p, "Search with free cancellation", [])
    assert d["choice"] == "e3" and d["target"] == "3" and d["target_name"] == "button: Go"


# --- option names Jev can read ---------------------------------------------------


def links_page(labels):
    actions = [
        {"id": "e%d" % i, "kind": "click", "label": label, "role": "link", "value": "", "node": 100 + i}
        for i, label in enumerate(labels, 1)
    ]
    state = {"url": "https://example.test/", "title": "T", "text": "x" * 9000, "scroll": {"y": 0},
             "actions": actions}
    state["fingerprint"] = fingerprint(state)
    return state


def test_target_options_are_named_by_role_and_label_not_by_number(monkeypatch):
    sent = []

    def post(_url, _key, body):
        sent.append(body)
        criteria = body["questions"]["click_target"]["criteria"]
        return {"model": "test", "answers": {
            "operation": choice(body["questions"]["operation"]["criteria"], "CLICK"),
            "click_target": choice(criteria, "link: Bicycle wheel (2)"),
        }}

    monkeypatch.setenv("TYPESAFE_API_KEY", "test")
    monkeypatch.setattr(model, "post_json", post)
    d = model.choose(links_page(["Bicycle wheel", "Bicycle", "Bicycle wheel"]), "Open Bicycle wheel", [])
    names = list(sent[0]["questions"]["click_target"]["criteria"])
    assert names == ["link: Bicycle wheel", "link: Bicycle", "link: Bicycle wheel (2)"]
    assert not any(n.isdigit() for n in names)
    # The name maps back to the element it was built from, and nothing else.
    assert d["choice"] == "e3" and d["target"] == "3"
    assert d["probabilities"] == {"e1": 0.0, "e2": 0.0, "e3": 1.0}
    # The state's element table uses the same names, so the two can be read together.
    assert [e["name"] for e in sent[0]["state"]["elements"]] == names


def test_names_are_unique_deterministic_and_bounded():
    labels = ["A", "A", "A (2)", "x" * 500, ""]
    first = model.element_names(model.action_space(links_page(labels)["actions"])[0])
    again = model.element_names(model.action_space(links_page(labels)["actions"])[0])
    assert first == again
    assert len(set(first.values())) == len(labels)
    assert list(first.values())[:3] == ["link: A", "link: A (2)", "link: A (2) (2)"]
    assert all(len(n) <= model.OPTION_NAME_CHARS + len("link: ") for n in first.values())
    assert first["5"] == "link: (no name)"


def test_select_options_carry_the_value_they_pick():
    _, targets, _ = model.action_space(select_page(3)["actions"])
    elements, _, _ = model.action_space(select_page(3)["actions"])
    named = model.target_names("SELECT", targets["SELECT"], model.element_names(elements))
    assert list(named) == ["combobox: Country \u2192 Option 0",
                           "combobox: Country \u2192 Option 1",
                           "combobox: Country \u2192 Option 2"]
    assert list(named.values()) == ["1:1", "1:2", "1:3"]


# --- page text in the state --------------------------------------------------------


@pytest.mark.parametrize("setting,expected", [("0", None), ("1500", 1500), ("", model.PAGE_TEXT_CHARS_DEFAULT)])
def test_page_text_is_trimmed_to_the_configured_length(monkeypatch, setting, expected):
    monkeypatch.setenv("JEV_PAGE_TEXT_CHARS", setting)
    body, *_ = model.build_questions(links_page(["A"]), "goal", [])
    if expected is None:
        assert "text" not in body["state"]["page"]
    else:
        assert len(body["state"]["page"]["text"]) == min(expected, 9000)


# --- one judgment per question, all in one request ------------------------------------


def answer_with(operation, nouls, target=None):
    def post(_url, _key, body):
        answers = {"operation": choice(body["questions"]["operation"]["criteria"], operation)}
        if target:
            head = operation.lower() + "_target"
            answers[head] = choice(body["questions"][head]["criteria"], target)
        answers.update({k: {"type": "noul", "noul": v} for k, v in nouls.items()})
        return {"model": "test", "answers": answers}
    return post


def scrolling_page():
    p = page()
    p["actions"].insert(-1, {"id": "scroll_down", "kind": "scroll", "label": "Scroll down", "delta": 560})
    p["fingerprint"] = fingerprint(p)
    return p


def test_the_nouls_ride_in_the_same_single_request(monkeypatch):
    sent = []
    post = answer_with("DONE", {"final_page": 0.9, "needed_off_screen": 0.1})
    monkeypatch.setenv("TYPESAFE_API_KEY", "test")
    monkeypatch.setattr(model, "post_json", lambda *a: sent.append(a[2]) or post(*a))
    d = model.choose(page(), "Anything", [])
    assert len(sent) == 1
    q = sent[0]["questions"]
    assert q["final_page"]["type"] == q["needed_off_screen"]["type"] == "noul"
    assert d["choice"] == "DONE" and d["confidence"] == 1.0 and d["adjusted_by"] is None


def test_done_on_a_page_the_noul_says_is_not_final_is_gated_low(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "test")
    monkeypatch.setattr(model, "post_json", answer_with("DONE", {"final_page": 0.2, "needed_off_screen": 0.1}))
    d = model.choose(page(), "Open the result", [])
    assert d["choice"] == "DONE" and d["confidence"] == 0.2 and d["adjusted_by"] == "final_page"
    assert d["operation_confidence"] == 1.0


def test_blocked_with_the_needed_element_off_screen_scrolls_instead(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "test")
    monkeypatch.setattr(model, "post_json", answer_with("BLOCKED", {"final_page": 0.1, "needed_off_screen": 0.8}))
    d = model.choose(scrolling_page(), "Open the last link", [])
    assert d["choice"] == "scroll_down" and d["adjusted_by"] == "needed_off_screen"


def test_blocked_stands_when_the_page_cannot_scroll_or_the_noul_is_bad(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "test")
    monkeypatch.setattr(model, "post_json", answer_with("BLOCKED", {"final_page": 0.1, "needed_off_screen": 0.8}))
    assert model.choose(page(), "g", [])["choice"] == "BLOCKED"
    monkeypatch.setattr(model, "post_json", answer_with("BLOCKED", {"needed_off_screen": float("nan")}))
    d = model.choose(scrolling_page(), "g", [])
    assert d["choice"] == "BLOCKED" and d["nouls"] == {"final_page": None, "needed_off_screen": None}


def test_quoted_task_text_still_uses_the_llm(monkeypatch):
    monkeypatch.setenv("TEXT_MODEL_API_KEY", "test")
    post = Mock(return_value={"choices": [{"message": {"content": '{"text":"Zurich"}'}}]})
    monkeypatch.setattr(model, "post_json", post)
    context = model.field_context('Fly from "Zurich" to London', page()["actions"][0], page(), [])
    assert model.field_text(context)[0] == "Zurich"
    assert post.call_count == 1
    sent = json.loads(post.call_args.args[2]["messages"][1]["content"])
    assert sent["goal"] == 'Fly from "Zurich" to London'


def test_missing_text_credential_stops_before_guessing(monkeypatch):
    monkeypatch.delenv("TEXT_MODEL_API_KEY", raising=False)
    with pytest.raises(ValueError, match="TEXT_MODEL_API_KEY"):
        model.field_text({"goal": 'Enter "Zurich"'})


@pytest.fixture
def runner():
    a = loop.Agent.__new__(loop.Agent)
    a.screenshots = False
    a.pending_text = None
    p = page()
    a.state = {
        "browser": Mock(fresh=Mock(return_value=True), observe=Mock(return_value=p)),
        "page": p,
        "decision": decision(),
        "goal": "Find a book",
        "history": [],
        "decisions": [],
        "status": "predicted",
        "started_at": time.perf_counter(),
        "record": False,
        "text_calls": [],
    }
    return a


def test_stale_decision_is_consumed_before_any_mutation(runner):
    runner.state["browser"].fresh.return_value = False
    with pytest.raises(StalePage):
        runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    runner.state["browser"].act.assert_not_called()
    assert runner.state["decision"] is None


def test_generated_text_reused_only_for_identical_retry_context(runner, monkeypatch):
    helper = Mock(return_value=("book", {"model": "test", "latency_ms": 10}))
    monkeypatch.setattr(loop, "field_text", helper)
    runner.state["browser"].act.side_effect = [StalePage("Changed before input"), None]
    with pytest.raises(StalePage):
        runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    runner.state["decision"] = decision()
    runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    assert helper.call_count == 1
    assert runner.state["browser"].act.call_count == 2  # The first call rejects before any browser input.
    assert runner.pending_text is None


def test_changed_field_context_does_not_reuse_generated_text(runner, monkeypatch):
    helper = Mock(return_value=("book", {"model": "test", "latency_ms": 10}))
    monkeypatch.setattr(loop, "field_text", helper)
    runner.state["browser"].act.side_effect = [StalePage("Changed before input"), None]
    with pytest.raises(StalePage):
        runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    # Change something both context shapes look at: page body text (the "full" shape) and the
    # field's own value (the "trimmed" shape, which drops page text — see model.field_context).
    runner.state["page"]["text"] = "Different page context"
    runner.state["page"]["actions"][0]["value"] = "changed"
    runner.state["decision"] = decision()
    runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    assert helper.call_count == 2


def test_loading_waits_do_not_trigger_no_progress_stop(runner):
    for _ in range(5):
        runner.state["decision"] = decision("wait")
        runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    assert len(runner.state["history"]) == 5 and runner.state["status"] == "ready"


def test_stale_observation_preserves_executed_action(runner):
    runner.state["decision"] = decision("e3")
    runner.state["browser"].observe.side_effect = StalePage("changed")
    with pytest.raises(StalePage):
        runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    assert runner.state["history"][-1]["action"] == "Go"
    runner.state["browser"].act.assert_called_once()


def test_observation_is_one_atomic_browser_read(monkeypatch):
    import jev_ultrafast.browser as browser

    p = page()
    cdp = Mock(return_value={"result": {"value": p}})
    monkeypatch.setattr(browser, "cdp", cdp)
    actual = browser_operation({"operation": "observe", "session": "test", "screenshot": False})
    assert actual["actions"] == p["actions"]
    assert cdp.call_count == 1
    assert cdp.call_args.args[0] == "Runtime.evaluate"


def test_executor_rejects_a_stale_page_before_browser_input(monkeypatch):
    import jev_ultrafast.browser as browser

    b = browser.Browser.__new__(browser.Browser)
    b.fresh = Mock(return_value=False)
    operation = Mock()
    monkeypatch.setattr(browser, "browser_operation", operation)
    with pytest.raises(StalePage):
        b.act(page()["actions"][0], page(), "book")
    operation.assert_not_called()


@pytest.mark.parametrize("response", [{"exceptionDetails": {}}, {"result": {}}])
def test_interrupted_dropdown_mutation_cannot_be_retried_as_stale(monkeypatch, response):
    import jev_ultrafast.browser as browser

    # A navigation can destroy the evaluation result after the change event already fired.
    if "exceptionDetails" in response:
        response["exceptionDetails"] = {"text": "Execution context destroyed"}
    cdp = Mock(return_value=response)
    monkeypatch.setattr(browser, "cdp", cdp)
    with pytest.raises(RuntimeError, match="Dropdown execution"):
        browser_operation({"operation": "act", "session": "test", "action": {
            "id": "e1", "kind": "select", "node": 1, "value": "Design",
        }})
    assert cdp.call_count == 1


def test_fingerprint_tracks_values_and_identity_not_screenshots():
    p = page()
    other = deepcopy(p)
    other["screenshot"] = "changed"
    assert fingerprint(p) == fingerprint(other)
    other["actions"][0]["node"] = 99
    assert fingerprint(p) != fingerprint(other)


@pytest.mark.parametrize("changed", ["Departure", "Where from?", "Where to?", "year"])
def test_flight_verification_rejects_wrong_trip(changed):
    from examples.flights import verify

    actual = {
        "url": "https://www.google.com/travel/flights/search?tfs=example",
        "text": "Track prices from Zürich to London departing 2026-09-20",
        "actions": [
            {"label": k, "value": v}
            for k, v in [
                ("Change ticket type. One way", "One way"),
                ("Where from?", "Zürich"),
                ("Where to?", "London"),
                ("Departure", "Sun, Sep 20"),
                ("Nonstop flight on Sunday, September 20. Select flight", ""),
            ]
        ],
    }
    assert verify(actual)["passed"]
    if changed == "year":
        actual["text"] = actual["text"].replace("2026", "2027")
    else:
        next(a for a in actual["actions"] if a["label"] == changed)["value"] = "wrong"
    assert not verify(actual)["passed"]


@pytest.mark.parametrize(
    "content", ["Thinking: Zurich", '{"text":null}', '{"text":"Zurich","extra":true}', '{"text":123}']
)
def test_text_helper_rejects_invalid_values(monkeypatch, content):
    monkeypatch.setenv("TEXT_MODEL_API_KEY", "test")
    monkeypatch.setattr(model, "post_json", Mock(return_value={"choices": [{"message": {"content": content}}]}))
    with pytest.raises(ValueError, match="nothing typed"):
        model.field_text({"goal": "Find a flight"})


def test_navigation_during_prediction_reobserves_without_action(runner):
    runner.state["browser"].fresh.side_effect = StalePage("Document navigating")
    runner.command("tick")
    assert runner.state["status"] == "ready"
    assert runner.state["decision"] is None
    runner.state["browser"].act.assert_not_called()


def test_offscreen_click_is_offered_with_its_direction_and_flag():
    state = page()
    state["actions"].insert(
        3,
        {
            "id": "e4",
            "kind": "click",
            "label": "Photosynthesis (below)",
            "role": "link",
            "value": "",
            "node": 30,
            "offscreen": "below",
        },
    )
    elements, targets, _controls = model.action_space(state["actions"])
    assert "Photosynthesis (below)" in [e["label"] for e in elements]
    chosen = targets["CLICK"][str(len(elements))]
    assert chosen["id"] == "e4" and chosen["offscreen"] == "below"


def test_a_scroll_that_moves_the_page_changes_the_fingerprint():
    state = page()
    moved = deepcopy(state)
    moved["scroll"] = {"y": 560}
    # The in-viewport action set changes with the scroll offset too; the offset alone must be enough.
    assert fingerprint(moved) != fingerprint(state)


def test_the_offscreen_budget_comes_from_the_environment(monkeypatch):
    import importlib

    from jev_ultrafast import browser as browser_module

    for raw, want in (("0", 0), ("40", 40), ("-1", -1), ("", 100), ("lots", 100)):
        monkeypatch.setenv("JEV_OFFSCREEN_MAX", raw)
        assert browser_module.offscreen_max() == want, raw
        reloaded = importlib.reload(browser_module)
        assert "const OFFSCREEN_LIMIT=%d;" % want in reloaded.READ_STATE, raw
    monkeypatch.delenv("JEV_OFFSCREEN_MAX")
    importlib.reload(browser_module)


def test_the_local_socket_is_skipped_unless_one_is_named(monkeypatch):
    monkeypatch.delenv("JEV_SYSTEMONE_SOCKET", raising=False)
    assert model.post_via_socket({"state": {}}) is None
    # A path that is not a socket is a fallback, not a crash.
    monkeypatch.setenv("JEV_SYSTEMONE_SOCKET", "/nonexistent/airlock.sock")
    assert model.post_via_socket({"state": {}}) is None


def test_a_daemon_answer_is_used_instead_of_https(monkeypatch, tmp_path):
    import socket as socket_module
    import threading

    path = str(tmp_path / "airlock.sock")
    answer = {"model": "jev-test", "answers": {"operation": {"choice": "DONE"}}}
    server = socket_module.socket(socket_module.AF_UNIX, socket_module.SOCK_STREAM)
    server.bind(path)
    server.listen(1)
    seen = []

    def serve():
        conn, _ = server.accept()
        with conn, conn.makefile("rwb") as f:
            seen.append(json.loads(f.readline()))
            f.write((json.dumps({"ok": True, "response": answer}) + "\n").encode())
            f.flush()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    monkeypatch.setenv("JEV_SYSTEMONE_SOCKET", path)
    monkeypatch.setattr(model, "post_json", lambda *a, **k: pytest.fail("HTTPS was used"))
    assert model.post_via_socket({"state": {"page": {}}}) == answer
    thread.join(timeout=5)
    server.close()
    assert seen[0]["body"] == {"state": {"page": {}}}


def test_a_daemon_that_refuses_falls_back_to_https(monkeypatch, tmp_path):
    import socket as socket_module
    import threading

    path = str(tmp_path / "airlock.sock")
    server = socket_module.socket(socket_module.AF_UNIX, socket_module.SOCK_STREAM)
    server.bind(path)
    server.listen(1)

    def serve():
        conn, _ = server.accept()
        with conn, conn.makefile("rwb") as f:
            f.readline()
            f.write(b'{"ok": false, "error": "no key"}\n')
            f.flush()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    monkeypatch.setenv("JEV_SYSTEMONE_SOCKET", path)
    assert model.post_via_socket({"state": {}}) is None
    thread.join(timeout=5)
    server.close()


# --- Goal-ranked off-screen candidates, same-page fragments, and the one retry ---

SNAPSHOT_JS = Path(__file__).resolve().parents[1] / "jev_ultrafast" / "snapshot.js"


def snapshot_helpers():
    """The pure, fenced helper block out of snapshot.js, runnable on its own in node."""
    source = SNAPSHOT_JS.read_text()
    start = source.index("// --- goal ranking")
    end = source.index("// --- end goal ranking")
    return source[start:end]


def run_js(driver):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    result = subprocess.run(
        [node, "-e", snapshot_helpers() + driver], capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


SORT_AS_SNAPSHOT_DOES = """
    const words=jevGoalWords(goal), flat=jevGoalFlat(goal);
    for (const r of rows) r.rank=jevGoalRank(r.label, words, flat);
    rows.sort((a,b)=>b.rank[0]-a.rank[0] || b.rank[1]-a.rank[1] ||
      a.distance-b.distance || a.node-b.node);
"""


def rank(goal, rows, field="label"):
    """`rows` in the order snapshot.js would offer them off-viewport."""
    return run_js(
        "const goal=%s, rows=%s;" % (json.dumps(goal), json.dumps(rows))
        + SORT_AS_SNAPSHOT_DOES
        + "console.log(JSON.stringify(rows.map(r=>r[%s])));" % json.dumps(field)
    )


def test_a_named_hop_far_down_the_page_is_offered_before_its_nearer_neighbours():
    # The A2 failure: "1972" sat thousands of pixels below the fold on the Chess article, so a
    # hundred rows ordered by distance were all its neighbours and none of them was the target.
    rows = [
        {"label": "Chess piece", "distance": 120, "node": 1},
        {"label": "Rules of chess", "distance": 200, "node": 2},
        {"label": "1971", "distance": 8800, "node": 3},
        {"label": "1972", "distance": 9000, "node": 4},
    ]
    assert rank("From the Chess article, open the article about 1972.", rows)[0] == "1972"


def test_a_two_word_name_quoted_in_the_goal_outranks_a_single_shared_word():
    rows = [
        {"label": "Bicycle", "distance": 10, "node": 1},
        {"label": "Wheel", "distance": 20, "node": 2},
        {"label": "Bicycle wheel", "distance": 7000, "node": 3},
    ]
    assert rank("On the Bicycle article, open Bicycle wheel.", rows)[0] == "Bicycle wheel"


def test_without_a_goal_the_order_is_still_distance_then_identity():
    rows = [
        {"label": "Third", "distance": 300, "node": 9},
        {"label": "First", "distance": 100, "node": 4},
        {"label": "Second", "distance": 300, "node": 5},
    ]
    assert rank("", rows) == ["First", "Second", "Third"]


def test_equally_relevant_candidates_keep_the_nearest_one_first():
    rows = [
        {"label": "Physics", "distance": 4000, "node": 1},
        {"label": "Physics", "distance": 90, "node": 2},
    ]
    assert rank("Open Physics", rows, field="distance") == [90, 4000]


@pytest.mark.parametrize(
    ("href", "same_page"),
    [
        ("#Microsoft", True),
        ("https://en.wikipedia.test/wiki/Guido_van_Rossum#Microsoft", True),
        ("/wiki/Guido_van_Rossum#Python", True),
        ("/wiki/Microsoft", False),
        ("/wiki/Microsoft#History", False),
        ("https://other.test/wiki/Guido_van_Rossum#Microsoft", False),
        ("/wiki/Guido_van_Rossum", False),
        ("", False),
        (None, False),
        ("javascript:void(0)", False),
    ],
)
def test_a_link_that_only_changes_the_fragment_is_recognised(href, same_page):
    here = "https://en.wikipedia.test/wiki/Guido_van_Rossum"
    actual = run_js(
        "console.log(JSON.stringify(jevSamePageFragment(%s, %s)));" % (json.dumps(href), json.dumps(here))
    )
    assert actual is same_page


def test_both_label_paths_carry_the_section_suffix():
    # The A3 failure: a table-of-contents entry read exactly like the article link, so the run
    # ended on the same page. The suffix is built once and used by both the in-viewport and the
    # off-viewport label; this guards the wiring the node tests above cannot see.
    source = SNAPSHOT_JS.read_text()
    assert "' (section of this page)'" in source
    assert "const plain=(name(e)||rname)+fragment;" in source
    assert "label:(name(e)||rname)+fragment," in source


def test_the_goal_is_compiled_into_the_snapshot_read():
    from jev_ultrafast import browser as browser_module

    goal = 'Open the "1972" article'
    source = browser_module.read_state(goal)
    assert "const GOAL_TEXT=%s;" % json.dumps(goal) in source
    assert 'const GOAL_TEXT="";' in browser_module.read_state()
    # The marker is computed from the same ordered table, so it has to be compiled the same way.
    assert "const GOAL_TEXT=%s;" % json.dumps(goal) in browser_module.marker_of(source)


def test_the_observed_read_uses_the_browsers_own_compiled_snapshot(monkeypatch):
    import jev_ultrafast.browser as browser

    b = browser.Browser.__new__(browser.Browser)
    b.session = "test"
    b.read_state = "COMPILED_FOR_THIS_GOAL"
    b.after_input = None
    seen = []

    def cdp(method, **params):
        seen.append(params.get("expression"))
        return {"result": {"value": page()}}

    monkeypatch.setattr(browser, "cdp", cdp)
    b.observe(screenshot=False)
    assert seen == ["COMPILED_FOR_THIS_GOAL"]


def test_a_malformed_decision_is_asked_once_more(monkeypatch):
    calls = []

    def once(_page, _goal, _history):
        calls.append(1)
        if len(calls) == 1:
            raise ValueError(model.INVALID_DECISION)
        return {"choice": "DONE"}

    monkeypatch.setattr(model, "_decide_once", once)
    assert model.decide(page(), "Find a book", []) == {"choice": "DONE"}
    assert len(calls) == 2


def test_a_decision_malformed_twice_is_raised_and_not_asked_a_third_time(monkeypatch):
    calls = []

    def once(_page, _goal, _history):
        calls.append(1)
        raise ValueError(model.INVALID_DECISION)

    monkeypatch.setattr(model, "_decide_once", once)
    with pytest.raises(ValueError, match="Invalid TypeSafe"):
        model.decide(page(), "Find a book", [])
    assert len(calls) == 2


def test_any_other_decision_failure_is_not_retried(monkeypatch):
    calls = []

    def once(_page, _goal, _history):
        calls.append(1)
        raise ValueError("Reached the demo's model-call budget")

    monkeypatch.setattr(model, "_decide_once", once)
    with pytest.raises(ValueError, match="budget"):
        model.decide(page(), "Find a book", [])
    assert len(calls) == 1


def test_an_empty_field_value_is_asked_once_more(monkeypatch):
    calls = []

    def once(_context):
        calls.append(1)
        if len(calls) == 1:
            raise ValueError(model.NO_FIELD_VALUE)
        return "book", {"model": "test", "latency_ms": 1}

    monkeypatch.setattr(model, "_field_text_once", once)
    assert model.field_text({"goal": "Find a book"})[0] == "book"
    assert len(calls) == 2


def test_a_field_value_missing_twice_is_raised(monkeypatch):
    calls = []

    def once(_context):
        calls.append(1)
        raise ValueError(model.NO_FIELD_VALUE)

    monkeypatch.setattr(model, "_field_text_once", once)
    with pytest.raises(ValueError, match="no valid field value"):
        model.field_text({"goal": "Find a book"})
    assert len(calls) == 2


def test_the_text_retry_happens_before_any_browser_input(runner, monkeypatch):
    calls = []

    def once(_context):
        calls.append(1)
        if len(calls) == 1:
            raise ValueError(model.NO_FIELD_VALUE)
        return "book", {"model": "test", "latency_ms": 1}

    monkeypatch.setattr(model, "_field_text_once", once)
    monkeypatch.setattr(loop, "field_text", model.field_text)
    runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    # Two model calls, one input: upstream's rule that a mutation is never retried is intact.
    assert len(calls) == 2
    runner.state["browser"].act.assert_called_once()


# --- the API's own 255-option ceiling on a Choice ------------------------------


def select_page(option_count):
    """A page whose one dropdown offers `option_count` choices."""
    actions = [
        {"id": "e%d" % (i + 1), "kind": "select", "label": "Country → Option %d" % i,
         "role": "combobox", "value": "v%d" % i, "current_value": "", "node": 10}
        for i in range(option_count)
    ]
    actions.append({"id": "wait", "kind": "wait", "label": "Wait"})
    state = {"url": "https://example.test/", "title": "Form", "text": "Form",
             "scroll": {"y": 0}, "actions": actions}
    state["fingerprint"] = fingerprint(state)
    return state


def test_a_head_within_the_limit_is_offered_whole():
    _, targets, _ = model.action_space(select_page(255)["actions"])
    capped, dropped = model.cap_targets(targets)
    assert len(capped["SELECT"]) == 255 and dropped == {}


def test_a_head_over_the_limit_is_trimmed_to_what_the_api_accepts():
    _, targets, _ = model.action_space(select_page(400)["actions"])
    capped, dropped = model.cap_targets(targets)
    assert len(capped["SELECT"]) == model.MAX_CHOICE_OPTIONS == 255
    assert dropped == {"SELECT": 145}
    # The kept options are the first ones action_space() built, in that order.
    assert list(capped["SELECT"])[:2] == ["1:1", "1:2"]


def test_no_request_offers_more_options_than_a_choice_allows(monkeypatch):
    sent = []

    def post(_url, _key, body):
        sent.append(body)
        return {
            "model": "test",
            "answers": {
                "operation": choice(body["questions"]["operation"]["criteria"], "SELECT"),
                "select_target": choice(list(body["questions"]["select_target"]["criteria"]),
                                        list(body["questions"]["select_target"]["criteria"])[0]),
            },
        }

    monkeypatch.setenv("TYPESAFE_API_KEY", "test")
    monkeypatch.setattr(model, "post_json", post)
    decided = model.choose(select_page(400), "Pick a country", [])
    criteria = sent[0]["questions"]["select_target"]["criteria"]
    assert len(criteria) == 255
    assert decided["omitted_targets"] == {"SELECT": 145}
    assert decided["choice"] == "e1"


# --- a step of a longer task ranks by the whole task ---------------------------


def test_a_planners_step_can_rank_off_screen_links_by_the_whole_task(monkeypatch):
    """Agent(rank_goal=...) is what reaches snapshot.js; the step text does not."""
    from jev_ultrafast import agent as agent_module

    made = []

    class FakeBrowser:
        def __init__(self, url, goal=""):
            made.append(goal)

        def observe(self, screenshot=False):
            return page()

        def close(self):
            pass

    monkeypatch.setattr(agent_module, "Browser", FakeBrowser)
    agent_module.Agent("https://example.test/", 'Click the link labelled "Bicycle wheel"',
                       rank_goal="Starting on the Bicycle article, reach the axle article")
    agent_module.Agent("https://example.test/", "Reach the axle article")
    assert made == ["Starting on the Bicycle article, reach the axle article",
                    "Reach the axle article"]


# --- the standing child, asked a different question -----------------------------


def test_the_standing_child_takes_a_model_and_a_system_prompt_of_its_own():
    from jev_ultrafast import text_model_claude_standing as standing

    default = standing._cmd()
    assert default[standing_index(default, "--model") + 1] == standing.MODEL
    assert default[standing_index(default, "--system-prompt") + 1] == standing.SYSTEM_PROMPT
    assert default[standing_index(default, "--effort") + 1] == standing.EFFORT

    mine = standing._cmd("sonnet", "Answer with one line.")
    assert mine[standing_index(mine, "--model") + 1] == "sonnet"
    assert mine[standing_index(mine, "--system-prompt") + 1] == "Answer with one line."
    # Everything else about the shape is the measured one: no tools, no hooks, no persistence.
    assert {"--safe-mode", "--no-session-persistence"} <= set(mine)
    assert mine[standing_index(mine, "--tools") + 1] == ""


def standing_index(command, flag):
    return command.index(flag)


# --- a low-confidence DONE or BLOCKED is looked at again, once ------------------------


def stop_decision(operation, confidence):
    return {"choice": operation, "operation": operation, "target": None, "confidence": confidence,
            "probabilities": {operation: confidence}, "latency_ms": 10, "usage": {}}


@pytest.mark.parametrize("operation", ["DONE", "BLOCKED"])
def test_a_low_confidence_stop_is_rechecked_before_it_ends_the_run(runner, operation):
    runner.state["decision"] = stop_decision(operation, 0.3)
    runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    assert runner.state["status"] == "ready"
    assert runner.state["browser"].observe.call_count == 1
    assert runner.state["rechecks"] == [{"operation": operation, "confidence": 0.3, "url": "https://example.test/"}]
    # The second answer stands, however unsure: the gate never loops.
    runner.state["decision"] = stop_decision(operation, 0.2)
    runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    assert runner.state["status"] == ("done" if operation == "DONE" else "blocked")


def test_a_confident_done_ends_the_run_at_once(runner):
    runner.state["decision"] = stop_decision("DONE", 0.95)
    runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    assert runner.state["status"] == "done"
    runner.state["browser"].observe.assert_not_called()


def test_only_an_action_that_changed_the_page_rearms_the_gate(runner):
    runner.state["decision"] = stop_decision("BLOCKED", 0.3)
    runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    # A WAIT that leaves the page as it was does not re-arm: the next BLOCKED stands.
    runner.state["decision"] = decision("wait")
    runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    runner.state["decision"] = stop_decision("BLOCKED", 0.3)
    runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    assert runner.state["status"] == "blocked" and len(runner.state["rechecks"]) == 1


def test_an_action_that_changed_the_page_rearms_the_gate(runner):
    runner.state["decision"] = stop_decision("DONE", 0.3)
    runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    moved = deepcopy(runner.state["page"])
    moved["url"] = "https://example.test/next"
    moved["fingerprint"] = fingerprint(moved)
    runner.state["browser"].observe.return_value = moved
    runner.state["decision"] = decision("e3")
    runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    runner.state["decision"] = stop_decision("DONE", 0.3)
    runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    assert runner.state["status"] == "ready" and len(runner.state["rechecks"]) == 2


def test_the_threshold_can_be_set_per_operation(runner, monkeypatch):
    monkeypatch.setenv("JEV_DONE_CONFIDENCE", "0.1")
    runner.state["decision"] = stop_decision("DONE", 0.3)
    runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    assert runner.state["status"] == "done"


# --- the planner loop ---------------------------------------------------------------


from jev_ultrafast import planner as plan  # noqa: E402


@pytest.mark.parametrize("line,expected", [
    ("CLICK Bicycle wheel", ("CLICK", "Bicycle wheel")),
    ("CLICK link chlorophyll (below)", ("CLICK", "chlorophyll (below)")),
    ("CLICK link", ("CLICK", "link")),
    ("TYPE searchbox Search = Curie", ("TYPE", ("Search", "Curie"))),
    ('click: "Bicycle wheel"', ("CLICK", "Bicycle wheel")),
    ("FIND Nobel laureates in Physics", ("FIND", "Nobel laureates in Physics")),
    ("TYPE Search = Marie Curie", ("TYPE", ("Search", "Marie Curie"))),
    ("DONE 1903", ("DONE", "1903")),
    ("DONE", ("DONE", "ok")),
    ("FIND", (None, None)),
    ("TYPE Search", (None, None)),
    ("", (None, None)),
    ("Clicking is next", ("STEP", "Clicking is next")),
    ("FINDING nothing", ("STEP", "FINDING nothing")),
    ("This path is not leading anywhere.\nCLICK link History", ("CLICK", "History")),
    ("\n  Try the history section.\n\nDONE 1703", ("DONE", "1703")),
    ("First try this.\nThen that.", ("STEP", "First try this.")),
])
def test_planner_lines_parse(line, expected):
    assert plan.parse(line) == expected


def test_find_returns_matching_rows_best_first():
    links = [{"role": "link", "label": "Physics"}, {"role": "link", "label": "Chemistry"},
             {"role": "link", "label": "List of Nobel laureates in Physics (below)"},
             {"role": "link", "label": "Nobel Prize"}]
    rows = plan.matching(links, "Nobel laureates in Physics")
    assert [r["label"] for r in rows] == ["List of Nobel laureates in Physics (below)", "Physics", "Nobel Prize"]
    assert plan.matching(links, "zebra") == []


class FakePlanner:
    model = "fake"

    def __init__(self, answers):
        self.answers, self.prompts = list(answers), []

    def ask(self, prompt, timeout=None):
        self.prompts.append(json.loads(prompt))
        return {"result": self.answers.pop(0), "total_cost_usd": 0.01, "usage": {"input_tokens": 100}}


def test_a_find_shows_the_planner_what_the_table_left_out():
    far = {"role": "link", "label": "Bicycle wheel (below)"}
    pages = {"start": {"final_url": "https://w/start", "title": "Start", "text": "",
                       "links": [{"role": "link", "label": "Art"}]}}
    looks, steps = [], []

    def look(url, rank):
        looks.append((url, rank))
        if len(looks) == 1:
            return pages["start"]
        return dict(pages["start"], links=[{"role": "link", "label": "Art"}, far])

    def step(goal, url, rank, _deadline):
        steps.append((goal, url, rank))
        return {"final_url": "https://w/Bicycle_wheel", "title": "Bicycle wheel", "text": "", "links": []}

    planner = FakePlanner(["FIND bicycle wheel", "CLICK Bicycle wheel (below)", "DONE ok"])
    out = plan.run("Open the Bicycle wheel article", "https://w/start", planner, step, look, 60)
    assert looks[1] == ("https://w/start", "bicycle wheel")
    assert planner.prompts[1]["found"]["elements"] == ["link Bicycle wheel (below)"]
    assert "elements" not in planner.prompts[1]
    assert steps == [('Click the element labelled "Bicycle wheel (below)".', "https://w/start",
                      "Bicycle wheel (below)\nOpen the Bicycle wheel article")]
    assert out["page"]["final_url"] == "https://w/Bicycle_wheel"
    assert out["plan"]["turns"] == 3 and out["plan"]["finds"] == 1
    assert out["plan"]["stopped"] == "planner said DONE" and out["plan"]["cost_usd"] == 0.03


def test_a_failed_step_stops_the_loop_with_the_page_it_had():
    start = {"final_url": "https://w/start", "title": "Start", "text": "", "links": []}

    def step(*_args):
        raise RuntimeError("boom")

    out = plan.run("t", "https://w/start", FakePlanner(["CLICK X"]), step, lambda *_: start, 60)
    assert out["page"] is start and out["plan"]["stopped"].startswith("the CLICK step failed")


def test_a_target_that_is_always_stale_stops_the_run_instead_of_the_budget(runner, monkeypatch):
    runner.state["browser"].act.side_effect = StalePage("Target changed or is covered.")
    monkeypatch.setattr(loop, "decide", lambda *_a: decision("e3"))
    for _ in range(loop.MAX_STALE_STREAK):
        runner.command("tick")
    assert runner.state["status"] == "blocked"
    assert "stale" in runner.state["stopped"]
    assert len(runner.state["decisions"]) == loop.MAX_STALE_STREAK


# --- a planner's steps share one tab ---------------------------------------------------


def test_an_agent_given_a_tab_runs_in_it_and_leaves_it_open(monkeypatch):
    from jev_ultrafast import agent as agent_module

    monkeypatch.setattr(agent_module, "Browser", Mock(side_effect=AssertionError("opened a tab")))
    tab = Mock(observe=Mock(return_value=page()))
    with agent_module.Agent(None, "Click Go", rank_goal="the whole task", browser=tab) as agent:
        assert agent.browser is tab
    tab.set_goal.assert_called_once_with("the whole task")
    tab.close.assert_not_called()
    tab.call.assert_not_called()  # no navigation: the page stays as the last step left it


# --- a passive page change re-arms the stop gate -----------------------------------------


def test_a_page_that_changed_by_itself_rearms_the_gate(runner, monkeypatch):
    runner.state["decision"] = stop_decision("DONE", 0.3)
    runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    assert runner.state["stop_rechecked"] is True
    moved = deepcopy(runner.state["page"])
    moved["text"] = "Search results arrived late"
    moved["fingerprint"] = fingerprint(moved)
    runner.state["browser"].fresh.return_value = False
    runner.state["browser"].observe.return_value = moved
    runner.state["started_at"] = time.perf_counter()
    monkeypatch.setattr(loop, "decide", lambda *_a: stop_decision("DONE", 0.3))
    runner.command("predict")
    assert runner.state["page"] is moved and runner.state["stop_rechecked"] is False
    runner.state["browser"].fresh.return_value = True
    runner.command("act", {"fingerprint": moved["fingerprint"]})
    assert runner.state["status"] == "ready" and len(runner.state["rechecks"]) == 2


def test_a_reobserve_of_the_same_page_does_not_rearm_the_gate(runner, monkeypatch):
    runner.state["decision"] = stop_decision("BLOCKED", 0.3)
    runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    runner.state["browser"].fresh.return_value = False
    runner.state["started_at"] = time.perf_counter()
    monkeypatch.setattr(loop, "decide", lambda *_a: stop_decision("BLOCKED", 0.3))
    runner.command("predict")
    assert runner.state["stop_rechecked"] is True


# --- the planner's DONE is checked on the page ----------------------------------------------


def done_pages():
    start = {"final_url": "https://w/start", "title": "Start", "text": "", "links": []}
    return start, (lambda *_a: start), (lambda *_a: dict(start, final_url="https://w/next"))


def test_a_done_the_page_check_rejects_goes_back_to_the_planner():
    _start, look, step = done_pages()
    probabilities = [0.4, 0.95]
    planner = FakePlanner(["DONE ok", "CLICK Next", "DONE ok"])
    out = plan.run("Reach next", "https://w/start", planner, step, look, 60,
                   verify=lambda _task: probabilities.pop(0))
    assert planner.prompts[1]["note"] == plan.NOT_COMPLETE
    assert "note" not in planner.prompts[2]
    assert out["plan"]["stopped"] == "planner said DONE"
    assert [c["probability"] for c in out["plan"]["done_checks"]] == [0.4, 0.95]
    assert out["plan"]["done_threshold"] == 0.9


def test_a_done_that_never_passes_ends_at_the_cap_with_the_reason():
    _start, look, step = done_pages()
    planner = FakePlanner(["DONE ok"] * plan.MAX_TURNS)
    out = plan.run("Reach next", "https://w/start", planner, step, look, 60, verify=lambda _task: None)
    assert out["plan"]["stopped"].startswith("reached the %d-turn cap" % plan.MAX_TURNS)
    assert plan.NOT_COMPLETE in out["plan"]["stopped"]
    assert len(out["plan"]["done_checks"]) == plan.MAX_TURNS
    assert not any(c["passed"] for c in out["plan"]["done_checks"])


def test_the_page_check_is_one_noul_with_the_task_as_data(monkeypatch):
    sent = []

    def post(body):
        sent.append(body)
        return {"answers": {"task_complete": {"type": "noul", "noul": 0.93}}}, "https"

    monkeypatch.setattr(model, "_post", post)
    out = model.task_complete(page(), "Find a book")
    assert out["probability"] == 0.93
    (question,) = sent[0]["questions"].values()
    assert question["type"] == "noul"
    assert question["instructions"] == {"question": model.TASK_COMPLETE, "task": "Find a book"}
    assert "`task`" in model.TASK_COMPLETE


class _FakeChild:
    def __init__(self):
        self.terminated = False

    def alive(self):
        return not self.terminated

    def terminate(self):
        self.terminated = True


class _SyncThread:
    def __init__(self, target, daemon=None):
        self._target = target

    def start(self):
        self._target()


def _standing(monkeypatch, spawn):
    from jev_ultrafast import text_model_claude_standing as standing

    monkeypatch.setattr(standing.threading, "Thread", _SyncThread)
    model = standing.StandingTextModel()
    monkeypatch.setattr(model, "_spawn", lambda: spawn(model))
    return model


def test_a_waiting_replacement_is_reused_not_doubled(monkeypatch):
    spawned = []

    def spawn(_model):
        spawned.append(_FakeChild())
        return spawned[-1]

    model = _standing(monkeypatch, spawn)
    model.new_session()
    model.new_session()
    assert len(spawned) == 1
    assert model._next is spawned[0] and not spawned[0].terminated


def test_a_replacement_that_loses_the_race_is_stopped(monkeypatch):
    waiting = _FakeChild()
    spawned = []

    def spawn(model):
        # Another replacement lands while this one is still starting.
        model._next = waiting
        spawned.append(_FakeChild())
        return spawned[-1]

    model = _standing(monkeypatch, spawn)
    model.new_session()
    assert model._next is waiting
    assert spawned[0].terminated
