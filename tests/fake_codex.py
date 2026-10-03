#!/usr/bin/env python3
"""Stand-in for `codex exec`, used to test the Codex adapter without the real CLI.

Emits a JSONL event stream like `codex exec --json` and writes the final message
to the -o file. Reads the canned final answer from $FAKE_CODEX_ANSWER and records
the argv it received in $FAKE_CODEX_ARGV_LOG.
"""
import json
import os
import sys

args = sys.argv[1:]
with open(os.environ["FAKE_CODEX_ARGV_LOG"], "a") as log:
    log.write(json.dumps(args) + "\n")

assert args[0] == "exec", args
out = args[args.index("-o") + 1]
schema = args[args.index("--output-schema") + 1]
json.load(open(schema))  # must be valid JSON

if os.environ.get("FAKE_CODEX_FAIL"):
    print(json.dumps({"type": "thread.started", "thread_id": "t-1"}))
    print(json.dumps({"type": "turn.failed", "error": {"message": "usage limit reached"}}))
    sys.exit(1)

print(json.dumps({"type": "thread.started", "thread_id": "t-1"}))
print(json.dumps({"type": "turn.started"}))
print(json.dumps({"type": "item.completed", "item": {"id": "i0", "type": "agent_message", "text": "done"}}))
print(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 120000, "cached_input_tokens": 100000, "output_tokens": 4000}}))
with open(out, "w") as f:
    f.write(open(os.environ["FAKE_CODEX_ANSWER"]).read())
