"""JSON Schemas the agents must answer in.

They are written to satisfy both tools' structured-output modes:
every object lists all of its properties as required and sets
additionalProperties to false (Codex/OpenAI strict mode requires this;
Claude Code accepts it).
"""

EVIDENCE = {
    "type": "object",
    "additionalProperties": False,
    "required": ["type", "location", "quote"],
    "properties": {
        "type": {"type": "string", "enum": ["file", "url", "command"]},
        "location": {
            "type": "string",
            "description": (
                "file: repo-relative path with optional line range, e.g. "
                "'src/app.ts:40-58' or 'src/app.ts:12'. url: the full URL. "
                "command: the exact command you ran."
            ),
        },
        "quote": {
            "type": "string",
            "description": (
                "Text copied VERBATIM from the file/page/command output that "
                "supports the claim. Empty string only for claims about absence."
            ),
        },
    },
}

FINDING = {
    "type": "object",
    "additionalProperties": False,
    "required": ["id", "claim", "kind", "confidence", "evidence", "would_refute"],
    "properties": {
        "id": {"type": "string", "description": "F-1, F-2, ..."},
        "claim": {"type": "string", "description": "One atomic, checkable statement."},
        "kind": {
            "type": "string",
            "enum": ["cause", "bug", "risk", "fact", "recommendation", "open_question"],
        },
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        "evidence": {"type": "array", "items": EVIDENCE},
        "would_refute": {
            "type": "string",
            "description": "What observation would show this claim is wrong.",
        },
    },
}

FINDINGS = {
    "type": "object",
    "additionalProperties": False,
    "required": ["findings", "coverage"],
    "properties": {
        "findings": {"type": "array", "items": FINDING},
        "coverage": {
            "type": "object",
            "additionalProperties": False,
            "required": ["examined", "not_examined"],
            "properties": {
                "examined": {"type": "array", "items": {"type": "string"}},
                "not_examined": {"type": "array", "items": {"type": "string"}},
            },
        },
    },
}

VERDICTS = {
    "type": "object",
    "additionalProperties": False,
    "required": ["verdicts"],
    "properties": {
        "verdicts": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["target_id", "verdict", "new_evidence", "reason"],
                "properties": {
                    "target_id": {"type": "string"},
                    "verdict": {
                        "type": "string",
                        "enum": ["confirmed", "refuted", "partially_confirmed", "cannot_verify"],
                    },
                    "new_evidence": {"type": "array", "items": EVIDENCE},
                    "reason": {"type": "string"},
                },
            },
        }
    },
}

CLUSTERS = {
    "type": "object",
    "additionalProperties": False,
    "required": ["clusters"],
    "properties": {
        "clusters": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["members", "relation"],
                "properties": {
                    "members": {"type": "array", "items": {"type": "string"}},
                    "relation": {"type": "string", "enum": ["same", "conflict"]},
                },
            },
        }
    },
}
