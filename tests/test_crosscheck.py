"""Run with:  python -m unittest discover -s tests -v"""

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from crosscheck import cluster, evaluate, ledger, orchestrator  # noqa: E402
from crosscheck.agents import AgentSpec, run_agent  # noqa: E402
from crosscheck.evidence import FAIL, LOOSE, NA, PASS, check_evidence, check_one  # noqa: E402
from crosscheck import schemas  # noqa: E402

EX = ROOT / "examples" / "logout-bug"
REPO = EX / "repo"
QUESTION = "Why are users logged out after about 10 minutes when sessions should last 24 hours?"


def f(fid, claim, loc, quote, kind="cause", mech=None):
    analyst = fid[0]
    ev = [{"type": "file", "location": loc, "quote": quote}]
    return {"id": fid, "analyst": analyst, "claim": claim, "kind": kind, "evidence": ev,
            "mechanical": mech or check_evidence(REPO, ev)[0]}


class EvidenceTests(unittest.TestCase):
    def test_exact_line(self):
        self.assertEqual(check_one(REPO, {"type": "file", "location": "src/config/session.ts:11", "quote": "ttl: 600"}), PASS)

    def test_off_by_two_lines_still_passes(self):
        self.assertEqual(check_one(REPO, {"type": "file", "location": "src/config/session.ts:13", "quote": "ttl: 600"}), PASS)

    def test_wrong_lines_is_loose(self):
        self.assertEqual(check_one(REPO, {"type": "file", "location": "src/config/session.ts:20", "quote": "ttl: 600"}), LOOSE)

    def test_fabricated_quote_fails(self):
        self.assertEqual(check_one(REPO, {"type": "file", "location": "src/config/session.ts:15", "quote": "rolling: true"}), FAIL)

    def test_missing_file_fails(self):
        self.assertEqual(check_one(REPO, {"type": "file", "location": "src/nope.ts:1", "quote": "x"}), FAIL)

    def test_path_traversal_blocked(self):
        self.assertEqual(check_one(REPO, {"type": "file", "location": "../../../../etc/passwd:1", "quote": "root"}), FAIL)

    def test_absence_claim_is_na(self):
        self.assertEqual(check_one(REPO, {"type": "file", "location": "src/server.ts", "quote": ""}), NA)

    def test_absolute_path_in_working_copy(self):
        ev = {"type": "file", "location": "/tmp/work/A/src/config/session.ts:11", "quote": "ttl: 600"}
        self.assertEqual(check_one(REPO, ev, alt_roots=("/tmp/work/A",)), PASS)

    def test_one_bad_quote_fails_the_claim(self):
        evs = [{"type": "file", "location": "src/config/session.ts:11", "quote": "ttl: 600"},
               {"type": "file", "location": "src/config/session.ts:15", "quote": "rolling: true"}]
        self.assertEqual(check_evidence(REPO, evs)[0], FAIL)


class ClusterTests(unittest.TestCase):
    def test_pairs_same_claim(self):
        a = [f("A-1", "The Redis store sets ttl 600 seconds", "src/config/session.ts:11", "ttl: 600")]
        b = [f("B-1", "Redis session TTL is 600 seconds", "src/config/session.ts:8-12", "ttl: 600")]
        self.assertEqual(cluster.cluster_heuristic(a, b), [{"members": ["A-1", "B-1"], "relation": "same"}])

    def test_detects_true_false_conflict(self):
        a = [f("A-1", "rolling is true so activity refreshes the session", "src/config/session.ts:15", "rolling")]
        b = [f("B-1", "rolling is false so activity does not refresh the session", "src/config/session.ts:15", "rolling")]
        self.assertEqual(cluster.cluster_heuristic(a, b)[0]["relation"], "conflict")

    def test_unrelated_claims_not_paired(self):
        a = [f("A-1", "trust proxy is never set", "src/server.ts", "")]
        b = [f("B-1", "canary uses a different session secret", "k8s/deployment-canary.yaml:27", "name: app-secrets")]
        self.assertEqual(cluster.cluster_heuristic(a, b), [])


class LedgerTests(unittest.TestCase):
    def _run(self, fa, fb, clusters, verdicts):
        findings = {"A": fa, "B": fb}
        by_id = {x["id"]: x for x in fa + fb}
        entries = ledger.build(findings, clusters)
        reqs = ledger.verification_requests(entries, by_id, seed="s")
        # verdicts are given per finding id; translate to the anonymized target ids
        vd = {"A": {"verdicts": []}, "B": {"verdicts": []}}
        for side, rs in reqs.items():
            for r in rs:
                if r["_finding_id"] in verdicts:
                    v = dict(verdicts[r["_finding_id"]], target_id=r["target_id"])
                    vd[side]["verdicts"].append(v)
        ledger.apply_verdicts(entries, reqs, vd, REPO, (), by_id)
        return entries

    GOOD_EV = [{"type": "file", "location": "k8s/deployment.yaml:27", "quote": "name: app-secrets-v2"}]
    FAKE_EV = [{"type": "file", "location": "k8s/deployment.yaml:27", "quote": "name: totally-made-up"}]

    def test_unique_confirmed_with_checked_evidence_is_verified(self):
        b = [f("B-1", "canary uses a different secret", "k8s/deployment-canary.yaml:27", "name: app-secrets")]
        e = self._run([], b, [], {"B-1": {"verdict": "confirmed", "new_evidence": self.GOOD_EV, "reason": "yes"}})
        self.assertEqual(e[0]["status"], "verified")

    def test_confirmation_with_fake_evidence_is_downgraded(self):
        b = [f("B-1", "canary uses a different secret", "k8s/deployment-canary.yaml:27", "name: app-secrets")]
        e = self._run([], b, [], {"B-1": {"verdict": "confirmed", "new_evidence": self.FAKE_EV, "reason": "yes"}})
        self.assertEqual(e[0]["status"], "single_source")
        self.assertEqual(e[0]["verdicts"][0]["downgraded_from"], "confirmed")

    def test_refuting_an_absence_claim(self):
        a = [f("A-1", "trust proxy is never set", "src/server.ts", "")]
        ev = [{"type": "file", "location": "src/bootstrap/proxy.ts:5", "quote": 'app.set("trust proxy", 1);'}]
        e = self._run(a, [], [], {"A-1": {"verdict": "refuted", "new_evidence": ev, "reason": "it is set"}})
        self.assertEqual(e[0]["status"], "refuted")

    def test_refuting_a_claim_with_good_evidence_is_disputed(self):
        a = [f("A-1", "ttl is 600 seconds", "src/config/session.ts:11", "ttl: 600")]
        ev = [{"type": "file", "location": "src/config/session.ts:5", "quote": "SESSION_TTL_SECONDS"}]
        e = self._run(a, [], [], {"A-1": {"verdict": "refuted", "new_evidence": ev, "reason": "sessions are meant to last 24h"}})
        self.assertEqual(e[0]["status"], "disputed")
        self.assertEqual(ledger.outcome(e, []), "NOT_CONVERGED")

    def test_conflict_settled_by_quote_check(self):
        a = [f("A-1", "rolling is true", "src/config/session.ts:15", "rolling: true")]
        b = [f("B-1", "rolling is false", "src/config/session.ts:15", "rolling: false")]
        e = ledger.build({"A": a, "B": b}, [{"members": ["A-1", "B-1"], "relation": "conflict"}])
        self.assertEqual((e[0]["status"], e[0]["winner"], e[0]["refuted_members"]), ("verified", "B", ["A-1"]))

    def test_unverifiable_unique_stays_single_source(self):
        a = [f("A-1", "the 24h constant is never used", "src/config/session.ts:5", "SESSION_TTL_SECONDS")]
        e = self._run(a, [], [], {})
        self.assertEqual(e[0]["status"], "single_source")

    def test_open_questions_are_not_cross_examined(self):
        a = [f("A-1", "does Redis evict keys early?", "src/lib/redis.ts", "", kind="open_question")]
        e = ledger.build({"A": a, "B": []}, [])
        self.assertEqual(e[0]["status"], "open_question")


class EndToEndMockTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_full_run_matches_walkthrough(self):
        s = orchestrator.run(QUESTION, REPO, agents=(f"mock:{EX}/mock/claude", f"mock:{EX}/mock/codex"),
                             cluster="heuristic", out_dir=self.tmp, log=lambda m: None)
        self.assertEqual(s["outcome"], "CONVERGED")
        entries = json.loads((Path(s["run_dir"]) / "ledger.json").read_text())
        statuses = sorted(e["status"] for e in entries)
        self.assertEqual(statuses, ["refuted", "verified", "verified", "verified"])
        missed_claims = " ".join(m["claim"] for m in s["missed"]["A"])
        self.assertIn("SESSION_SECRET", missed_claims)
        self.assertEqual(s["missed"]["B"], [])
        self.assertAlmostEqual(s["total_cost_usd"], 2.80, places=2)
        self.assertFalse((Path(s["run_dir"]) / "work").exists(), "working copies should be cleaned up")
        report = Path(s["report_path"]).read_text()
        self.assertIn("as not examined", report)

    def test_rounds_zero_skips_cross_exam(self):
        s = orchestrator.run(QUESTION, REPO, agents=(f"mock:{EX}/mock/claude", f"mock:{EX}/mock/codex"),
                             cluster="heuristic", rounds=0, out_dir=self.tmp, log=lambda m: None)
        entries = json.loads((Path(s["run_dir"]) / "ledger.json").read_text())
        self.assertIn("single_source", {e["status"] for e in entries})
        self.assertFalse(any(e["verdicts"] for e in entries))

    def test_working_copy_excludes_agent_config(self):
        repo = self.tmp / "repo"
        shutil.copytree(REPO, repo)
        (repo / ".claude").mkdir()
        (repo / ".claude" / "settings.json").write_text('{"hooks": {}}')
        (repo / ".mcp.json").write_text("{}")
        s = orchestrator.run(QUESTION, repo, agents=(f"mock:{EX}/mock/claude", f"mock:{EX}/mock/codex"),
                             cluster="heuristic", out_dir=self.tmp / "runs", keep_work=True, log=lambda m: None)
        work = Path(s["run_dir"]) / "work" / "A"
        self.assertTrue((work / "src" / "server.ts").exists())
        self.assertFalse((work / ".claude").exists())
        self.assertFalse((work / ".mcp.json").exists())

    def test_one_agent_failing_gives_error_outcome(self):
        broken = self.tmp / "broken"
        broken.mkdir()
        s = orchestrator.run(QUESTION, REPO, agents=(f"mock:{EX}/mock/claude", f"mock:{broken}"),
                             cluster="heuristic", out_dir=self.tmp / "runs", log=lambda m: None)
        self.assertEqual(s["outcome"], "ERROR")
        self.assertTrue(Path(s["report_path"]).exists())

    def test_hint_in_brief_triggers_warning(self):
        _, warnings = orchestrator.make_brief("I suspect the Redis TTL. Why are users logged out?")
        self.assertTrue(warnings)

    def test_coverage_hint_ignores_prose_coverage(self):
        cited = {"src/config/session.ts"}
        prose = {"examined": ["All nine files in the repository"], "not_examined": []}
        self.assertEqual(orchestrator._coverage_hint(cited, prose), "")
        paths = {"examined": ["k8s/service.yaml"], "not_examined": []}
        self.assertIn("never mentions src/", orchestrator._coverage_hint(cited, paths))

    def test_eval_harness(self):
        res = evaluate.run_eval(EX.parent / "cases.json", cluster="heuristic", out_dir=self.tmp, log=lambda m: None)
        a = res["aggregate"]
        self.assertAlmostEqual(a["recall_A"], 1 / 2)
        self.assertAlmostEqual(a["recall_final"], 1.0)
        self.assertAlmostEqual(a["recall_baseline"], 1 / 2)
        self.assertEqual(a["true_causes_wrongly_rejected"], 0)
        self.assertEqual(a["cases_where_second_agent_added_a_cause"], 1)


class CodexAdapterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.argv_log = self.tmp / "argv.jsonl"
        os.environ["CROSSCHECK_CODEX_BIN"] = str(ROOT / "tests" / "fake_codex.py")
        os.environ["FAKE_CODEX_ARGV_LOG"] = str(self.argv_log)
        answer = self.tmp / "answer.json"
        answer.write_text((EX / "mock" / "codex" / "research.json").read_text())
        os.environ["FAKE_CODEX_ANSWER"] = str(answer)
        os.environ["CROSSCHECK_CODEX_PRICE"] = "1.75,0.175,14"

    def tearDown(self):
        for k in ("CROSSCHECK_CODEX_BIN", "FAKE_CODEX_ARGV_LOG", "FAKE_CODEX_ANSWER", "CROSSCHECK_CODEX_PRICE", "FAKE_CODEX_FAIL"):
            os.environ.pop(k, None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _call(self):
        return run_agent(AgentSpec.parse("codex:gpt-5.3-codex"), prompt="investigate", schema=schemas.FINDINGS,
                         cwd=REPO, phase="research", raw_dir=self.tmp / "raw", label="B", timeout_s=30)

    def test_parses_output_and_prices_tokens(self):
        res = self._call()
        self.assertTrue(res.ok, res.error)
        self.assertEqual(len(res.data["findings"]), 3)
        self.assertEqual(res.tokens["output_tokens"], 4000)
        # 20k uncached * 1.75 + 100k cached * 0.175 + 4k out * 14, per million
        self.assertAlmostEqual(res.cost_usd, (20000 * 1.75 + 100000 * 0.175 + 4000 * 14) / 1e6, places=4)

    def test_safe_flags(self):
        self._call()
        argv = json.loads(self.argv_log.read_text().splitlines()[0])
        self.assertEqual(argv[argv.index("-s") + 1], "read-only")
        for flag in ("--json", "--ephemeral", "--skip-git-repo-check", "--output-schema", "-o"):
            self.assertIn(flag, argv)
        self.assertEqual(argv[argv.index("-m") + 1], "gpt-5.3-codex")
        self.assertNotIn("--dangerously-bypass-approvals-and-sandbox", argv)

    def test_failed_turn_is_an_error(self):
        os.environ["FAKE_CODEX_FAIL"] = "1"
        res = self._call()
        self.assertFalse(res.ok)
        self.assertIn("usage limit", res.error)


class SchemaTests(unittest.TestCase):
    def test_schemas_are_strict_mode_compatible(self):
        """OpenAI strict structured output: every object lists all properties as required."""
        def walk(node):
            if isinstance(node, dict):
                if node.get("type") == "object":
                    self.assertFalse(node.get("additionalProperties", True))
                    self.assertEqual(set(node["required"]), set(node["properties"]))
                for v in node.values():
                    walk(v)
            elif isinstance(node, list):
                for v in node:
                    walk(v)
        for s in (schemas.FINDINGS, schemas.VERDICTS, schemas.CLUSTERS):
            walk(s)


if __name__ == "__main__":
    unittest.main()
