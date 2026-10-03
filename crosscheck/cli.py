"""Command line: `crosscheck run ...` and `crosscheck eval ...`."""

from __future__ import annotations

import argparse
import sys

from . import evaluate, orchestrator


def _agents(s: str) -> tuple[str, str]:
    parts = [p.strip() for p in s.split(",") if p.strip()]
    if len(parts) != 2:
        raise argparse.ArgumentTypeError("give exactly two agents, e.g. claude,codex")
    return parts[0], parts[1]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="crosscheck", description="Two agents research blind, then verify each other.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="crosscheck one question against a repo")
    r.add_argument("question")
    r.add_argument("--repo", default=".", help="repository to investigate (default: current dir)")
    r.add_argument("--scope", help="optional scope line for the brief")
    r.add_argument("--agents", type=_agents, default=("claude", "codex"),
                   help="two agents: claude[:model], codex[:model] or mock:<dir> (default: claude,codex)")
    r.add_argument("--rounds", type=int, choices=(0, 1), default=1, help="cross-examination rounds (0 = diff only)")
    r.add_argument("--cluster", choices=("auto", "llm", "heuristic"), default="auto")
    r.add_argument("--clusterer", default="claude:haiku", help="cheap model used for --cluster llm")
    r.add_argument("--budget", type=float, default=3.0, help="max USD per Claude research run (default 3)")
    r.add_argument("--verify-budget", type=float, default=1.5, help="max USD per Claude verification run")
    r.add_argument("--timeout", type=int, default=1200, help="seconds per agent run (default 1200)")
    r.add_argument("--web", action="store_true", help="allow Claude web search/fetch during research")
    r.add_argument("--out", default=".crosscheck/runs", help="where run folders go")
    r.add_argument("--keep-work", action="store_true", help="keep the agents' working copies")
    r.add_argument("--print-report", action="store_true", help="print the markdown report when done")

    sub.add_parser("doctor", help="check that both agent CLIs are installed and logged in (no model calls)")

    e = sub.add_parser("eval", help="score crosscheck on cases with known answers")
    e.add_argument("cases", help="cases .json or .jsonl")
    e.add_argument("--agents", type=_agents, default=("claude", "codex"))
    e.add_argument("--baseline", default="claude", help="single agent run at 2x budget for comparison ('none' to skip)")
    e.add_argument("--rounds", type=int, choices=(0, 1), default=1)
    e.add_argument("--cluster", choices=("auto", "llm", "heuristic"), default="auto")
    e.add_argument("--budget", type=float, default=3.0)
    e.add_argument("--timeout", type=int, default=1200)
    e.add_argument("--out", default=".crosscheck/eval")

    args = ap.parse_args(argv)
    log = lambda m: print(m, file=sys.stderr, flush=True)  # noqa: E731
    if args.cmd == "doctor":
        from .doctor import doctor
        return doctor()
    try:
        if args.cmd == "run":
            s = orchestrator.run(args.question, args.repo, agents=args.agents, scope=args.scope,
                                 out_dir=args.out, rounds=args.rounds, cluster=args.cluster,
                                 clusterer=args.clusterer, budget_usd=args.budget,
                                 verify_budget_usd=args.verify_budget, timeout_s=args.timeout,
                                 allow_web=args.web, keep_work=args.keep_work, log=log)
            if args.print_report:
                print(open(s["report_path"]).read())
            else:
                print(s["report_path"])
            return 0 if s["outcome"] != "ERROR" else 2
        res = evaluate.run_eval(args.cases, agents=args.agents,
                                baseline=None if args.baseline == "none" else args.baseline,
                                out_dir=args.out, budget_usd=args.budget, rounds=args.rounds,
                                cluster=args.cluster, timeout_s=args.timeout, log=log)
        print(res["report_path"])
        return 0
    except orchestrator.CrosscheckError as err:
        log(f"crosscheck: {err}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
