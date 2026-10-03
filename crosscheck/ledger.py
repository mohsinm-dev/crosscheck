"""The ledger: every claim, which bucket it fell in, and what happened to it.

All resolution rules live here as plain functions. Models never decide a
claim's status; they only supply verdicts + evidence, which these rules weigh.

Statuses:
  verified            mechanical evidence check passed, or the other analyst
                      confirmed it with new, checked evidence
  partially_verified  the other analyst confirmed part of it
  single_source       only one analyst found it; not refuted, not confirmed
  agreed_unchecked    both found it, but nothing could be checked mechanically
  refuted             its own evidence failed the check, or checked
                      counter-evidence disproved it
  disputed            evidence on both sides; needs a human
  open_question       reported as a question, not a claim
"""

from __future__ import annotations

import random

from .evidence import FAIL, PASS, check_evidence

ACCEPTED = {"verified", "partially_verified", "single_source", "agreed_unchecked"}
STRONG = {"verified", "partially_verified"}


def other(side: str) -> str:
    return "B" if side == "A" else "A"


def build(findings: dict[str, list[dict]], clusters: list[dict]) -> list[dict]:
    """Initial classification, before cross-examination."""
    by_id = {f["id"]: f for side in findings.values() for f in side}
    entries, clustered = [], set()

    for c in clusters:
        members = [by_id[m] for m in c["members"] if m in by_id]
        clustered |= {m["id"] for m in members}
        e = _entry(len(entries) + 1, members)
        mech = {m["id"]: m["mechanical"] for m in members}
        sides = {s: [m for m in members if m["analyst"] == s] for s in ("A", "B")}
        side_ok = {s: any(mech[m["id"]] == PASS for m in ms) and not any(mech[m["id"]] == FAIL for m in ms)
                   for s, ms in sides.items()}
        side_bad = {s: all(mech[m["id"]] == FAIL for m in ms) for s, ms in sides.items()}

        if c.get("relation") == "conflict":
            e["origin"] = "conflict"
            for s in ("A", "B"):
                if side_ok[s] and side_bad[other(s)]:
                    _resolve_conflict(e, winner=s, sides=sides, status="verified",
                                      reason=f"{s}'s quoted evidence checks out; {other(s)}'s does not")
                    break
            else:
                e["status"] = "pending"
                e["to_verify"] = {s: [m["id"] for m in sides[other(s)]] for s in ("A", "B")}
        else:
            e["origin"] = "agreed"
            if all(v == FAIL for v in mech.values()):
                e["status"], e["status_reason"] = "refuted", "both analysts cited evidence that does not check out"
            elif any(v == PASS for v in mech.values()):
                bad = [m["id"] for m in members if mech[m["id"]] == FAIL]
                good = next(m for m in members if mech[m["id"]] == PASS)
                e["claim"], e["kind"] = good["claim"], good["kind"]
                e["refuted_members"] = bad
                e["status"] = "verified"
                e["status_reason"] = "found by both; quoted evidence checks out" + (
                    f" (quote in {', '.join(bad)} did not match the file)" if bad else "")
            else:
                e["status"], e["status_reason"] = "agreed_unchecked", "found by both; nothing to check mechanically"
        entries.append(e)

    for f in [f for side in ("A", "B") for f in findings.get(side, [])]:
        if f["id"] in clustered:
            continue
        e = _entry(len(entries) + 1, [f])
        e["origin"] = f"unique_{f['analyst']}"
        if f["kind"] == "open_question":
            e["status"], e["status_reason"] = "open_question", "reported as an open question"
        elif f["mechanical"] == FAIL:
            e["status"], e["status_reason"] = "refuted", "its own quoted evidence does not match the files"
        else:
            e["status"] = "pending"
            e["to_verify"] = {other(f["analyst"]): [f["id"]]}
        entries.append(e)
    return entries


def _entry(n: int, members: list[dict]) -> dict:
    return {
        "cluster_id": f"C-{n}",
        "members": [m["id"] for m in members],
        "claim": members[0]["claim"],
        "kind": members[0]["kind"],
        "origin": None,
        "status": None,
        "status_reason": "",
        "verdicts": [],
        "refuted_members": [],
    }


def _resolve_conflict(e, winner, sides, status, reason):
    e["claim"] = sides[winner][0]["claim"]
    e["kind"] = sides[winner][0]["kind"]
    e["winner"] = winner
    e["refuted_members"] = [m["id"] for m in sides[other(winner)]]
    e["status"], e["status_reason"] = status, reason


def verification_requests(entries: list[dict], findings_by_id: dict, seed: str) -> dict[str, list[dict]]:
    """Claims each analyst must verify, anonymized as X-n in random order."""
    requests: dict[str, list[dict]] = {"A": [], "B": []}
    for e in entries:
        for verifier, ids in (e.get("to_verify") or {}).items():
            for fid in ids:
                requests[verifier].append(fid)
    out = {}
    rng = random.Random(seed)
    for verifier, ids in requests.items():
        rng.shuffle(ids)
        out[verifier] = [
            {"target_id": f"X-{i + 1}", "_finding_id": fid,
             "claim": findings_by_id[fid]["claim"], "kind": findings_by_id[fid]["kind"],
             "evidence": findings_by_id[fid]["evidence"]}
            for i, fid in enumerate(ids)
        ]
    return out


def apply_verdicts(entries, requests, verdict_data: dict[str, dict | None], repo, alt_roots, findings_by_id):
    """Attach each analyst's verdicts, downgrading any whose evidence fails the check."""
    verdict_for: dict[str, dict] = {}
    for verifier, reqs in requests.items():
        given = {v.get("target_id"): v for v in ((verdict_data.get(verifier) or {}).get("verdicts") or [])}
        for r in reqs:
            v = given.get(r["target_id"])
            if v is None:
                rec = {"by": verifier, "target": r["_finding_id"], "verdict": "cannot_verify",
                       "reason": "no verdict returned" if verdict_data.get(verifier) else "verifier run failed",
                       "new_evidence": [], "evidence_check": "n/a"}
            else:
                check, _ = check_evidence(repo, v.get("new_evidence", []), alt_roots)
                rec = {"by": verifier, "target": r["_finding_id"], "verdict": v.get("verdict", "cannot_verify"),
                       "reason": v.get("reason", ""), "new_evidence": v.get("new_evidence", []),
                       "evidence_check": check}
                if rec["verdict"] != "cannot_verify" and check != PASS:
                    rec["downgraded_from"] = rec["verdict"]
                    rec["verdict"] = "cannot_verify"
                    rec["reason"] = f"[downgraded: new evidence {check}] " + rec["reason"]
            verdict_for[r["_finding_id"]] = rec

    for e in entries:
        if e["status"] != "pending":
            continue
        targets = [fid for ids in e["to_verify"].values() for fid in ids]
        e["verdicts"] = [verdict_for[t] for t in targets if t in verdict_for]
        if e["origin"].startswith("unique_"):
            _resolve_unique(e, verdict_for.get(targets[0]), findings_by_id[targets[0]])
        else:
            _resolve_pending_conflict(e, verdict_for, findings_by_id)


def _resolve_unique(e, v, finding):
    verdict = (v or {}).get("verdict", "cannot_verify")
    if verdict == "confirmed":
        e["status"], e["status_reason"] = "verified", "confirmed by the other analyst with new, checked evidence"
    elif verdict == "partially_confirmed":
        e["status"], e["status_reason"] = "partially_verified", "partly confirmed by the other analyst"
    elif verdict == "refuted":
        if finding["mechanical"] == PASS:
            e["status"], e["status_reason"] = "disputed", "both sides have checked evidence; needs a human"
        else:
            e["status"], e["status_reason"] = "refuted", "the other analyst disproved it with checked evidence"
    else:
        tail = " (its quoted evidence checks out)" if finding["mechanical"] == PASS else ""
        e["status"], e["status_reason"] = "single_source", "found by one analyst only; not confirmed or refuted" + tail


def _resolve_pending_conflict(e, verdict_for, findings_by_id):
    side_ids = {"A": [m for m in e["members"] if m.startswith("A-")],
                "B": [m for m in e["members"] if m.startswith("B-")]}
    refuted = {s: any(verdict_for.get(m, {}).get("verdict") == "refuted" for m in ids) for s, ids in side_ids.items()}
    confirmed = {s: any(verdict_for.get(m, {}).get("verdict") == "confirmed" for m in ids) for s, ids in side_ids.items()}
    sides = {s: [findings_by_id[m] for m in ids] for s, ids in side_ids.items()}
    for s in ("A", "B"):
        if refuted[other(s)] and not refuted[s]:
            status = "verified" if confirmed[s] or any(f["mechanical"] == PASS for f in sides[s]) else "single_source"
            _resolve_conflict(e, winner=s, sides=sides, status=status,
                              reason=f"{other(s)}'s version was disproved with checked evidence")
            return
    if confirmed["A"] and confirmed["B"] and not (refuted["A"] or refuted["B"]):
        # Each side confirmed the other's version: the "conflict" was a false alarm.
        e["origin"] = "agreed"
        e["status"], e["status_reason"] = "verified", "flagged as conflicting, but each analyst confirmed the other's version"
        return
    e["status"], e["status_reason"] = "disputed", "contradictory claims, not settled by evidence; needs a human"


def finalize_without_exam(entries, reason: str):
    """When cross-examination is skipped or impossible, pending claims stay single-source/disputed."""
    for e in entries:
        if e["status"] == "pending":
            if e["origin"] == "conflict":
                e["status"], e["status_reason"] = "disputed", f"contradictory claims; {reason}"
            else:
                e["status"], e["status_reason"] = "single_source", f"found by one analyst only; {reason}"


def outcome(entries, errors: list[str]) -> str:
    if errors:
        return "ERROR"
    return "NOT_CONVERGED" if any(e["status"] == "disputed" for e in entries) else "CONVERGED"
