"""Markdown report for one run."""

from __future__ import annotations

ORDER = ["verified", "partially_verified", "agreed_unchecked", "single_source"]
LABEL = {
    "verified": "verified",
    "partially_verified": "partly verified",
    "agreed_unchecked": "both found, unchecked",
    "single_source": "single source",
}


def _cell(text: str) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ").strip()


def _who(e, names) -> str:
    if e["origin"] == "agreed":
        return "both"
    if e["origin"].startswith("unique_"):
        return f"{names[e['origin'][-1]]} only"
    if e.get("winner"):
        return f"{names[e['winner']]} (conflict won)"
    return "conflict"


def _loc(e, findings_by_id) -> str:
    for m in e["members"]:
        if m in e.get("refuted_members", []):
            continue
        for ev in findings_by_id[m]["evidence"]:
            if ev.get("location"):
                return f"`{ev['location']}`"
    for v in e.get("verdicts", []):
        for ev in v.get("new_evidence", []):
            if ev.get("location"):
                return f"`{ev['location']}`"
    return ""


def _money(x) -> str:
    return "n/a" if x is None else f"${x:.2f}"


def render(summary: dict, entries: list[dict], findings_by_id: dict) -> str:
    names = {s: f"{d} (A)" if s == "A" else f"{d} (B)" for s, d in summary["agents"].items()}
    short = {s: d for s, d in summary["agents"].items()}
    L = []
    L.append(f"# Crosscheck: {summary['outcome'].replace('_', ' ')}")
    L.append("")
    L.append(f"**Question:** {summary['brief']['question']}")
    L.append("")
    cost = _money(summary["total_cost_usd"])
    if not summary["cost_complete"]:
        cost += " (incomplete: some agents don't report cost; set CROSSCHECK_CODEX_PRICE)"
    L.append(f"Analysts: A = {short['A']}, B = {short['B']} · cross-exam rounds: {summary['rounds']} · "
             f"clustering: {summary['cluster_mode']} · time: {summary['duration_s']:.0f}s · cost: {cost} · "
             f"run `{summary['run_id']}`")
    L.append("")

    if summary["errors"]:
        L.append("> **Errors:** " + " · ".join(summary["errors"]))
        L.append("")

    accepted = sorted((e for e in entries if e["status"] in ORDER), key=lambda e: ORDER.index(e["status"]))
    L.append("## Findings")
    L.append("")
    if accepted:
        L.append("| # | Claim | Found by | Status | Evidence |")
        L.append("|---|---|---|---|---|")
        for i, e in enumerate(accepted, 1):
            L.append(f"| {i} | {_cell(e['claim'])} | {_who(e, short)} | {LABEL[e['status']]} | {_loc(e, findings_by_id)} |")
    else:
        L.append("_No findings survived._")
    L.append("")

    L.append("## What each analyst missed")
    L.append("")
    L.append("Verified findings that only the other analyst produced.")
    L.append("")
    for s in ("A", "B"):
        items = summary["missed"][s]
        if not items:
            L.append(f"- **{names[s]}** missed nothing that was verified.")
            continue
        L.append(f"- **{names[s]}** missed:")
        for it in items:
            hint = f" ({it['hint']})" if it["hint"] else ""
            L.append(f"  - {it['claim']}{hint}")
    L.append("")

    refuted = [e for e in entries if e["status"] == "refuted" or e.get("refuted_members")]
    if refuted:
        L.append("## Refuted")
        L.append("")
        for e in refuted:
            ids = e["refuted_members"] if e["status"] != "refuted" else e["members"]
            for fid in ids:
                f = findings_by_id[fid]
                why = e["status_reason"] if e["status"] == "refuted" else "a contradicting claim won on evidence"
                counter = next((v for v in e.get("verdicts", []) if v["target"] == fid and v["verdict"] == "refuted"), None)
                if counter:
                    loc = next((ev["location"] for ev in counter["new_evidence"] if ev.get("location")), "")
                    why = f"{counter['reason']}" + (f" (`{loc}`)" if loc else "")
                elif f["mechanical"] == "fail":
                    why = "its quoted evidence does not match the files"
                L.append(f"- ~~{f['claim']}~~ ({short[f['analyst']]}): {why}")
        L.append("")

    disputed = [e for e in entries if e["status"] == "disputed"]
    if disputed:
        L.append("## Disputed: needs a human")
        L.append("")
        for e in disputed:
            L.append(f"**{e['cluster_id']}**: {e['status_reason']}")
            for fid in e["members"]:
                f = findings_by_id[fid]
                locs = ", ".join(f"`{ev['location']}`" for ev in f["evidence"] if ev.get("location"))
                L.append(f"- {short[f['analyst']]}: {f['claim']} {locs}")
            for v in e.get("verdicts", []):
                locs = ", ".join(f"`{ev['location']}`" for ev in v["new_evidence"] if ev.get("location"))
                L.append(f"  - {short[v['by']]} on {v['target']}: **{v['verdict']}**: {v['reason']} {locs}")
            L.append("")

    questions = [e for e in entries if e["status"] == "open_question"]
    if questions:
        L.append("## Open questions")
        L.append("")
        for e in questions:
            L.append(f"- {e['claim']} ({_who(e, short)})")
        L.append("")

    L.append("## Coverage")
    L.append("")
    for s in ("A", "B"):
        cov = summary["coverage"].get(s) or {}
        ex = ", ".join(cov.get("examined", [])) or "n/a"
        nex = ", ".join(cov.get("not_examined", [])) or "none listed"
        L.append(f"- **{short[s]}** examined: {ex}. Not examined: {nex}.")
    L.append("")

    notes = summary["warnings"] + ([summary["cluster_note"]] if summary["cluster_note"] else [])
    if notes:
        L.append("## Notes")
        L.append("")
        L.extend(f"- {n}" for n in notes)
        L.append("")
    L.append(f"_Full ledger, raw transcripts and prompts: `{summary['run_dir']}`_")
    L.append("")
    return "\n".join(L)
