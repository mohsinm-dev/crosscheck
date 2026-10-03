"""Evaluation harness: does the second agent earn its cost?

Run crosscheck on past bugs whose root causes you already know, and compare:
  - what A found alone, what B found alone
  - what survived crosscheck (the verified ledger)
  - a single agent given 2x the research budget (the fair baseline)

Cases file (JSON list):
[
  {
    "id": "logout-bug",
    "repo": "examples/logout-bug/repo",        # relative to the cases file
    "question": "Why are users logged out after ~10 minutes?",
    "scope": "optional",
    "ground_truth": [
      {"id": "redis-ttl", "summary": "Redis store TTL is 600s",
       "files": ["src/config/session.ts"], "keywords": ["ttl", "600"]}
    ],
    "agents": ["mock:...", "mock:..."],         # optional per-case override
    "baseline_agent": "mock:..."                # optional per-case override
  }
]

A finding matches a ground-truth cause when it cites one of the cause's files
AND its claim or quotes mention at least one keyword (case-insensitive).
Keep keywords specific; this matcher is deliberately simple and transparent.
"""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

from . import ledger, orchestrator
from .agents import AgentSpec
from .evidence import cited_files


def load_cases(path: Path) -> list[dict]:
    text = path.read_text()
    if path.suffix == ".jsonl":
        return [json.loads(line) for line in text.splitlines() if line.strip()]
    return json.loads(text)


def matches(finding: dict, gt: dict, alt_roots=()) -> bool:
    files = cited_files(finding.get("evidence", []), alt_roots)
    want_files = gt.get("files") or []
    if want_files and not any(f.endswith(w) or w.endswith(f) for f in files for w in want_files if f):
        return False
    text = (finding.get("claim", "") + " " + " ".join(ev.get("quote", "") for ev in finding.get("evidence", []))).lower()
    kws = gt.get("keywords") or []
    return not kws or any(k.lower() in text for k in kws)


def _found(findings: list[dict], gts: list[dict]) -> set[str]:
    return {g["id"] for g in gts if any(matches(f, g) for f in findings)}


def score_case(case, run_dir: Path, baseline_findings: list[dict] | None) -> dict:
    gts = case["ground_truth"]
    fa = json.loads((run_dir / "findings_A.json").read_text())["findings"]
    fb = json.loads((run_dir / "findings_B.json").read_text())["findings"]
    entries = json.loads((run_dir / "ledger.json").read_text())
    by_id = {f["id"]: f for f in fa + fb}

    raw_a, raw_b = _found(fa, gts), _found(fb, gts)
    accepted_findings, strong_findings, extras = [], [], 0
    for e in entries:
        if e["status"] not in ledger.ACCEPTED:
            continue
        members = [by_id[m] for m in e["members"] if m not in e.get("refuted_members", [])]
        accepted_findings += members
        if e["status"] in ledger.STRONG:
            strong_findings += members
        if not any(matches(f, g) for f in members for g in gts):
            extras += 1
    final = _found(accepted_findings, gts)
    strong = _found(strong_findings, gts)
    wrongly_rejected = sorted((raw_a | raw_b) - final)
    base = _found(baseline_findings, gts) if baseline_findings is not None else None

    n = len(gts)
    return {
        "case": case["id"],
        "n_causes": n,
        "found_A": sorted(raw_a),
        "found_B": sorted(raw_b),
        "only_A": sorted(raw_a - raw_b),
        "only_B": sorted(raw_b - raw_a),
        "final": sorted(final),
        "final_verified": sorted(strong),
        "wrongly_rejected": wrongly_rejected,
        "extras_accepted": extras,
        "baseline": sorted(base) if base is not None else None,
        "recall_A": len(raw_a) / n if n else 0,
        "recall_B": len(raw_b) / n if n else 0,
        "recall_final": len(final) / n if n else 0,
        "recall_baseline": (len(base) / n if n else 0) if base is not None else None,
        "second_agent_added": sorted(final - raw_a),
    }


def run_eval(cases_path: str | Path, *, agents=("claude", "codex"), baseline: str | None = "claude",
             out_dir: str | Path = ".crosscheck/eval", budget_usd: float = 3.0, rounds: int = 1,
             cluster: str = "auto", timeout_s: int = 1200, log=print) -> dict:
    cases_path = Path(cases_path).resolve()
    cases = load_cases(cases_path)
    eval_dir = Path(out_dir).resolve() / time.strftime("%Y%m%d-%H%M%S")
    eval_dir.mkdir(parents=True)
    rows = []
    for case in cases:
        repo = (cases_path.parent / case["repo"]).resolve()
        case_agents = tuple(_resolve_spec(a, cases_path) for a in case.get("agents", agents))
        log(f"\n=== case {case['id']} ===")
        summary = orchestrator.run(case["question"], repo, agents=case_agents, scope=case.get("scope"),
                                   out_dir=eval_dir / "runs", rounds=rounds, cluster=cluster,
                                   budget_usd=budget_usd, timeout_s=timeout_s, log=log)
        base_findings, base_cost = None, None
        base_spec = case.get("baseline_agent", baseline)
        if base_spec:
            base_spec = _resolve_spec(base_spec, cases_path)
            log(f"  baseline: {base_spec} alone with 2x budget...")
            brief, _ = orchestrator.make_brief(case["question"], case.get("scope"))
            bdir = Path(summary["run_dir"]) / "baseline"
            (bdir / "raw").mkdir(parents=True)
            base_findings, _, res = orchestrator.research_only(
                AgentSpec.parse(base_spec), brief, repo, bdir, budget_usd=2 * budget_usd,
                timeout_s=2 * timeout_s, allow_web=False)
            base_cost = res.cost_usd
            if not res.ok:
                log(f"  ! baseline failed: {res.error}")
                base_findings = None
            shutil.rmtree(bdir / "work", ignore_errors=True)
        row = score_case(case, Path(summary["run_dir"]), base_findings)
        row.update(outcome=summary["outcome"], cost_crosscheck=summary["total_cost_usd"],
                   cost_complete=summary["cost_complete"], cost_baseline=base_cost,
                   duration_s=summary["duration_s"], report=summary["report_path"],
                   agents=summary["agents"])
        rows.append(row)

    result = {"cases": rows, "aggregate": _aggregate(rows), "eval_dir": str(eval_dir)}
    (eval_dir / "eval_results.json").write_text(json.dumps(result, indent=2))
    (eval_dir / "eval_report.md").write_text(render(result))
    result["report_path"] = str(eval_dir / "eval_report.md")
    log(f"\neval report: {result['report_path']}")
    return result


def _resolve_spec(spec: str, cases_path: Path) -> str:
    if spec.startswith("mock:"):
        p = Path(spec[5:])
        return "mock:" + str(p if p.is_absolute() else (cases_path.parent / p).resolve())
    return spec


def _aggregate(rows):
    n = len(rows)
    total = sum(r["n_causes"] for r in rows)

    def pooled(key):
        return sum(len(r[key]) for r in rows) / total if total else 0

    has_base = all(r["baseline"] is not None for r in rows) and n
    return {
        "cases": n,
        "causes": total,
        "recall_A": pooled("found_A"),
        "recall_B": pooled("found_B"),
        "recall_final": pooled("final"),
        "recall_baseline": pooled("baseline") if has_base else None,
        "cases_where_second_agent_added_a_cause": sum(bool(r["second_agent_added"]) for r in rows),
        "true_causes_wrongly_rejected": sum(len(r["wrongly_rejected"]) for r in rows),
        "extras_accepted": sum(r["extras_accepted"] for r in rows),
        "cost_crosscheck": _sum([r["cost_crosscheck"] for r in rows]),
        "cost_baseline": _sum([r["cost_baseline"] for r in rows]) if has_base else None,
    }


def _sum(xs):
    xs = [x for x in xs if x is not None]
    return round(sum(xs), 4) if xs else None


def _pct(x):
    return "n/a" if x is None else f"{x:.0%}"


def render(result) -> str:
    a = result["aggregate"]
    rows = result["cases"]
    agents = rows[0]["agents"] if rows else {"A": "A", "B": "B"}
    L = ["# Crosscheck evaluation", ""]
    L.append(f"{a['cases']} cases, {a['causes']} known causes. A = {agents['A']}, B = {agents['B']}.")
    L.append("")
    L.append("## Headline")
    L.append("")
    L.append(f"- **The second agent added a verified-or-accepted true cause that A missed in "
             f"{a['cases_where_second_agent_added_a_cause']} of {a['cases']} cases.**")
    L.append(f"- Recall (share of known causes found): A alone {_pct(a['recall_A'])} · B alone {_pct(a['recall_B'])} · "
             f"**crosscheck {_pct(a['recall_final'])}** · single agent at 2x budget {_pct(a['recall_baseline'])}")
    L.append(f"- True causes wrongly rejected by reconciliation: {a['true_causes_wrongly_rejected']} (should be 0)")
    L.append(f"- Accepted findings that match no known cause: {a['extras_accepted']} (read these: false positives, or real issues missing from your ground truth)")
    cc = "n/a" if a["cost_crosscheck"] is None else f"${a['cost_crosscheck']:.2f}"
    cb = "n/a" if a["cost_baseline"] is None else f"${a['cost_baseline']:.2f}"
    L.append(f"- Cost: crosscheck {cc} vs baseline {cb} (Codex cost is only counted if CROSSCHECK_CODEX_PRICE is set)")
    L.append("")
    L.append("## Per case")
    L.append("")
    L.append("| Case | Causes | A found | B found | Crosscheck kept | Baseline | 2nd agent added | Wrongly rejected | Outcome |")
    L.append("|---|---|---|---|---|---|---|---|---|")
    for r in rows:
        base = "n/a" if r["baseline"] is None else ", ".join(r["baseline"]) or "none"
        L.append(f"| {r['case']} | {r['n_causes']} | {', '.join(r['found_A']) or 'none'} | {', '.join(r['found_B']) or 'none'} | "
                 f"{', '.join(r['final']) or 'none'} | {base} | {', '.join(r['second_agent_added']) or 'none'} | "
                 f"{', '.join(r['wrongly_rejected']) or 'none'} | {r['outcome']} |")
    L.append("")
    L.append("## How to read this")
    L.append("")
    L.append("- If crosscheck recall is not clearly above the 2x-budget baseline across many cases, the second agent "
             "is mostly buying extra spend, not extra coverage.")
    L.append("- Any wrongly rejected cause means the reconciliation rules are too aggressive; inspect that run's ledger.")
    L.append("- Ten to twenty real past bugs is the minimum before drawing conclusions.")
    L.append("")
    for r in rows:
        L.append(f"- `{r['case']}` report: `{r['report']}`")
    L.append("")
    return "\n".join(L)
