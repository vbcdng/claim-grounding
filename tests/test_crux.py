"""Crux finder — no API calls (topology) + one fake-LLM confirm pass.

Run:  venv/bin/python3 -m unittest tests.test_crux -v
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.papertrail import crux


class FakeLLM:
    def __init__(self, response, model="fake-model"):
        self.response = response
        self.model = model
        self.calls = 0

    def call(self, prompt, temperature=0.0, max_output_tokens=1024):
        self.calls += 1
        return self.response


# t1 is the thesis; t2 supports it, t3 attacks it, t4 is unconnected.
ARGMAP = {
    "nodes": [
        {"id": "t1", "text": "Cities should ban cars downtown.", "role": "thesis"},
        {"id": "t2", "text": "Car bans cut pollution 30%.", "role": "premise"},
        {"id": "t3", "text": "Car bans hurt retail revenue.", "role": "premise"},
        {"id": "t4", "text": "Parking is expensive, as an aside.", "role": "aside"},
    ],
    "edges": [
        {"from": "t2", "to": "t1", "type": "support", "confidence": 0.9},
        {"from": "t3", "to": "t1", "type": "attack", "confidence": 0.8},
    ],
    "thesis_ids": ["t1"],
}


class TestTopology(unittest.TestCase):
    def test_thesis_excluded_and_aside_scores_zero(self):
        payload = crux.find_cruxes(ARGMAP)
        ids = [c["id"] for c in payload["cruxes"]]
        self.assertNotIn("t1", ids)          # thesis is never its own crux
        self.assertNotIn("t4", ids)          # unconnected → zero leverage, filtered
        self.assertEqual(payload["method"], "topology")

    def test_direct_supporter_and_attacker_are_cruxes(self):
        payload = crux.find_cruxes(ARGMAP)
        ids = [c["id"] for c in payload["cruxes"]]
        self.assertIn("t2", ids)
        self.assertIn("t3", ids)
        for c in payload["cruxes"]:
            self.assertGreaterEqual(c["score"], 0.0)
            self.assertLessEqual(c["score"], 1.0)

    def test_scores_normalized_top_is_one(self):
        payload = crux.find_cruxes(ARGMAP)
        self.assertAlmostEqual(max(c["score"] for c in payload["cruxes"]), 1.0)


class TestLLMConfirm(unittest.TestCase):
    def test_confirm_drops_non_crux(self):
        # Confirm keeps candidate 1, drops candidate 2 as non-crux.
        resp = json.dumps({"cruxes": [
            {"n": 1, "is_crux": True, "why": "conclusion rests on it"},
            {"n": 2, "is_crux": False, "why": "redundant"},
        ]})
        llm = FakeLLM(resp)
        payload = crux.find_cruxes(ARGMAP, llm=llm, confirm_with_llm=True)
        self.assertEqual(llm.calls, 1)
        self.assertEqual(payload["method"], "topology+llm")
        self.assertEqual(payload["model"], "fake-model")
        self.assertEqual(len(payload["cruxes"]), 1)

    def test_confirm_failopen_on_bad_json(self):
        before = crux.find_cruxes(ARGMAP)["cruxes"]
        llm = FakeLLM("garbage")
        payload = crux.find_cruxes(ARGMAP, llm=llm, confirm_with_llm=True)
        # Unparseable → keep the topology ranking, don't silently delete cruxes.
        self.assertEqual(len(payload["cruxes"]), len(before))


class TestFragilityV2(unittest.TestCase):
    """v2: leverage × evidential fragility (analysis passed in). Zero calls."""

    def _analysis(self, t2_verdict="unsupported", t3_extra=None):
        t3 = {"id": "t3", "text": "Car bans hurt retail revenue.",
              "verdict": "supported", "paper_ids": ["x", "y"]}
        t3.update(t3_extra or {})
        return {"text_claims": [
            {"id": "t2", "text": "Car bans cut pollution 30%.",
             "verdict": t2_verdict, "paper_ids": ["x"]},
            t3,
        ]}

    def test_v1_unchanged_without_analysis(self):
        payload = crux.find_cruxes(ARGMAP)
        self.assertEqual(payload["method"], "topology")
        for c in payload["cruxes"]:
            self.assertNotIn("fragility", c)

    def test_unsupported_outranks_well_supported_at_equal_leverage(self):
        # t2/t3 have identical topology; fragility must break the tie.
        indep = {"per_claim": {"t3": {"cited": 2, "effective": 2}}}
        payload = crux.find_cruxes(ARGMAP, analysis=self._analysis(),
                                   independence=indep)
        self.assertEqual(payload["method"], "topology+fragility")
        ranked = [c["id"] for c in payload["cruxes"]]
        self.assertLess(ranked.index("t2"), ranked.index("t3"))
        top = payload["cruxes"][0]
        self.assertEqual(top["fragility"], 1.0)
        self.assertIn("unsupported", top["why"])

    def test_correlated_sources_raise_fragility(self):
        base = {"per_claim": {"t3": {"cited": 2, "effective": 2}}}
        correlated = {"per_claim": {"t3": {"cited": 2, "effective": 1}}}
        f_indep = [c for c in crux.find_cruxes(
            ARGMAP, analysis=self._analysis(), independence=base)["cruxes"]
            if c["id"] == "t3"][0]
        f_corr = [c for c in crux.find_cruxes(
            ARGMAP, analysis=self._analysis(), independence=correlated)["cruxes"]
            if c["id"] == "t3"][0]
        self.assertGreater(f_corr["fragility"], f_indep["fragility"])
        self.assertIn("independent", f_corr["why"])

    def test_flags_raise_fragility(self):
        flagged = self._analysis(t3_extra={"partial_support": {"reason": "x"}})
        f = [c for c in crux.find_cruxes(ARGMAP, analysis=flagged)["cruxes"]
             if c["id"] == "t3"][0]
        self.assertEqual(f["fragility"], 0.7)
        disputed = self._analysis(t3_extra={"second_opinion": {"agrees": False}})
        f2 = [c for c in crux.find_cruxes(ARGMAP, analysis=disputed)["cruxes"]
              if c["id"] == "t3"][0]
        self.assertEqual(f2["fragility"], 0.7)

    def test_own_fact_more_fragile_than_own_opinion(self):
        analysis = {"text_claims": [
            {"id": "t2", "text": "Car bans cut pollution 30%.", "verdict": "own",
             "paper_ids": [], "own_kind": {"kind": "fact"}},
            {"id": "t3", "text": "Car bans hurt retail revenue.", "verdict": "own",
             "paper_ids": [], "own_kind": {"kind": "opinion"}},
        ]}
        by_id = {c["id"]: c for c in
                 crux.find_cruxes(ARGMAP, analysis=analysis)["cruxes"]}
        self.assertGreater(by_id["t2"]["fragility"], by_id["t3"]["fragility"])
        self.assertIn("uncited factual assertion", by_id["t2"]["why"])

    def test_confirm_method_string_composes(self):
        resp = json.dumps({"cruxes": [{"n": 1, "is_crux": True, "why": "yes"}]})
        payload = crux.find_cruxes(ARGMAP, llm=FakeLLM(resp),
                                   confirm_with_llm=True,
                                   analysis=self._analysis())
        self.assertEqual(payload["method"], "topology+fragility+llm")


class TestWrite(unittest.TestCase):
    def test_write_cruxes(self):
        with tempfile.TemporaryDirectory() as d:
            crux.write_cruxes(crux.find_cruxes(ARGMAP), d)
            self.assertTrue(os.path.exists(os.path.join(d, "crux.json")))


if __name__ == "__main__":
    unittest.main()
