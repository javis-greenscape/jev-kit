"""uv run --env-file .env python examples/run.py --url URL --goal 'A narrow goal'"""

import argparse
import os

from jev_ultrafast import Agent

parser = argparse.ArgumentParser()
parser.add_argument("--url", required=True)
parser.add_argument("--goal", action="append", required=True, help="Repeat for an ordered list of goals.")
args = parser.parse_args()

if os.environ.get("TEXT_MODEL_PROVIDER") == "claude-standing":
    # Start the standing text-model child now, while the first page loads, so the first
    # TYPE_TEXT step doesn't pay its cold-start cost. See text_model_claude_standing.py.
    from jev_ultrafast.text_model_claude_standing import warm

    warm()

with Agent(args.url, args.goal) as agent:
    for state in agent.run():
        print(f"{state['elapsed_ms']:>5} ms  {len(state['history'])} actions  {state['status']}")
    print(state["page"]["url"])
