---
name: eval
description: Measure whether crosscheck is worth its cost on this codebase by running it on past bugs whose root causes are already known, and comparing against a single agent given twice the budget. Use only when the user asks to evaluate or benchmark crosscheck.
argument-hint: <path to cases.json> [--baseline claude|none] [--rounds 0|1]
disable-model-invocation: true
---

# Evaluate crosscheck

Cases file and options: `$ARGUMENTS`

## If the user has no cases file yet

Help them write one. Each case is a past bug or question with a known answer. Ask for 10–20 real ones; fewer won't support conclusions. Format (JSON list, `repo` is relative to the cases file):

```json
[
  {
    "id": "logout-bug",
    "repo": "../",
    "question": "Why are users logged out after about 10 minutes?",
    "ground_truth": [
      {"id": "redis-ttl", "summary": "Redis store ttl is 600s",
       "files": ["src/config/session.ts"], "keywords": ["ttl: 600", "600 seconds"]}
    ]
  }
]
```

A finding counts as finding a known cause when it cites one of the cause's `files` and its claim or quoted text contains one of the `keywords`. Keep keywords specific (`"rolling: false"`, not `"rolling"`), or wrong claims will get credit. Point `repo` at a checkout of the commit *before* the fix.

Warn the user before starting: every case runs both agents plus a 2x-budget baseline, so 15 cases can cost tens of dollars and take an hour or more.

## Run

Use the Bash tool with `run_in_background: true`:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/bin/crosscheck" eval <cases.json> [--baseline claude|none] [--rounds 0|1]
```

The last stdout line is the path to `eval_report.md`. Read it and give the user the headline numbers:
- in how many cases the second agent added a true cause the first missed,
- recall for A alone, B alone, crosscheck, and the 2x-budget baseline,
- true causes wrongly rejected (should be 0),
- total cost.

Then state plainly what it implies: if crosscheck isn't clearly ahead of the 2x-budget baseline, the second agent is mostly extra spend on this codebase, and it's better reserved for high-stakes questions.
