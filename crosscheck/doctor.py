"""`crosscheck doctor`: check prerequisites without spending anything on models."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys


def _version(cmd: list[str]) -> str | None:
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return None
    text = (out.stdout or out.stderr).strip().splitlines()
    return text[0] if text else ""


def doctor() -> int:
    ok = True
    print("crosscheck doctor\n")

    py = sys.version_info
    good = py >= (3, 10)
    ok &= good
    print(f"[{'ok' if good else 'XX'}] Python {py.major}.{py.minor}" + ("" if good else "  -> needs 3.10+"))

    claude = os.environ.get("CROSSCHECK_CLAUDE_BIN", "claude")
    if shutil.which(claude):
        print(f"[ok] Claude Code: {_version([claude, '--version'])}")
    else:
        ok = False
        print("[XX] Claude Code not found  -> install: npm install -g @anthropic-ai/claude-code")

    codex = os.environ.get("CROSSCHECK_CODEX_BIN", "codex")
    if shutil.which(codex):
        print(f"[ok] Codex CLI: {_version([codex, '--version'])}")
        status = _version([codex, "login", "status"])
        if status is None:
            print("[??] Could not read Codex login status  -> run: codex login")
        elif "not" in status.lower() and "logged" in status.lower():
            ok = False
            print(f"[XX] Codex: {status}  -> run: codex login")
        else:
            print(f"[ok] Codex login: {status}")
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
