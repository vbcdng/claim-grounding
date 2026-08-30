"""Offline tests for benchmarks/arbiter_replay.py — fake run dir, fake LLM."""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "benchmarks"))

import arbiter_replay  # noqa: E402

REAL_SENT = "The kiln was fired at 900 degrees celsius for two full days."
FAKE_SENT = "Totally fabricated sentence that appears nowhere in the source."


class FakeLLM:
    def __init__(self, response):
        self.model = "fake/candidate"
        self.response = response
        self.calls = 0

    def call(self, prompt, **kw):
        self.calls += 1
        return self.response


class FakeJudge:
    """Primary-judge stand-in for the rescue re-judge (combined-prompt votes)."""

    def __init__(self, supported=True):
        self.model = "fake/judge"
        self.supported = supported
        self.calls = 0

    def call(self, prompt, **kw):
        self.calls += 1
        return json.dumps({"supported": self.supported, "reason": "fake judge vote"})


def _write_run(base, name, timestamp="2026-07-19T10:00:00"):
    run = os.path.join(base, "data", name)
    os.makedirs(os.path.join(run, "source_claims"))
    claims = [
        {"id": "t1", "text": "The kiln was fired at 900 degrees.", "verdict": "unsupported",
         "paper_ids": ["p1"], "evidences": [],
         "arbiter": {"model": "deepseek/deepseek-v4-flash", "prompt_sha": "aaa",
                     "trigger": "unsupported", "action": "add_citation_or_rewrite",
                     "missing_subclaim": "", "rewrite_suggestion": "",
                     "proofs": [], "quotes_dropped": 0, "conflict": None, "why": ""}},
        {"id": "t2", "text": "Pots dry slowly.", "verdict": "supported",
         "method": "arbiter_rescue", "paper_ids": ["p1"],
         "evidences": [{"paper_id": "p1", "sentence": REAL_SENT, "supported": True,
                        "via": "arbiter_rescue"},
                       {"paper_id": "p1", "sentence": "An original evidence line kept by rescue.",
                        "supported": True}],
         "arbiter": {"model": "deepseek/deepseek-v4-flash", "prompt_sha": "aaa",
                     "trigger": "unsupported", "action": "wrong_or_insufficient_evidence",
                     "missing_subclaim": "", "rewrite_suggestion": "",
                     "proofs": [REAL_SENT], "quotes_dropped": 0, "conflict": None,
                     "why": "", "rescued": True}},
        {"id": "t3", "text": "Glaze needs quartz.", "verdict": "supported",
         "proof_state": "arbiter_resolved", "paper_ids": ["p1"], "evidences": [],
         "covering": {"uncovered": ["quartz"],
                      "arbiter_resolution": {"model": "x", "proofs": [REAL_SENT], "why": ""}},
         "arbiter": {"model": "deepseek/deepseek-v4-flash", "prompt_sha": "aaa",
                     "trigger": "uncovered_components", "action": "supported",
                     "missing_subclaim": "", "rewrite_suggestion": "",
                     "proofs": [REAL_SENT], "quotes_dropped": 0, "conflict": None, "why": ""}},
    ]
    analysis = {"text_claims": claims,
                "sources": [{"paper_id": "p1", "title": "Pots Paper — A. Author"}],
                "metadata": {"text_file": "/somewhere/pots_loop.md",
                             "timestamp": timestamp, "model": "gemini/judge"}}
    with open(os.path.join(run, "analysis.json"), "w") as f:
        json.dump(analysis, f)
    with open(os.path.join(run, "source_claims", "p1.json"), "w") as f:
        json.dump({"paper_id": "p1", "title": "stale cached title",
                   "sentences": [{"text": REAL_SENT, "page": 1},
                                 {"text": "Clay pots dry slowly in humid coastal weather.",
                                  "page": 1}]}, f)
    return run


def _bench_dir(base):
    bench = os.path.join(base, "bench")
    os.makedirs(bench)
    with open(os.path.join(bench, "coverage_ground_truth_pots.json"), "w") as f:
        json.dump({"claims": [{"id": "t1", "kind": "must_flag", "note": "n"}]}, f)
    return bench


class TestInventoryAndSample(unittest.TestCase):
    def test_inventory_strata_and_dedup(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_run(tmp, "pots_run")
            _write_run(tmp, "pots_run_older_dup", timestamp="2026-07-01T00:00:00")
            inv = arbiter_replay.build_inventory(os.path.join(tmp, "data"), _bench_dir(tmp))
            self.assertEqual(inv["n_duplicate_runs_skipped"], 1)
            self.assertEqual(inv["totals"]["claims"], 3)
            by_id = {c["claim_id"]: c for c in inv["claims"]}
            self.assertEqual(by_id["t1"]["strata"], ["labeled", "amber_survivor"])
            self.assertEqual(by_id["t2"]["strata"], ["flip"])
            self.assertEqual(by_id["t3"]["strata"], ["flip"])
            self.assertEqual(by_id["t1"]["gt"]["kind"], "must_flag")

    def test_sample_is_seeded_and_complete(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_run(tmp, "pots_run")
            inv = arbiter_replay.build_inventory(os.path.join(tmp, "data"), _bench_dir(tmp))
            smp = arbiter_replay.make_sample(inv, n_survivors=5, seed=1)
            self.assertEqual(smp["counts"]["total"], 3)   # 1 labeled + 2 flips, no extra pool
            smp2 = arbiter_replay.make_sample(inv, n_survivors=5, seed=1)
            self.assertEqual([r["claim_id"] for r in smp["rows"]],
                             [r["claim_id"] for r in smp2["rows"]])


class TestRestore(unittest.TestCase):
    def test_rescued_claim_restored_approximately(self):
        rescued = {"id": "t2", "verdict": "supported", "method": "arbiter_rescue",
                   "evidences": [{"via": "arbiter_rescue", "sentence": "a"},
                                 {"sentence": "orig"}],
                   "arbiter": {"trigger": "unsupported", "action": "x"}}
        c, old, approx = arbiter_replay.restore_pre_arbiter(rescued)
        self.assertTrue(approx)
        self.assertEqual(c["verdict"], "unsupported")
        self.assertEqual([e["sentence"] for e in c["evidences"]], ["orig"])
        self.assertNotIn("arbiter", c)
        self.assertEqual(old["trigger"], "unsupported")
        self.assertEqual(rescued["verdict"], "supported")   # input untouched

    def test_resolved_claim_restored_exactly(self):
        resolved = {"id": "t3", "verdict": "supported", "proof_state": "arbiter_resolved",
                    "covering": {"uncovered": ["q"], "arbiter_resolution": {"m": 1}},
                    "arbiter": {"trigger": "uncovered_components", "action": "supported"}}
        c, old, approx = arbiter_replay.restore_pre_arbiter(resolved)
        self.assertFalse(approx)
        self.assertEqual(c["proof_state"], "partial")
        self.assertNotIn("arbiter_resolution", c["covering"])


class TestReplay(unittest.TestCase):
    def test_replay_diffs_and_quote_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = _write_run(tmp, "pots_run")
            with open(os.path.join(run, "analysis.json"), "rb") as f:
                before = f.read()
            inv = arbiter_replay.build_inventory(os.path.join(tmp, "data"), _bench_dir(tmp))
            out = os.path.join(tmp, "out")
            fake = FakeLLM(json.dumps({
                "action": "wrong_or_insufficient_evidence", "missing_subclaim": "m",
                "rewrite_suggestion": "", "proof_sentences": [REAL_SENT, FAKE_SENT],
                "conflict": None, "why": "w"}))
            summary = arbiter_replay.replay(inv["claims"], out,
                                            data_dir=os.path.join(tmp, "data"), llm=fake)
            self.assertEqual(summary["claims"], 3)
            self.assertEqual(summary["no_response"], 0)
            self.assertEqual(fake.calls, 3)
            with open(os.path.join(run, "analysis.json"), "rb") as f:
                self.assertEqual(f.read(), before)   # frozen run untouched

            with open(os.path.join(out, "results.jsonl")) as f:
                results = [json.loads(line) for line in f]
            by_id = {r["claim_id"]: r for r in results}
            for r in results:   # quote gate: 1 verbatim kept, 1 fabricated dropped
                self.assertEqual(r["new"]["n_proofs"], 1)
                self.assertEqual(r["new"]["quotes_dropped"], 1)
            self.assertFalse(by_id["t1"]["action_match"])   # add_citation → w_or_i_e
            self.assertTrue(by_id["t2"]["restored_approx"])
            self.assertEqual(by_id["t2"]["trigger_replay"], "unsupported")
            self.assertFalse(by_id["t3"]["action_match"])
            self.assertEqual(by_id["t3"]["trigger_replay"], "uncovered_components")

            with open(os.path.join(out, "raw_responses.jsonl")) as f:
                raw = [json.loads(line) for line in f]
            self.assertEqual(len(raw), 3)
            self.assertTrue(all(r["response_chars"] > 0 for r in raw))
            with open(os.path.join(out, "report.md")) as f:
                report = f.read()
            self.assertIn("quote gate", report.lower())
            self.assertIn("Flip claims", report)

    def test_estimate_makes_no_calls_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_run(tmp, "pots_run")
            inv = arbiter_replay.build_inventory(os.path.join(tmp, "data"), _bench_dir(tmp))
            out = os.path.join(tmp, "out")
            est = arbiter_replay.replay(inv["claims"], out,
                                        data_dir=os.path.join(tmp, "data"),
                                        estimate_only=True)
            self.assertEqual(est["claims"], 3)
            self.assertGreater(est["est_input_tokens"], 0)
            self.assertFalse(os.path.exists(out))


PROVABLE = json.dumps({
    "action": "wrong_or_insufficient_evidence", "missing_subclaim": "m",
    "rewrite_suggestion": "", "proof_sentences": [REAL_SENT, FAKE_SENT],
    "conflict": None, "why": "w"})
NOT_PROVABLE = json.dumps({
    "action": "add_citation_or_rewrite", "missing_subclaim": "m",
    "rewrite_suggestion": "r", "proof_sentences": [],
    "conflict": None, "why": "w"})


class TestRescueReJudge(unittest.TestCase):
    """The task #24 extension: candidate rulings run through the REAL
    production rescue (arbiter.rescue + injected primary judge) and the real
    amber resolution (arbiter.resolve_ambers, $0)."""

    def _replay(self, tmp, candidate_response, judge):
        run = _write_run(tmp, "pots_run")
        with open(os.path.join(run, "analysis.json"), "rb") as f:
            before = f.read()
        inv = arbiter_replay.build_inventory(os.path.join(tmp, "data"), _bench_dir(tmp))
        out = os.path.join(tmp, "out")
        summary = arbiter_replay.replay(inv["claims"], out,
                                        data_dir=os.path.join(tmp, "data"),
                                        llm=FakeLLM(candidate_response),
                                        judge_llm=judge)
        with open(os.path.join(run, "analysis.json"), "rb") as f:
            self.assertEqual(f.read(), before)   # frozen run still untouched
        with open(os.path.join(out, "results.jsonl")) as f:
            by_id = {r["claim_id"]: r for r in map(json.loads, f)}
        return summary, by_id, out

    def test_positive_judge_flips_and_amber_resolves(self):
        with tempfile.TemporaryDirectory() as tmp:
            judge = FakeJudge(supported=True)
            summary, by_id, out = self._replay(tmp, PROVABLE, judge)

            # t1 + t2 restore to unsupported; candidate ruled provable with a
            # verbatim quote → the real rescue runs and the unanimous judge flips
            for cid in ("t1", "t2"):
                r = by_id[cid]["rescue"]
                self.assertTrue(r["proposed"])
                self.assertTrue(r["would_flip"])
                self.assertFalse(r["no_window"])
                self.assertEqual(r["judge_calls"], 3)   # early_break=False → all votes
                self.assertIn("3-0", r["flip_reason"])
                self.assertIsNone(by_id[cid]["amber"])

            # t3 restores to supported/partial → amber path, judge never touched
            self.assertIsNone(by_id["t3"]["rescue"])
            self.assertEqual(by_id["t3"]["amber"],
                             {"eligible": True, "would_resolve": True})

            self.assertEqual(summary["rescue"],
                             {"unsupported_answered": 2, "proposed": 2,
                              "would_flip": 2, "no_window": 0})
            self.assertEqual(summary["amber"], {"eligible": 1, "would_resolve": 1})
            self.assertEqual(summary["rescue_judge_model"], "fake/judge")
            self.assertEqual(judge.calls, 6)   # 3 votes × 2 rescue attempts

            with open(os.path.join(out, "raw_responses.jsonl")) as f:
                raw = [json.loads(line) for line in f]
            judge_rows = [r for r in raw if r["stage"] == "rescue_judge"]
            self.assertEqual(len(judge_rows), 6)
            self.assertTrue(all(r["model"] == "fake/judge" for r in judge_rows))
            self.assertEqual(len([r for r in raw if r["stage"] == "arbiter"]), 3)

            with open(os.path.join(out, "report.md")) as f:
                report = f.read()
            self.assertIn("Rescue re-judge", report)
            self.assertIn("Amber resolution", report)
            self.assertIn("fake/judge", report)

    def test_negative_judge_vetoes_the_flip(self):
        with tempfile.TemporaryDirectory() as tmp:
            summary, by_id, out = self._replay(tmp, PROVABLE, FakeJudge(supported=False))
            r = by_id["t1"]["rescue"]
            self.assertTrue(r["proposed"])
            self.assertFalse(r["would_flip"])
            self.assertFalse(r["no_window"])
            self.assertEqual(r["judge_calls"], 3)
            self.assertEqual(summary["rescue"]["would_flip"], 0)
            # amber resolution is label-free — the positive candidate ruling
            # still clears it regardless of the judge
            self.assertTrue(by_id["t3"]["amber"]["would_resolve"])
            with open(os.path.join(out, "report.md")) as f:
                self.assertIn("judge vetoed | 2", f.read())   # t1 AND t2 held

    def test_not_provable_ruling_proposes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            judge = FakeJudge(supported=True)
            summary, by_id, _ = self._replay(tmp, NOT_PROVABLE, judge)
            self.assertEqual(by_id["t1"]["rescue"], {"proposed": False})
            self.assertEqual(by_id["t3"]["amber"],
                             {"eligible": True, "would_resolve": False})
            self.assertEqual(judge.calls, 0)
            self.assertEqual(summary["rescue"]["proposed"], 0)

    def test_no_judge_configured_records_unjudged_proposal(self):
        with tempfile.TemporaryDirectory() as tmp:
            summary, by_id, out = self._replay(tmp, PROVABLE, None)
            r = by_id["t1"]["rescue"]
            self.assertTrue(r["proposed"])
            self.assertIsNone(r["would_flip"])
            self.assertNotIn("judge_calls", r)
            self.assertEqual(summary["rescue"]["would_flip"], 0)
            self.assertIsNone(summary["rescue_judge_model"])
            with open(os.path.join(out, "report.md")) as f:
                self.assertIn("NOT re-judged", f.read())


if __name__ == "__main__":
    unittest.main()
