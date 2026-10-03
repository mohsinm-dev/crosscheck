"""`crosscheck doctor`: check prerequisites without spending anything on models."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys


def _version(cmd: list[str]) -> tuple[int, str] | None:
    """(exit code, most useful output line), or None if the command couldn't run."""
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return None
    lines = [l.strip() for l in (out.stdout or out.stderr).splitlines() if l.strip()]
    # A crashing Node CLI prints a stack trace; the "Error: ..." line is the one worth showing.
    err = next((l for l in lines if l.startswith("Error:")), None)
    return out.returncode, err or (lines[0] if lines else "")


def doctor() -> int:
    ok = True
    print("crosscheck doctor\n")

    py = sys.version_info
    good = py >= (3, 10)
    ok &= good
    print(f"[{'ok' if good else 'XX'}] Python {py.major}.{py.minor}" + ("" if good else "  -> needs 3.10+"))

    claude = os.environ.get("CROSSCHECK_CLAUDE_BIN", "claude")
    if shutil.which(claude):
        v = _version([claude, "--version"])
        if v and v[0] == 0:
            print(f"[ok] Claude Code: {v[1]}")
        else:
            ok = False
            print(f"[XX] Claude Code is installed but does not run: {v[1] if v else 'no response'}")
    else:
        ok = False
        print("[XX] Claude Code not found  -> install: npm install -g @anthropic-ai/claude-code")

    codex = os.environ.get("CROSSCHECK_CODEX_BIN", "codex")
    if shutil.which(codex):
        v = _version([codex, "--version"])
        if not v or v[0] != 0:
            ok = False
            msg = v[1] if v else "no response"
            hint = "" if "reinstall" in msg.lower() else "  -> reinstall: npm install -g @openai/codex@latest"
            print(f"[XX] Codex CLI is installed but does not run: {msg}{hint}")
        else:
            print(f"[ok] Codex CLI: {v[1]}")
            status = _version([codex, "login", "status"])
            if status is None:
                print("[??] Could not read Codex login status  -> run: codex login")
            elif status[0] != 0 or ("not" in status[1].lower() and "logged" in status[1].lower()):
                ok = False
                print(f"[XX] Codex: {status[1]}  -> run: codex login")
            else:
                print(f"[ok] Codex login: {status[1]}")
    else:
        ok = False
        print("[XX] Codex CLI not found  -> install: npm install -g @openai/codex   then: codex login")

    if os.environ.get("ANTHROPIC_API_KEY"):
        print("[!!] ANTHROPIC_API_KEY is set: Claude runs will bill your API account, not your subscription.")
    if os.environ.get("CROSSCHECK_CODEX_PRICE"):
        print(f"[ok] Codex cost estimates on (CROSSCHECK_CODEX_PRICE={os.environ['CROSSCHECK_CODEX_PRICE']})")
    else:
        print("[--] Codex cost is not reported by the CLI; set CROSSCHECK_CODEX_PRICE='in,cached_in,out' "
              "($ per 1M tokens) to include it in reports.")

    print("\nReady." if ok else "\nFix the items marked XX, then run this again.")
    return 0 if ok else 1
