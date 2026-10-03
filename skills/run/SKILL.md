---
name: run
description: Cross-check a question about this codebase with two independent agents (Claude Code and Codex). Both investigate blind, then verify each other's claims with file-and-line evidence, and the report shows what each one missed. Use only when the user asks for a crosscheck.
argument-hint: <question about this codebase> [--agents claude,codex] [--rounds 0|1] [--budget USD] [--web]
disable-model-invocation: true
---

# Crosscheck a question

The user wants two independent agents to investigate this question and reconcile their findings:

<question>
$ARGUMENTS
</question>

The crosscheck engine is bundled with this plugin at `${CLAUDE_PLUGIN_ROOT}/bin/crosscheck`. It starts its own **fresh, separate** Claude Code and Codex sessions; nothing from this conversation is shared with them. That separation is the point: do not try to help the second agent.

## Steps

1. **Get the question.** If the question above is empty, ask the user what to investigate and stop.

2. **Keep the brief neutral.** Pass the user's question as written. Do NOT add your own hypotheses, findings from this conversation, or "we already know X" — that would anchor both agents and defeat the blind first pass. If the user's question itself contains a guess (for example "I think it's the cache"), run it anyway but tell the user in one line that hints reduce independence.

3. **Separate options from the question.** If the text above contains flags (anything starting with `--`), pass them through as flags, not as part of the question. Supported flags:
   - `--agents A,B` — default `claude,codex`. Each is `claude[:model]` or `codex[:model]`, e.g. `claude:sonnet,codex:gpt-5.3-codex`.
   - `--rounds 0|1` — `0` = compare findings only (cheaper), `1` = also cross-examine (default).
   - `--budget USD` — max spend per Claude research run (default 3).
   - `--web` — let Claude use web search during research.
   - `--scope "..."` — narrow what to look at.

4. **Run it in the background.** It usually takes 3–10 minutes. Use the Bash tool with `run_in_background: true`, from the project root, quoting the question safely with single quotes (escape any single quote in it as `'\''`):

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/bin/crosscheck" run '<question>' --repo "$(git rev-parse --show-toplevel 2>/dev/null || pwd)" [flags]
   ```

   Tell the user in one sentence that both agents are working and roughly how long it takes. Then wait for the background command to finish (check its output; don't start other work on the same files meanwhile).

5. **Show the result.** The last line of stdout is the path to `report.md`. Read that file and present it to the user:
   - the outcome line (CONVERGED / NOT CONVERGED / ERROR), cost and time,
   - the findings table,
   - **what each analyst missed**,
   - anything refuted or disputed.

   Present the report faithfully. Do not re-judge claims yourself or overrule the ledger. If you have your own view, add it after the report, clearly labelled as your opinion. For disputed items, say a human needs to decide and show both sides' evidence.

6. **If it fails** (exit code 2, or outcome ERROR), show the error lines from stderr and the likely fix:
   - `'codex' is not installed` → `npm install -g @openai/codex`, then `codex login`
   - `'claude' is not installed` → make sure the `claude` CLI is on PATH
   - usage-limit or 429 messages → wait for limits to reset, or lower `--budget` / use `--rounds 0`
   - suggest `/crosscheck:setup` to check everything.

The full ledger, prompts and raw transcripts are in the run folder (`.crosscheck/runs/<run-id>/`), which is git-ignored automatically.
