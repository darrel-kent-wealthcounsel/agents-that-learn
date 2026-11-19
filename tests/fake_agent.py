"""Stands in for Claude Code in the tests: prints a stream-json event stream and edits one file."""
import json
import sys

prompt = sys.argv[-1]
looks = 2 if "code map" in prompt else 5
say = lambda ev: print(json.dumps(ev), flush=True)
say({"type": "system", "subtype": "init"})
for n in range(looks):
    say({"type": "assistant", "message": {"id": f"m{n}", "usage": {"input_tokens": 1000, "output_tokens": 50},
         "content": [{"type": "tool_use", "name": "Grep", "input": {"pattern": "x"}}]}})
with open("a.js", "a") as f:
    f.write("// edited\n")
say({"type": "assistant", "message": {"id": "edit", "usage": {"input_tokens": 1000, "output_tokens": 80},
     "content": [{"type": "tool_use", "name": "Edit", "input": {"file_path": "a.js"}}]}})
say({"type": "result", "subtype": "success", "is_error": False, "num_turns": looks + 1,
     "total_cost_usd": 0.01 * (looks + 1),
     "usage": {"input_tokens": 1000 * (looks + 1), "output_tokens": 50 * looks + 80}})
