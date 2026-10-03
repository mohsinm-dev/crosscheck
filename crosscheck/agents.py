"""Adapters that run one agent once, headless, and return schema-shaped JSON.

Agent specs:
  claude              Claude Code, default model
  claude:sonnet       Claude Code with --model sonnet (any --model value works)
  codex               Codex CLI, default model
  codex:gpt-5.3-codex Codex CLI with -m <model>
  mock:<dir>          Canned responses from <dir> (tests and demos)

Every call starts a FRESH session. Nothing carries over between phases or agents,
which is what keeps the first pass blind.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

READ_TOOLS = "Read,Grep,Glob"
WEB_TOOLS = "WebSearch,WebFetch"


@dataclass
class AgentResult:
    ok: bool
    data: dict | None = None
    error: str | None = None
    cost_usd: float | None = None
    tokens: dict = field(default_factory=dict)
    duration_s: float = 0.0
    raw_path: str | None = None


@dataclass
class AgentSpec:
    kind: str  # claude | codex | mock
    arg: str | None = None  # model name, or mock dir

    @classmethod
    def parse(cls, spec: str) -> "AgentSpec":
        kind, _, arg = spec.partition(":")
        kind = kind.strip().lower()
        if kind not in ("claude", "codex", "mock"):
            raise ValueError(f"unknown agent '{spec}' (use claude[:model], codex[:model] or mock:<dir>)")
        if kind == "mock" and not arg:
            raise ValueError("mock agent needs a directory: mock:<dir>")
        return cls(kind, arg or None)

    @property
    def display(self) -> str:
        if self.kind == "mock":
            return f"mock({Path(self.arg).name})"
        return f"{self.kind}:{self.arg}" if self.arg else self.kind


def run_agent(
    spec: AgentSpec,
    *,
    prompt: str,
    schema: dict,
    cwd: Path,
    phase: str,
    raw_dir: Path,
    label: str,
    budget_usd: float = 3.0,
    timeout_s: int = 1200,
    allow_web: bool = False,
    claims: list[dict] | None = None,
) -> AgentResult:
    raw_dir.mkdir(parents=True, exist_ok=True)
    (raw_dir / f"{label}_{phase}.prompt.txt").write_text(prompt)
    start = time.monotonic()
    try:
        if spec.kind == "claude":
            res = _run_claude(spec, prompt, schema, cwd, phase, raw_dir, label, budget_usd, timeout_s, allow_web)
        elif spec.kind == "codex":
            res = _run_codex(spec, prompt, schema, cwd, phase, raw_dir, label, timeout_s)
        else:
            res = _run_mock(spec, phase, claims)
    except subprocess.TimeoutExpired:
        res = AgentResult(ok=False, error=f"timed out after {timeout_s}s")
    except FileNotFoundError as e:
        res = AgentResult(ok=False, error=f"executable not found: {e.filename}")
    res.duration_s = round(time.monotonic() - start, 1)
    return res


# --------------------------------------------------------------------- claude

def _run_claude(spec, prompt, schema, cwd, phase, raw_dir, label, budget_usd, timeout_s, allow_web):
    tools = READ_TOOLS + ("," + WEB_TOOLS if allow_web else "") if phase != "cluster" else ""
    cmd = [
        os.environ.get("CROSSCHECK_CLAUDE_BIN", "claude"),
        "-p",
        "--output-format", "json",
        "--json-schema", json.dumps(schema),
        "--tools", tools,
        "--permission-mode", "dontAsk",
        "--no-session-persistence",
        # Ignore the repo's own .claude/settings.json (hooks!) and .mcp.json.
        "--setting-sources", "user",
        "--strict-mcp-config",
        "--max-budget-usd", f"{budget_usd:.2f}",
    ]
    if tools:
        cmd += ["--allowedTools", tools]
    if spec.arg:
        cmd += ["--model", spec.arg]
    # Prompt goes over stdin so variadic flags can't swallow it.
    proc = subprocess.run(cmd, input=prompt, capture_output=True, text=True, cwd=cwd, timeout=timeout_s)
    raw_path = raw_dir / f"{label}_{phase}.claude.json"
    raw_path.write_text(proc.stdout + ("\n--- stderr ---\n" + proc.stderr if proc.stderr else ""))

    try:
        out = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return AgentResult(ok=False, error=f"claude exited {proc.returncode}: {proc.stderr.strip()[:300] or 'no JSON output'}",
                           raw_path=str(raw_path))

    cost = out.get("total_cost_usd")
    usage = out.get("usage") or {}
    tokens = {k: usage.get(k, 0) for k in ("input_tokens", "cache_read_input_tokens",
                                           "cache_creation_input_tokens", "output_tokens")}
    if out.get("is_error") or out.get("subtype") != "success":
        return AgentResult(ok=False, error=f"claude run ended '{out.get('subtype')}'", cost_usd=cost,
                           tokens=tokens, raw_path=str(raw_path))
    data = out.get("structured_output")
    if not isinstance(data, dict):
        # A "success" without structured output still counts as a failed run.
        return AgentResult(ok=False, error="claude returned no structured_output", cost_usd=cost,
                           tokens=tokens, raw_path=str(raw_path))
    return AgentResult(ok=True, data=data, cost_usd=cost, tokens=tokens, raw_path=str(raw_path))


# ---------------------------------------------------------------------- codex

def _codex_prices():
    """Optional list prices for cost estimates: CROSSCHECK_CODEX_PRICE='in,cached_in,out' in $ per 1M tokens."""
    raw = os.environ.get("CROSSCHECK_CODEX_PRICE")
    if not raw:
        return None
    try:
        p_in, p_cached, p_out = (float(x) for x in raw.split(","))
        return p_in, p_cached, p_out
    except ValueError:
        return None


def _run_codex(spec, prompt, schema, cwd, phase, raw_dir, label, timeout_s):
    schema_file = raw_dir / f"{label}_{phase}.schema.json"
    schema_file.write_text(json.dumps(schema))
    last_file = raw_dir / f"{label}_{phase}.codex.last.txt"
    cmd = [
        os.environ.get("CROSSCHECK_CODEX_BIN", "codex"), "exec",
        "--json",
        "--skip-git-repo-check",
        "--ephemeral",
        "-s", "read-only",  # exec never asks for approval; the sandbox is the only guard
        "-C", str(cwd),
        "--output-schema", str(schema_file),
        "-o", str(last_file),
    ]
    if spec.arg:
        cmd += ["-m", spec.arg]
    cmd.append(prompt)
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd, timeout=timeout_s, stdin=subprocess.DEVNULL)
    raw_path = raw_dir / f"{label}_{phase}.codex.events.jsonl"
    raw_path.write_text(proc.stdout + ("\n--- stderr ---\n" + proc.stderr if proc.stderr else ""))

    tokens = {"input_tokens": 0, "cached_input_tokens": 0, "output_tokens": 0}
    errors = []
    for line in proc.stdout.splitlines():
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if ev.get("type") == "turn.completed":
            for k in tokens:
                tokens[k] += int((ev.get("usage") or {}).get(k, 0) or 0)
        elif ev.get("type") in ("turn.failed", "error"):
            errors.append(json.dumps(ev)[:300])

    cost = None
    prices = _codex_prices()
    if prices:
        p_in, p_cached, p_out = prices
        uncached = max(tokens["input_tokens"] - tokens["cached_input_tokens"], 0)
        cost = round((uncached * p_in + tokens["cached_input_tokens"] * p_cached + tokens["output_tokens"] * p_out) / 1e6, 4)

    if errors or proc.returncode != 0:
        return AgentResult(ok=False, error=f"codex exited {proc.returncode}: {'; '.join(errors) or proc.stderr.strip()[:300]}",
                           cost_usd=cost, tokens=tokens, raw_path=str(raw_path))
    if not last_file.exists():
        return AgentResult(ok=False, error="codex wrote no final message", cost_usd=cost, tokens=tokens, raw_path=str(raw_path))
    data = _loads_lenient(last_file.read_text())
    if not isinstance(data, dict):
        return AgentResult(ok=False, error="codex final message was not valid JSON", cost_usd=cost, tokens=tokens,
                           raw_path=str(raw_path))
    return AgentResult(ok=True, data=data, cost_usd=cost, tokens=tokens, raw_path=str(raw_path))


def _loads_lenient(text: str):
    text = text.strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if m:
        text = m.group(1)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


# ----------------------------------------------------------------------- mock

def _run_mock(spec, phase, claims):
    """Canned responses for tests and demos.

    <dir>/research.json : a FindingsReport
    <dir>/verify.json   : {"by_claim": [{"match": "<substring of claim>", "verdict": ...,
                            "new_evidence": [...], "reason": "..."}]}
                          (claim IDs are anonymized at runtime, so mocks match on claim text)
    <dir>/cluster.json  : a Clusters object (only if this mock is used as the clusterer)
    Optional top-level "_cost_usd" in any file simulates spend.
    """
    d = Path(spec.arg)
    path = d / f"{phase}.json"
    if not path.exists():
        return AgentResult(ok=False, error=f"mock has no {path.name}")
    payload = json.loads(path.read_text())
    cost = payload.pop("_cost_usd", 0.0)
    if phase == "verify":
        verdicts = []
        for c in claims or []:
            rule = next((r for r in payload.get("by_claim", []) if r["match"].lower() in c["claim"].lower()), None)
            if rule is None:
                verdicts.append({"target_id": c["target_id"], "verdict": "cannot_verify", "new_evidence": [],
                                 "reason": "no mock rule"})
            else:
                verdicts.append({"target_id": c["target_id"], "verdict": rule["verdict"],
                                 "new_evidence": rule.get("new_evidence", []), "reason": rule.get("reason", "")})
        payload = {"verdicts": verdicts}
    return AgentResult(ok=True, data=payload, cost_usd=cost)


def check_available(spec: AgentSpec) -> str | None:
    """Return an error message if the agent's executable isn't installed."""
    if spec.kind == "mock":
        return None if Path(spec.arg).is_dir() else f"mock directory not found: {spec.arg}"
    exe = os.environ.get(f"CROSSCHECK_{spec.kind.upper()}_BIN", spec.kind)
    return None if shutil.which(exe) else f"'{exe}' is not installed or not on PATH"
