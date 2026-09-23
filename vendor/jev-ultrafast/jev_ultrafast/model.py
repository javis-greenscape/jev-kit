"""TypeSafe makes choices; an optional small OpenAI-compatible model writes field values."""

import json
import math
import os
import socket
import time
import uuid

import httpx

from .questions import NEXT_ACTION, TARGET, TEXT_VALUE

# One client for the process, so HTTP/2 connection reuse survives between
# decisions. It only helps a process that outlives a single run: a fresh
# interpreter per call pays DNS, TCP and TLS again every time.
CLIENT = httpx.Client(http2=True, timeout=25)

# Optional local transport for the systemone request. jev-kit runs a daemon
# that already holds pooled keep-alive connections to the same endpoint and
# adds the Authorization header itself; when the server hands us its socket
# path we go through it, and we fall back to the direct HTTPS call above on
# anything at all. Nothing from that project is imported here: the protocol is
# one JSON line each way.
SYSTEMONE_SOCKET_ENV = "JEV_SYSTEMONE_SOCKET"
SOCKET_CONNECT_TIMEOUT = 0.2


def systemone_socket():
    return os.environ.get(SYSTEMONE_SOCKET_ENV) or ""


def post_via_socket(body, timeout=25):
    """The response dict from the local daemon, or None to use HTTPS.

    Never raises: a missing socket, a refused connection, a malformed reply
    and a daemon-reported failure all come back as None."""
    path = systemone_socket()
    if not path or not hasattr(socket, "AF_UNIX"):
        return None
    sock = None
    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(SOCKET_CONNECT_TIMEOUT)
        sock.connect(path)
        sock.settimeout(timeout + 0.5)
        request = {"id": uuid.uuid4().hex, "body": body, "timeout_s": timeout}
        sock.sendall((json.dumps(request) + "\n").encode())
        line = sock.makefile("rb").readline()
        if not line:
            return None
        answer = json.loads(line.decode())
        return answer.get("response") if answer.get("ok") else None
    except Exception:
        return None
    finally:
        if sock is not None:
            try:
                sock.close()
            except Exception:
                pass


def post_json(url, key, body):
    for attempt in range(3):
        try:
            response = CLIENT.post(url, json=body, headers={"Authorization": f"Bearer {key}"})
        except httpx.HTTPError:
            raise RuntimeError("Model connection failed; no action executed.") from None
        if response.status_code in {429, 529, 503} and attempt < 2:
            time.sleep(0.5 * 2**attempt)
            continue
        if response.is_error:
            raise RuntimeError(f"Model provider returned HTTP {response.status_code}; no action executed.")
        return response.json()
    raise RuntimeError("Model unavailable")


def validate_choice(answer, ids):
    try:
        probabilities = answer["probabilities"]
        numbers = [*probabilities.values(), answer["confidence"]]
        valid = (
            answer["choice"] in ids
            and set(probabilities) == set(ids)
            and all(type(n) in (int, float) and math.isfinite(n) and 0 <= n <= 1 for n in numbers)
            and abs(sum(probabilities.values()) - 1) < 0.02
            and probabilities[answer["choice"]] >= max(probabilities.values()) - 1e-6
        )
    except (KeyError, TypeError, ValueError):
        valid = False
    if not valid:
        raise ValueError(INVALID_DECISION)
    return answer


def action_space(actions):
    """One index per observed element; each operation has its own valid target choices."""
    elements, indices, targets, controls = [], {}, {}, {}
    operations = {"click": "CLICK", "fill": "TYPE_TEXT", "select": "SELECT"}
    for action in actions:
        kind = action["kind"]
        if kind not in operations:
            controls[action["id"].upper()] = action
            continue
        node = action["node"]
        if node not in indices:
            index = str(len(elements) + 1)
            indices[node] = index
            element = {k: action[k] for k in ("role", "value", "checked", "selected", "expanded") if k in action}
            element.update(index=index, label=action["label"].split(" → ")[0], operations=[])
            if kind == "select":
                element["value"] = action.get("current_value", "")
                element["options"] = []
            elements.append(element)
        index = indices[node]
        operation = operations[kind]
        group = targets.setdefault(operation, {})
        element = elements[int(index) - 1]
        if operation not in element["operations"]:
            element["operations"].append(operation)
        target = index
        if kind == "select":
            target = f"{index}:{len(element['options']) + 1}"
            element["options"].append({"index": target, "label": action["label"], "value": action["value"]})
        group[target] = action
    return elements, targets, controls


# TypeSafe's own limit on a Choice question: "You can have a maximum of 255 options per
# Choice" (docs/jev-reference/typesafe-docs/api.md, the `criteria` field of a Choice). A
# request that offers more is rejected with 422 and no action is executed at all, so the
# whole run dies on a page the executor could otherwise have handled. The viewport budget in
# snapshot.js caps the element table at 250 rows, which keeps the CLICK and TYPE_TEXT heads
# inside the limit, but the SELECT head has one option per observed dropdown choice: two
# country pickers on one page are already past 255. Trim each head to what the API accepts,
# in the order action_space() built it, and say how many were dropped.
MAX_CHOICE_OPTIONS = 255


def cap_targets(targets):
    """(targets trimmed to MAX_CHOICE_OPTIONS per head, {operation: how many were dropped})."""
    capped, dropped = {}, {}
    for operation, candidates in targets.items():
        if len(candidates) <= MAX_CHOICE_OPTIONS:
            capped[operation] = candidates
            continue
        capped[operation] = dict(list(candidates.items())[:MAX_CHOICE_OPTIONS])
        dropped[operation] = len(candidates) - MAX_CHOICE_OPTIONS
    return capped, dropped


def choose(state, goal, history):
    elements, targets, controls = action_space(state["actions"])
    targets, omitted_targets = cap_targets(targets)
    labels = {
        "CLICK": "Click an element, button, menu option, autocomplete suggestion, or calendar day.",
        "TYPE_TEXT": "Enter or replace text in an editable field. A small LLM will supply the value from the goal.",
        "SELECT": "Select an observed dropdown value.",
    }
    operations = {key: labels[key] for key in targets}
    operations.update({key: value["label"] for key, value in controls.items()})
    operations.update(DONE="Every requirement is visibly satisfied.", BLOCKED="No supported operation can progress.")
    questions = {
        "operation": {"type": "choice", "criteria": operations, "instructions": {"goal": goal, "rules": NEXT_ACTION}}
    }
    for operation, candidates in targets.items():
        questions[operation.lower() + "_target"] = {
            "type": "choice",
            "criteria": {
                index: {
                    "element": f"[{index}] {a['label']}",
                    "current_value": a.get("current_value", a.get("value", "")),
                    **{k: a[k] for k in ("role", "checked", "selected", "expanded") if k in a},
                }
                for index, a in candidates.items()
            },
            "instructions": {"goal": goal, "operation": operation, "rules": [NEXT_ACTION, TARGET]},
        }
    body = {
        "model": os.environ.get("TYPESAFE_MODEL", "jev-latest"),
        "state": {
            "page": {k: state[k] for k in ("url", "title", "text")},
            "elements": elements,
            "recent_actions": [
                {k: h.get(k) for k in ("action", "kind", "text", "page_changed")} for h in history[-10:]
            ],
        },
        "questions": questions,
    }
    started = time.perf_counter()
    result = post_via_socket(body)
    transport = "daemon"
    if result is None:
        transport = "https"
        result = post_json("https://api.typesafe.ai/v1/systemone", os.environ["TYPESAFE_API_KEY"], body)
    operation_answer = validate_choice(result["answers"].get("operation", {}), operations)
    operation = operation_answer["choice"]
    target = None
    target_answer = None
    probabilities = {}
    if operation in targets:
        # Unused target heads cannot cause an action. Validate the head selected by the operation.
        target_answer = validate_choice(result["answers"].get(operation.lower() + "_target", {}), targets[operation])
        target = target_answer["choice"]
        choice = targets[operation][target]["id"]
        probabilities = {a["id"]: target_answer["probabilities"][index] for index, a in targets[operation].items()}
    else:
        choice = controls[operation]["id"] if operation in controls else operation
        probabilities[choice] = operation_answer["probabilities"][operation]
    return {
        "choice": choice,
        "operation": operation,
        "target": target,
        "confidence": operation_answer["confidence"],
        "probabilities": probabilities,
        "operation_probabilities": operation_answer["probabilities"],
        "target_probabilities": target_answer["probabilities"] if target_answer else {},
        "target_confidence": target_answer["confidence"] if target_answer else None,
        "raw_answers": result["answers"],
        "model": result["model"],
        "usage": result.get("usage", {}),
        "transport": transport,
        "latency_ms": round((time.perf_counter() - started) * 1000),
        "omitted_targets": omitted_targets,
        "request": body,
    }


INVALID_DECISION = "Invalid TypeSafe response; no action executed."
NO_FIELD_VALUE = "Text helper returned no valid field value; nothing typed."


def _decide_once(page, goal, history):
    provider = os.environ.get("DECISION_PROVIDER", "jev")
    if provider == "jev":
        return choose(state=page, goal=goal, history=history)
    from .decision_claude import decide as claude_decide

    return claude_decide(page, goal, history, provider)


def decide(page, goal, history):
    """Dispatch to the decision-maker selected by DECISION_PROVIDER (default: jev, unchanged).

    jev_ultrafast.agent.Agent calls this instead of choose() directly, so the Jev/TypeSafe path
    (choose(), above) is untouched: DECISION_PROVIDER unset or "jev" is exactly the old
    behaviour. "claude-haiku"/"claude-sonnet" route to jev_ultrafast.decision_claude instead,
    a standing `claude -p` child asked for the same operation+target decision in strict JSON,
    given the same element table. See SPIKE-NOTES.md, "Jev versus Claude as decision-maker".

    A malformed answer is asked again, once. Upstream's rule that a browser mutation is never
    retried is untouched: this retry happens before anything is executed, because the answer
    was rejected before it could name an action. A second malformed answer is raised.
    """
    for attempt in (0, 1):
        try:
            return _decide_once(page, goal, history)
        except ValueError as exc:
            if attempt or INVALID_DECISION not in str(exc):
                raise
    raise AssertionError("unreachable")


def field_context(goal, action, page, history):
    """TYPE_TEXT context for the text helper. TEXT_MODEL_CONTEXT selects the shape:

    - "trimmed" (default, measured 2026-09-19, see SPIKE-NOTES.md "Thinking off and trimmed
      context"): goal, the chosen element's own element-table row, its 5 nearest rows (by
      position in the same first-encounter order action_space() uses), page title and url. No
      page body text, no recent_actions — the field's name/role/value plus its neighbours are
      almost always enough to derive the value, and dropping ~6000 chars of page text is most of
      why this shape is fast.
    - "full": the original shape — goal, field {label, role, value}, page {title, text[:6000]},
      last 6 recent_actions. Kept behind the switch as the fallback if a page ever needs body
      text the trimmed shape can't see (e.g. a value that must be copied from prose on the page).
    """
    if os.environ.get("TEXT_MODEL_CONTEXT", "trimmed") == "full":
        return {
            "goal": goal,
            "field": {k: action.get(k) for k in ("label", "role", "value")},
            "page": {"title": page["title"], "text": page["text"][:6000]},
            "recent_actions": [{k: h.get(k) for k in ("action", "text")} for h in history[-6:]],
        }
    elements, _, _ = action_space(page["actions"])
    order, seen = [], set()
    for a in page["actions"]:
        node = a.get("node")
        # Pseudo-actions (wait/scroll) carry no node and never appear in `elements`; skip them.
        if node is not None and node not in seen:
            seen.add(node)
            order.append(node)
    position = order.index(action["node"])
    nearest = sorted((i for i in range(len(elements)) if i != position), key=lambda i: (abs(i - position), i))[:5]
    return {
        "goal": goal,
        "field": elements[position],
        "nearby_fields": [elements[i] for i in sorted(nearest)],
        "page": {"title": page["title"], "url": page["url"]},
    }


def field_text(context):
    """The field value, asking again once if the helper answers with nothing usable.

    Same reasoning as decide(): nothing has been typed when this raises, so the retry cannot
    repeat an input. Anything else — a missing key, a transport failure — is raised at once."""
    for attempt in (0, 1):
        try:
            return _field_text_once(context)
        except ValueError as exc:
            if attempt or NO_FIELD_VALUE not in str(exc):
                raise
    raise AssertionError("unreachable")


def _field_text_once(context):
    if os.environ.get("TEXT_MODEL_PROVIDER") == "claude-standing":
        # Standing-process adapter: one long-lived `claude` CLI child instead of a spawn per
        # call. See jev_ultrafast/text_model_claude_standing.py and SPIKE-NOTES.md.
        from .text_model_claude_standing import field_text as claude_standing_field_text

        return claude_standing_field_text(context)
    if os.environ.get("TEXT_MODEL_PROVIDER") == "claude-cli":
        # Spike-only adapter: no Anthropic/OpenRouter key on this box, OAuth via `claude` CLI instead.
        # See jev_ultrafast/text_model_claude.py and SPIKE-NOTES.md.
        from .text_model_claude import field_text as claude_field_text

        return claude_field_text(context)
    key = os.environ.get("TEXT_MODEL_API_KEY")
    if not key:
        raise ValueError("TYPE_TEXT needs TEXT_MODEL_API_KEY; no text is hardcoded or guessed by the executor.")
    base = os.environ.get("TEXT_MODEL_BASE_URL", "https://api.deepseek.com/v1").rstrip("/")
    model = os.environ.get("TEXT_MODEL", "deepseek-chat")
    reasoning = {"thinking": {"type": "disabled"}} if "api.deepseek.com/" in base else {"reasoning": {"effort": "low"}}
    if os.environ.get("TEXT_MODEL_REASONING") == "none":
        reasoning = {"reasoning": {"enabled": False}}
    started = time.perf_counter()
    result = post_json(
        base + "/chat/completions",
        key,
        {
            "model": model,
            "max_tokens": 1024,
            "response_format": {"type": "json_object"},
            **reasoning,
            "messages": [
                {"role": "system", "content": TEXT_VALUE},
                {
                    "role": "user",
                    "content": json.dumps(context),
                },
            ],
        },
    )
    try:
        output = json.loads(result["choices"][0]["message"]["content"])
        value = output["text"]
        if set(output) != {"text"} or not isinstance(value, str) or not value.strip() or len(value) > 2000:
            raise ValueError()
    except (ValueError, KeyError, TypeError):
        raise ValueError(NO_FIELD_VALUE) from None
    return value, {
        "model": model,
        "latency_ms": round((time.perf_counter() - started) * 1000),
        "usage": result.get("usage", {}),
    }
