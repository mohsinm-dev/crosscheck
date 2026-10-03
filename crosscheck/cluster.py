"""Group A's and B's findings that talk about the same thing.

Two strategies:
  heuristic  free, deterministic: word overlap + same file + overlapping lines
  llm        a cheap model (default claude:haiku) also detects contradictions

Either way the output is a list of {"members": [ids], "relation": "same"|"conflict"}.
Findings not in any cluster are unique to their analyst.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from . import prompts, schemas
from .agents import AgentSpec, run_agent
from .evidence import clean_rel, parse_location

_STOP = set("""the and for that this with from are was were has have had not but its into
than then them they their there which when what where who will would can could should
does did done also only just any all some more most other such each per via using used
use set sets value values file files line lines code""".split())

THRESHOLD = 0.6


def _tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9_]+", text.lower()) if (len(t) >= 3 or t.isdigit()) and t not in _STOP}


def _spans(f: dict, alt_roots) -> list[tuple[str, int | None, int | None]]:
    out = []
    for ev in f.get("evidence", []):
        if ev.get("type") == "file":
            path, s, e = parse_location(ev.get("location", ""))
            out.append((clean_rel(path, alt_roots), s, e))
    return out


def _score(a: dict, b: dict, alt_roots) -> float:
    ta, tb = _tokens(a["claim"]), _tokens(b["claim"])
    jac = len(ta & tb) / len(ta | tb) if ta | tb else 0.0
    sa, sb = _spans(a, alt_roots), _spans(b, alt_roots)
    same_file = any(pa == pb for pa, _, _ in sa for pb, _, _ in sb)
    overlap = any(
        pa == pb and s1 is not None and s2 is not None and s1 <= (e2 or s2) and s2 <= (e1 or s1)
        for pa, s1, e1 in sa for pb, s2, e2 in sb
    )
    return jac + (0.3 if same_file else 0) + (0.3 if overlap else 0)


_NEG = {"not", "never", "no", "missing", "absent", "without", "isn't", "doesn't", "lacks", "disabled", "false", "off"}
_POS_FOR = {"false": "true", "disabled": "enabled", "off": "on"}


def _polarity_differs(a: str, b: str) -> bool:
    """Crude contradiction check: 'rolling: true' vs 'rolling: false', 'X is set' vs 'X is never set'."""
    wa, wb = set(re.findall(r"[a-z']+", a.lower())), set(re.findall(r"[a-z']+", b.lower()))
    for neg, pos in _POS_FOR.items():
        if (neg in wa and pos in wb) or (neg in wb and pos in wa):
            return True
    return bool(wa & _NEG) != bool(wb & _NEG)


def cluster_heuristic(fa: list[dict], fb: list[dict], alt_roots=()) -> list[dict]:
    pairs = sorted(
        ((_score(a, b, alt_roots), a["id"], b["id"]) for a in fa for b in fb),
        reverse=True,
    )
    claims = {f["id"]: f["claim"] for f in fa + fb}
    used, clusters = set(), []
    for score, ia, ib in pairs:
        if score < THRESHOLD:
            break
        if ia in used or ib in used:
            continue
        used |= {ia, ib}
        rel = "conflict" if _polarity_differs(claims[ia], claims[ib]) else "same"
        clusters.append({"members": [ia, ib], "relation": rel})
    return clusters


def cluster_llm(fa, fb, spec: AgentSpec, run_dir: Path, budget_usd: float = 0.5) -> tuple[list[dict] | None, float | None, str | None]:
    items = [
        {"id": f["id"], "claim": f["claim"],
         "locations": [ev.get("location", "") for ev in f.get("evidence", [])]}
        for f in fa + fb
    ]
    res = run_agent(
        spec, prompt=prompts.CLUSTER.format(findings=json.dumps(items, indent=2)),
        schema=schemas.CLUSTERS, cwd=run_dir, phase="cluster", raw_dir=run_dir / "raw",
        label="clusterer", budget_usd=budget_usd, timeout_s=300,
    )
    if not res.ok:
        return None, res.cost_usd, res.error
    valid_a, valid_b = {f["id"] for f in fa}, {f["id"] for f in fb}
    used, clusters = set(), []
    for c in res.data.get("clusters", []):
        members = [m for m in c.get("members", []) if (m in valid_a or m in valid_b) and m not in used]
        if any(m in valid_a for m in members) and any(m in valid_b for m in members):
            used |= set(members)
            clusters.append({"members": members, "relation": c.get("relation", "same")})
    return clusters, res.cost_usd, None
