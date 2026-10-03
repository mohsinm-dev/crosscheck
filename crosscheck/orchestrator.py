"""Runs one crosscheck: brief -> blind research -> diff -> cross-exam -> report.

Plain code owns every decision. The models only fill in schemas.
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import shutil
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import cluster as clustering
from . import ledger, prompts, report, schemas
from .agents import AgentResult, AgentSpec, check_available, run_agent
from .evidence import FAIL, check_evidence, cited_files

BRIEF_KEYS = ("question", "scope", "deliverable", "constraints", "out_of_scope")
HINT_WORDS = ("i suspect", "i think", "probably", "hypothesis", "we know", "my guess", "likely cause")

# Never copied into the agents' working copies: VCS data, dependencies, build
# output, and repo-controlled agent config (hooks / MCP servers can run code).
COPY_IGNORE = shutil.ignore_patterns(
    ".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build", "target",
    ".next", ".cache", ".claude", ".codex", ".mcp.json", ".crosscheck",
)


class CrosscheckError(Exception):
    pass


def make_brief(question, scope=None, deliverable=None, constraints=None, out_of_scope=None) -> tuple[dict, list[str]]:
    brief = {
        "question": question.strip(),
        "scope": scope or "This repository",
        "deliverable": deliverable or "Root causes / answers, each with file-and-line evidence",
        "constraints": constraints or "Read-only. Do not modify any file.",
        "out_of_scope": out_of_scope or "",
    }
    brief = {k: v for k, v in brief.items() if k in BRIEF_KEYS}
    warnings = [
        f"The brief contains '{w}'. Hints in the brief anchor both analysts; consider removing them."
        for w in HINT_WORDS if w in json.dumps(brief).lower()
    ]
    return brief, warnings


def _snapshot(repo: Path, dest: Path) -> Path:
    shutil.copytree(repo, dest, ignore=COPY_IGNORE, symlinks=True)
    return dest


def _normalize(side: str, res: AgentResult, snapshot: Path, alt_roots) -> tuple[list[dict], dict, str | None]:
    if not res.ok:
        return [], {}, res.error
    raw = res.data or {}
    items = raw.get("findings")
    if not isinstance(items, list):
        return [], {}, "output had no 'findings' list"
    out = []
    for i, f in enumerate(items, 1):
        if not isinstance(f, dict) or not f.get("claim"):
            continue
        evidence = [ev for ev in f.get("evidence") or [] if isinstance(ev, dict)]
        mech, per_item = check_evidence(snapshot, evidence, alt_roots)
        out.append({
            "id": f"{side}-{i}",
            "orig_id": f.get("id"),
            "analyst": side,
            "claim": str(f["claim"]).strip(),
            "kind": f.get("kind", "fact"),
            "confidence": f.get("confidence", "medium"),
            "evidence": evidence,
            "would_refute": f.get("would_refute", ""),
            "mechanical": mech,
            "mechanical_items": per_item,
        })
    coverage = raw.get("coverage") or {}
    return out, {"examined": coverage.get("examined", []), "not_examined": coverage.get("not_examined", [])}, None


def _covers(entry: str, path: str) -> bool:
    """Does a free-text coverage entry ("k8s/ manifests", "src/") cover this repo path?"""
    for tok in re.findall(r"[\w.\-/]+", entry):
        tok = tok.strip("/")
        if tok.startswith("./"):
            tok = tok[2:]
        if tok and tok not in (".", "..") and (path == tok or path.startswith(tok + "/")):
            return True
    return False


def _coverage_hint(cited: set[str], coverage: dict) -> str:
    for entry in coverage.get("not_examined", []):
        if any(_covers(entry, c) for c in cited):
            return f"it listed \u201c{entry}\u201d as not examined"
    examined = coverage.get("examined") or []
    # Only judge path coverage when the agent listed paths; prose like "all files" can't be matched.
    listed_paths = any("/" in e or "." in e for e in examined)
    if listed_paths and cited and not any(_covers(e, c) for e in examined for c in cited):
        tops = sorted({Path(c).parts[0] + ("/" if len(Path(c).parts) > 1 else "") for c in cited if Path(c).parts})
        return f"its coverage never mentions {', '.join(tops)}"
    return ""


def missed(entries, findings_by_id, coverage, alt_roots) -> dict[str, list[dict]]:
    out = {"A": [], "B": []}
    for e in entries:
        if e["status"] not in ledger.STRONG:
            continue
        if e["origin"].startswith("unique_"):
            finder = e["origin"][-1]
        elif e["origin"] == "conflict" and e.get("winner"):
            finder = e["winner"]
        else:
            continue
        miss = ledger.other(finder)
        cited = set()
        for m in e["members"]:
            if m.startswith(finder):
                cited |= cited_files(findings_by_id[m]["evidence"], alt_roots)
        hint = ("it claimed the opposite, which was refuted" if e["origin"] == "conflict"
                else _coverage_hint(cited, coverage.get(miss, {})))
        out[miss].append({"cluster_id": e["cluster_id"], "claim": e["claim"], "hint": hint})
    return out


def research_only(spec: AgentSpec, brief: dict, repo: Path, run_dir: Path, *, budget_usd: float,
                  timeout_s: int, allow_web: bool, label: str = "A") -> tuple[list[dict], dict, AgentResult]:
    """One blind research pass (also used by the eval harness as the single-agent baseline)."""
    work = _snapshot(repo, run_dir / "work" / label)
    res = run_agent(spec, prompt=prompts.RESEARCH.format(brief=prompts.render_brief(brief)),
                    schema=schemas.FINDINGS, cwd=work, phase="research", raw_dir=run_dir / "raw",
                    label=label, budget_usd=budget_usd, timeout_s=timeout_s, allow_web=allow_web)
    alt = (str(work.resolve()), str(repo.resolve()))
    findings, cov, err = _normalize(label, res, work, alt)
    if err and res.ok:
        res.ok, res.error = False, err
    return findings, cov, res


def run(
    question: str,
    repo: str | Path,
    *,
    agents: tuple[str, str] = ("claude", "codex"),
    scope: str | None = None,
    out_dir: str | Path = ".crosscheck/runs",
    rounds: int = 1,
    cluster: str = "auto",
    clusterer: str = "claude:haiku",
    budget_usd: float = 3.0,
    verify_budget_usd: float = 1.5,
    timeout_s: int = 1200,
    allow_web: bool = False,
    keep_work: bool = False,
    log=print,
) -> dict:
    t0 = time.monotonic()
    repo = Path(repo).resolve()
    if not repo.is_dir():
        raise CrosscheckError(f"repo not found: {repo}")
    specs = {"A": AgentSpec.parse(agents[0]), "B": AgentSpec.parse(agents[1])}
    for side, spec in specs.items():
        err = check_available(spec)
        if err:
            raise CrosscheckError(f"agent {side} ({spec.display}): {err}")

    run_id = time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(2)
    run_dir = Path(out_dir).resolve() / run_id
    (run_dir / "raw").mkdir(parents=True)
    _ignore_in_git(Path(out_dir).resolve())
    log(f"crosscheck {run_id}: A={specs['A'].display}  B={specs['B'].display}")

    # Phase 1: frozen brief
    brief, warnings = make_brief(question, scope)
    brief_text = prompts.render_brief(brief)
    brief_sha = hashlib.sha256(brief_text.encode()).hexdigest()
    (run_dir / "brief.json").write_text(brief_text)
    for w in warnings:
        log(f"  warning: {w}")

    # Phase 2: blind research, in parallel, on separate copies
    log("  [1/4] both analysts researching independently...")
    work = {s: _snapshot(repo, run_dir / "work" / s) for s in ("A", "B")}
    alt_roots = tuple(str(p.resolve()) for p in work.values()) + (str(repo),)
    research_prompt = prompts.RESEARCH.format(brief=brief_text)
    with ThreadPoolExecutor(2) as pool:
        futs = {s: pool.submit(run_agent, specs[s], prompt=research_prompt, schema=schemas.FINDINGS,
                               cwd=work[s], phase="research", raw_dir=run_dir / "raw", label=s,
                               budget_usd=budget_usd, timeout_s=timeout_s, allow_web=allow_web)
                for s in ("A", "B")}
        results = {s: f.result() for s, f in futs.items()}

    # Phase 3: structured findings + mechanical checks
    findings, coverage, errors = {}, {}, []
    costs = {s: {"research": results[s].cost_usd} for s in ("A", "B")}
    timings = {s: {"research": results[s].duration_s} for s in ("A", "B")}
    for s in ("A", "B"):
        findings[s], coverage[s], err = _normalize(s, results[s], work["A"], alt_roots)
        if err:
            errors.append(f"{s} ({specs[s].display}) research failed: {err}")
            log(f"  ! {errors[-1]}")
        else:
            bad = sum(f["mechanical"] == FAIL for f in findings[s])
            log(f"    {s}: {len(findings[s])} findings ({bad} with evidence that doesn't check out)")
        (run_dir / f"findings_{s}.json").write_text(json.dumps({"findings": findings[s], "coverage": coverage[s]}, indent=2))
    findings_by_id = {f["id"]: f for side in findings.values() for f in side}

    # Phase 4: diff
    log("  [2/4] comparing findings...")
    cluster_note, cluster_cost = None, None
    mode = cluster
    if mode == "auto":
        # Mock runs are meant to be offline and free, so they never call a real clustering model.
        offline = any(spec.kind == "mock" for spec in specs.values())
        mode = "llm" if not offline and check_available(AgentSpec.parse(clusterer)) is None else "heuristic"
    if mode == "llm" and findings["A"] and findings["B"]:
        clusters, cluster_cost, err = clustering.cluster_llm(findings["A"], findings["B"], AgentSpec.parse(clusterer), run_dir)
        if clusters is None:
            cluster_note = f"model clustering failed ({err}); fell back to heuristic"
            log(f"    {cluster_note}")
            clusters, mode = clustering.cluster_heuristic(findings["A"], findings["B"], alt_roots), "heuristic"
    else:
        clusters = clustering.cluster_heuristic(findings["A"], findings["B"], alt_roots)
        mode = "heuristic" if mode == "llm" else mode
    entries = ledger.build(findings, clusters)

    # Phase 5: cross-examination (fresh sessions, anonymized claims)
    verify_costs = {"A": 0.0, "B": 0.0}  # stays 0 when a side has nothing to verify
    if rounds >= 1 and not errors:
        requests = ledger.verification_requests(entries, findings_by_id, seed=run_id)
        n = {s: len(r) for s, r in requests.items()}
        log(f"  [3/4] cross-examination: A checks {n['A']} of B's claims, B checks {n['B']} of A's...")
        verdicts = {"A": None, "B": None}
        with ThreadPoolExecutor(2) as pool:
            futs = {}
            for s in ("A", "B"):
                if not requests[s]:
                    verdicts[s] = {"verdicts": []}
                    continue
                payload = [{k: v for k, v in r.items() if not k.startswith("_")} for r in requests[s]]
                prompt = prompts.VERIFY.format(brief=brief_text, claims=prompts.render_claims(payload))
                futs[s] = pool.submit(run_agent, specs[s], prompt=prompt, schema=schemas.VERDICTS, cwd=work[s],
                                      phase="verify", raw_dir=run_dir / "raw", label=s,
                                      budget_usd=verify_budget_usd, timeout_s=timeout_s, claims=payload)
            for s, f in futs.items():
                res = f.result()
                verify_costs[s], timings[s]["verify"] = res.cost_usd, res.duration_s
                if res.ok:
                    verdicts[s] = res.data
                else:
                    log(f"  ! {s} cross-examination failed: {res.error} (its targets stay unconfirmed)")
        ledger.apply_verdicts(entries, requests, verdicts, work["A"], alt_roots, findings_by_id)
    else:
        ledger.finalize_without_exam(entries, "cross-examination skipped" if rounds < 1 else "the other analyst's run failed")
    for s in ("A", "B"):
        costs[s]["verify"] = verify_costs[s]

    # Phase 6: resolution + report
    log("  [4/4] writing report...")
    status = ledger.outcome(entries, errors)
    summary = {
        "run_id": run_id,
        "outcome": status,
        "brief": brief,
        "brief_sha256": brief_sha,
        "agents": {s: specs[s].display for s in ("A", "B")},
        "rounds": rounds if not errors else 0,
        "cluster_mode": mode,
        "cluster_note": cluster_note,
        "costs": costs,
        "cluster_cost": cluster_cost,
        "total_cost_usd": _sum_costs(costs, cluster_cost),
        "cost_complete": all(v is not None for c in costs.values() for v in c.values()),
        "timings_s": timings,
        "duration_s": round(time.monotonic() - t0, 1),
        "errors": errors,
        "warnings": warnings,
        "coverage": coverage,
        "missed": missed(entries, findings_by_id, coverage, alt_roots),
        "counts": {st: sum(e["status"] == st for e in entries) for st in sorted({e["status"] for e in entries})},
        "run_dir": str(run_dir),
    }
    (run_dir / "ledger.json").write_text(json.dumps(entries, indent=2))
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    md = report.render(summary, entries, findings_by_id)
    (run_dir / "report.md").write_text(md)
    summary["report_path"] = str(run_dir / "report.md")
    if not keep_work:
        shutil.rmtree(run_dir / "work", ignore_errors=True)
    log(f"  {status}: report at {summary['report_path']}")
    return summary


def _ignore_in_git(out_dir: Path) -> None:
    """Keep run folders out of the user's commits: drop a '*' .gitignore at the .crosscheck root."""
    for p in [out_dir, *out_dir.parents]:
        if p.name == ".crosscheck":
            gi = p / ".gitignore"
            if not gi.exists():
                gi.write_text("*\n")
            return


def _sum_costs(costs, extra):
    vals = [v for c in costs.values() for v in c.values() if v is not None]
    if extra is not None:
        vals.append(extra)
    return round(sum(vals), 4) if vals else None
