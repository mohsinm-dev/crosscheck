---
name: setup
description: Check that everything crosscheck needs is installed and logged in (Python, Claude Code CLI, Codex CLI). Makes no model calls and costs nothing. Use when the user asks to set up or troubleshoot crosscheck.
disable-model-invocation: true
---

# Crosscheck setup check

Run the bundled doctor from the project root with the Bash tool:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/bin/crosscheck" doctor
```

Show the user the result. For each line marked `XX`, give the exact fix it prints and offer to run the install command for them (ask before installing anything globally). Logins (`codex login`) are interactive, so the user must run them in their own terminal.

When everything shows `ok`, tell the user they can now run:

```
/crosscheck:run <question about this codebase>
```

and mention that each run starts two fresh agent sessions, so it costs roughly two to four times a single agent run.
