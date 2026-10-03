# crosscheck

**Claude Code and Codex investigate the same question blind, then verify each other's claims with evidence.** You get one report: what's verified, what was refuted, what's disputed, and what each agent missed.

```
/crosscheck:run Why are users logged out after about 10 minutes when sessions should last 24 hours?
```

Example report (abridged; this is the bundled demo in `examples/logout-bug`):

```
# Crosscheck: CONVERGED                                   1 round · 6m 40s · $2.31

| # | Claim                                                        | Found by        | Status   |
|---|--------------------------------------------------------------|-----------------|----------|
| 1 | Redis session store ttl is 600s, overriding the 24h cookie   | both            | verified |
| 2 | rolling is false, so activity never extends the session      | codex (won)     | verified |
| 3 | Canary pods read SESSION_SECRET from a different k8s secret  | codex only      | verified |

## What each analyst missed
- claude missed:
  - rolling is false ...            (it claimed the opposite, which was refuted)
  - Canary pods read ... secret     (it listed "k8s/ deployment manifests" as not examined)

## Refuted
- ~~rolling: true~~ (claude): its quoted evidence does not match the files
- ~~trust proxy is never set~~ (claude): it is set in src/bootstrap/proxy.ts (`src/bootstrap/proxy.ts:5`)
```

One agent alone would have fixed the TTL and shipped. Users on the canary pod would still be getting logged out.

## Why not just ask Codex for a second opinion?

Because the second opinion is only worth something if it's independent. When one agent asks the other, it writes the other's prompt, and its own hypotheses leak in. And when two models argue freely, the research shows they tend to drift toward agreement, not truth.

crosscheck takes the conversation away from the models:

| Phase | What happens |
|---|---|
| 1. Brief | Your question is frozen into a neutral brief. Both agents get byte-identical text. |
| 2. Blind research | Fresh Claude Code and Codex sessions investigate **in parallel**, read-only, on separate copies of your repo. Neither knows the other exists. |
| 3. Structured findings | Each returns atomic claims with file-and-line evidence and verbatim quotes, plus what it did and didn't examine. |
| 4. Diff | Claims are grouped into agreed / unique / conflicting. Every quote is **checked against the actual files**; fabricated evidence kills a claim before any model sees it. |
| 5. Cross-examination | Each agent gets the *other's* unique and conflicting claims, anonymized and shuffled, and must confirm or refute each with **new** evidence. Verdicts whose evidence doesn't check out are downgraded. |
| 6. Resolution | Fixed rules, not a model, decide each claim's status. Anything still contested goes to you as **disputed**, with both sides' evidence. |

## Install

You need [Claude Code](https://code.claude.com) and the [Codex CLI](https://github.com/openai/codex), both installed and logged in, and Python 3.10+. No Python packages required.

```bash
npm install -g @openai/codex
codex login
```

Then, inside Claude Code:

```
/plugin marketplace add YOUR_GITHUB_USER/crosscheck
/plugin install crosscheck@crosscheck
/crosscheck:setup
```

`/crosscheck:setup` checks everything without spending anything. (To install from a local folder instead of GitHub: `/plugin marketplace add /path/to/crosscheck`.)

## Use

| Command | What it does |
|---|---|
| `/crosscheck:run <question>` | Run both agents and show the report |
| `/crosscheck:setup` | Check prerequisites (free) |
| `/crosscheck:eval <cases.json>` | Measure whether it's worth it on your own past bugs |

Options go after the question:

```
/crosscheck:run Why does the nightly export drop rows? --rounds 0
/crosscheck:run What breaks if we upgrade to Postgres 17? --agents claude:sonnet,codex --budget 5
/crosscheck:run Is our JWT validation safe? --web
```

| Flag | Default | Meaning |
|---|---|---|
| `--agents A,B` | `claude,codex` | Each is `claude[:model]` or `codex[:model]`. `claude:opus,claude:sonnet` also works. |
| `--rounds 0\|1` | `1` | `0` compares findings only (cheaper); `1` adds cross-examination |
| `--budget USD` | `3` | Max spend per Claude research run (`--verify-budget` for cross-exam, default 1.5) |
| `--timeout S` | `1200` | Seconds per agent run |
| `--web` | off | Let Claude use web search during research |
| `--scope "..."` | whole repo | Narrow the investigation |
| `--cluster auto\|llm\|heuristic` | `auto` | How claims are matched: a cheap model (Haiku) if available, else word/location overlap |

Runs are saved in `.crosscheck/runs/<run-id>/` in your project (git-ignored automatically): the frozen brief, every prompt, raw transcripts, both findings files, `ledger.json` and `report.md`.

### Without Claude Code

The engine is a plain CLI, so you can run it from a terminal, CI, or from inside Codex:

```bash
pipx install git+https://github.com/YOUR_GITHUB_USER/crosscheck
crosscheck doctor
crosscheck run "Why are users logged out after ~10 minutes?" --repo . --print-report
```

## Is it worth it on your codebase?

Honestly: maybe. Frontier models share a lot of blind spots, and some of any multi-agent gain is just spending more tokens. So crosscheck ships with an eval harness that compares it against **one agent given twice the budget**:

```
/crosscheck:eval path/to/cases.json
```

A case is a past bug with a known root cause (see [`examples/cases.json`](examples/cases.json) and `/crosscheck:eval` for the format). Ten to twenty real cases is the minimum. The report tells you in how many cases the second agent added a true cause the first missed, recall for each setup, whether reconciliation wrongly rejected any true cause, and the cost of each.

Good fits: production incidents, hard-to-reproduce bugs, security reviews, migration and upgrade risk, auditing unfamiliar code. Poor fits: routine edits and small questions; it costs roughly 2 to 4 times a single agent run.

## Safety defaults

Each agent's output becomes the other's input, so crosscheck treats everything that crosses between them as untrusted:

- **Read-only.** Codex runs with `-s read-only`; Claude gets only `Read, Grep, Glob` (plus web tools with `--web`) under `--permission-mode dontAsk`.
- **No repo-controlled agent config.** Working copies exclude `.claude/`, `.codex/`, `.mcp.json` and `.git`, and Claude runs with `--setting-sources user --strict-mcp-config`, so a repo's hooks or MCP servers can't execute.
- **Claims are data, never commands.** They're passed as schema-validated JSON inside "untrusted data" delimiters, anonymized. Nothing an agent outputs can set a flag, path or command for the other.
- **Fresh sessions every phase**, no session persistence, so no agent defends its earlier verdict or reads the other's transcript.
- **Caps everywhere.** Per-run dollar caps on Claude, timeouts on both, one cross-examination round, and an explicit NOT CONVERGED instead of looping.
- **Your credentials are never touched.** The tool just calls your installed, logged-in CLIs. Note: if `ANTHROPIC_API_KEY` is set, Claude runs bill your API account instead of your subscription (`/crosscheck:setup` warns you).

## Costs

Claude runs report their exact cost. The Codex CLI doesn't report dollars, so set list prices to include an estimate:

```bash
export CROSSCHECK_CODEX_PRICE="1.75,0.175,14"   # $ per 1M tokens: input, cached input, output
```

## Limitations (v0.1)

- **Quote checks prove a quote exists, not that the conclusion is right.** A claim can cite real lines and still misread them. Cross-examination is what catches those.
- URL and command evidence isn't re-checked yet; only file evidence is.
- The Claude side is tested against Claude Code 2.1.288. The Codex side is built on the documented `codex exec` flags (`--json`, `--output-schema`, `-o`, `-s read-only`) and tested against a simulated Codex; Codex releases often, so if a flag changes, override the binary with `CROSSCHECK_CODEX_BIN` or open an issue.
- One cross-examination round; disputed claims go to a human rather than another round.
- The eval matcher is keyword-and-file based: transparent, but you need specific keywords.

## Development

```bash
python3 -m unittest discover -s tests -v     # 29 tests, no network, no model calls
python3 bin/crosscheck run "Why are users logged out?" --repo examples/logout-bug/repo \
  --agents mock:examples/logout-bug/mock/claude,mock:examples/logout-bug/mock/codex --print-report
python3 bin/crosscheck eval examples/cases.json        # uses the simulated agents in the case file
```

Layout: `crosscheck/` is the engine (`orchestrator.py` runs the phases, `ledger.py` holds every resolution rule, `evidence.py` the quote checks, `agents.py` the Claude/Codex/mock adapters), `skills/` are the slash commands, `.claude-plugin/` makes the repo installable, `examples/` has a demo repo with a planted three-cause bug.

## License

MIT
