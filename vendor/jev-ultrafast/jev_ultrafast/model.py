"""TypeSafe makes choices; an optional small OpenAI-compatible model writes field values."""

import json
import math
import os
import time

import httpx

from .questions import NEXT_ACTION, TARGET, TEXT_VALUE

CLIENT = httpx.Client(http2=True, timeout=25)


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
        raise ValueError("Invalid TypeSafe response; no action executed.")
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


def choose(state, goal, history):
    elements, targets, controls = action_space(state["actions"])
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
        "latency_ms": round((time.perf_counter() - started) * 1000),
        "request": body,
    }


def decide(page, goal, history):
    """Dispatch to the decision-maker selected by DECISION_PROVIDER (default: jev, unchanged).

    jev_ultrafast.agent.Agent calls this instead of choose() directly, so the Jev/TypeSafe path
    (choose(), above) is untouched: DECISION_PROVIDER unset or "jev" is exactly the old
    behaviour. "claude-haiku"/"claude-sonnet" route to jev_ultrafast.decision_claude instead,
    a standing `claude -p` child asked for the same operation+target decision in strict JSON,
    given the same element table. See SPIKE-NOTES.md, "Jev versus Claude as decision-maker".
    """
    provider = os.environ.get("DECISION_PROVIDER", "jev")
    if provider == "jev":
        return choose(state=page, goal=goal, history=history)
    from .decision_claude import decide as claude_decide

    return claude_decide(page, goal, history, provider)


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
        raise ValueError("Text helper returned no valid field value; nothing typed.") from None
    return value, {
        "model": model,
        "latency_ms": round((time.perf_counter() - started) * 1000),
        "usage": result.get("usage", {}),
    }
