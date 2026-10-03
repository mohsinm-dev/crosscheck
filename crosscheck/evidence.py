"""Mechanical evidence checks: no model involved.

A file citation passes if the file exists inside the repo and the quoted text
appears at (or near) the cited lines. This is what lets wrong claims die on
evidence instead of on which model sounds more confident.
"""

from __future__ import annotations

import re
from pathlib import Path

PASS, LOOSE, FAIL, NA = "pass", "loose", "fail", "n/a"
LINE_SLACK = 3  # models are often off by a line or two

_LOC = re.compile(r"^(?P<path>.+?)(?::(?P<start>\d+)(?:\s*[-–]\s*(?P<end>\d+))?)?(?::\d+)?$")


def _norm(s: str) -> str:
    s = s.replace("‘", "'").replace("’", "'").replace("“", '"').replace("”", '"')
    return re.sub(r"\s+", " ", s).strip()


def parse_location(loc: str) -> tuple[str, int | None, int | None]:
    loc = loc.strip().strip("`")
    m = _LOC.match(loc)
    if not m:
        return loc, None, None
    start = int(m.group("start")) if m.group("start") else None
    end = int(m.group("end")) if m.group("end") else start
    return m.group("path").strip(), start, end


def clean_rel(path: str, alt_roots: tuple[str, ...] = ()) -> str:
    """Normalise a cited path to repo-relative form.

    Agents sometimes cite absolute paths inside their own working copy; strip
    those roots so every agent's citations are checked against the same snapshot.
    """
    path = path.strip()
    for root in alt_roots:
        root = root.rstrip("/") + "/"
        if path.startswith(root):
            path = path[len(root):]
            break
    while path.startswith("./"):
        path = path[2:]
    return path


def resolve_in_repo(repo: Path, rel: str) -> Path | None:
    """Resolve a cited path, refusing anything that escapes the repo."""
    root = repo.resolve()
    p = (root / rel).resolve()
    try:
        p.relative_to(root)
    except ValueError:
        return None
    return p


def check_one(repo: Path, ev: dict, alt_roots: tuple[str, ...] = ()) -> str:
    if ev.get("type") != "file":
        return NA  # v0 does not fetch URLs or re-run commands
    path, start, end = parse_location(ev.get("location", ""))
    p = resolve_in_repo(repo, clean_rel(path, alt_roots))
    if p is None or not p.is_file():
        return FAIL
    quote = _norm(ev.get("quote", ""))
    if not quote:
        return NA  # absence claims can't be checked mechanically
    try:
        lines = p.read_text(errors="replace").splitlines()
    except OSError:
        return FAIL
    if start is not None:
        lo = max(start - 1 - LINE_SLACK, 0)
        hi = min((end or start) + LINE_SLACK, len(lines))
        if quote in _norm("\n".join(lines[lo:hi])):
            return PASS
    if quote in _norm("\n".join(lines)):
        return LOOSE if start is not None else PASS
    return FAIL


def check_evidence(repo: Path, evidence: list[dict], alt_roots: tuple[str, ...] = ()) -> tuple[str, list[str]]:
    """Return (overall, per-item). Any fabricated quote fails the whole claim."""
    results = [check_one(repo, ev, alt_roots) for ev in evidence or []]
    if any(r == FAIL for r in results):
        return FAIL, results
    if any(r in (PASS, LOOSE) for r in results):
        return PASS, results
    return NA, results


def cited_files(evidence: list[dict], alt_roots: tuple[str, ...] = ()) -> set[str]:
    out = set()
    for ev in evidence or []:
        if ev.get("type") == "file":
            out.add(clean_rel(parse_location(ev.get("location", ""))[0], alt_roots))
    return out
