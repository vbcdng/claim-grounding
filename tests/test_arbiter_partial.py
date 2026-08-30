"""Partly-proven badge tests (task #1 round 2 step 3, arbiter.partial_map).

The centrality guard is the point: only a proven CORE part with a remaining
gap earns proof_state="partial_from_arbiter". All offline; the LLM is a fake.
Run:  venv/bin/python3 -m unittest tests.test_arbiter_partial -v
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.papertrail import arbiter, viewer


class FakeLLM:
    def __init__(self, response):
        self.model = "fake/arbiter"
        self.response = response
        self.calls = 0

    def call(self, prompt, **kw):
        self.calls += 1
        self.last_prompt = prompt
        return self.response


def mapping(parts):
    return json.dumps({"parts": parts, "why": "mapped."})


def claim(cid="t1", verdict="unsupported", proofs=("Quote one.", "Quote two."),
          action="wrong_or_insufficient_evidence", **kw):
    c = {"id": cid, "text": "The dam, finished in 1932, remains the tallest in the region.",
         "verdict": verdict, "paper_ids": ["p1"],
         "judge_missing_parts": ["The dam remains the tallest in the region."],
         "arbiter": {"model": "fake/arbiter", "prompt_sha": "abc", "trigger": "unsupported",
                     "action": action, "missing_subclaim": "", "rewrite_suggestion": "",
                     "proofs": list(proofs), "quotes_dropped": 0, "conflict": None,
                     "why": "part of it is in the source"}}
    c.update(kw)
    return c


CORE_PLUS_GAP = mapping([
    {"part": "The dam was finished in 1932.", "centrality": "core", "proven_by": [1]},
    {"part": "The dam remains the tallest in the region.", "centrality": "core", "proven_by": []},
])
FRAMING_ONLY = mapping([
    {"part": "The region contains dams.", "centrality": "framing", "proven_by": [1]},
    {"part": "The dam remains the tallest in the region.", "centrality": "core", "proven_by": []},
])
ALL_PROVEN = mapping([
    {"part": "The dam was finished in 1932.", "centrality": "core", "proven_by": [1]},
    {"part": "The dam remains the tallest in the region.", "centrality": "core", "proven_by": [2]},
])


class TestGuard(unittest.TestCase):
    def test_proven_core_with_gap_earns_badge(self):
        c = claim()
        s = arbiter.partial_map([c], FakeLLM(CORE_PLUS_GAP))
        self.assertEqual(c["proof_state"], "partial_from_arbiter")
        self.assertTrue(c["arbiter_partial"]["badge"])
        self.assertEqual(s["badged"], ["t1"])

    def test_framing_only_proven_stays_flat(self):
        c = claim()
        s = arbiter.partial_map([c], FakeLLM(FRAMING_ONLY))
        self.assertNotIn("proof_state", c)
        self.assertFalse(c["arbiter_partial"]["badge"])
        self.assertEqual(s["held"], ["t1"])

    def test_all_parts_proven_is_rescue_territory_not_a_badge(self):
        c = claim()
        arbiter.partial_map([c], FakeLLM(ALL_PROVEN))
        self.assertNotIn("proof_state", c)
        self.assertFalse(c["arbiter_partial"]["badge"])

    def test_verdict_field_never_moves(self):
        c = claim()
        arbiter.partial_map([c], FakeLLM(CORE_PLUS_GAP))
        self.assertEqual(c["verdict"], "unsupported")

    def test_unknown_centrality_downgrades_to_framing(self):
        c = claim()
        arbiter.partial_map([c], FakeLLM(mapping([
            {"part": "The dam was finished in 1932.", "centrality": "essential", "proven_by": [1]},
            {"part": "The dam remains the tallest in the region.", "centrality": "core", "proven_by": []},
        ])))
        self.assertNotIn("proof_state", c)
        self.assertEqual(c["arbiter_partial"]["parts"][0]["centrality"], "framing")

    def test_out_of_range_proof_numbers_do_not_count_as_proof(self):
        c = claim()
        arbiter.partial_map([c], FakeLLM(mapping([
            {"part": "The dam was finished in 1932.", "centrality": "core", "proven_by": [7]},
            {"part": "The dam remains the tallest in the region.", "centrality": "core", "proven_by": []},
        ])))
        self.assertNotIn("proof_state", c)


class TestEligibility(unittest.TestCase):
    def test_supported_claim_never_mapped(self):
        c = claim(verdict="supported")
        llm = FakeLLM(CORE_PLUS_GAP)
        s = arbiter.partial_map([c], llm)
        self.assertEqual((llm.calls, s["checked"]), (0, 0))
        self.assertNotIn("arbiter_partial", c)

    def test_no_proofs_never_mapped(self):
        c = claim(proofs=())
        llm = FakeLLM(CORE_PLUS_GAP)
        arbiter.partial_map([c], llm)
        self.assertEqual(llm.calls, 0)

    def test_author_ruled_claim_skipped(self):
        c = claim(owner_flag={"verdict": "supported"})
        llm = FakeLLM(CORE_PLUS_GAP)
        arbiter.partial_map([c], llm)
        self.assertEqual(llm.calls, 0)

    def test_stale_badge_cleared_when_no_longer_eligible(self):
        # e.g. rescue flipped the verdict on this run; last run's badge must go.
        c = claim(verdict="supported", proof_state="partial_from_arbiter",
                  arbiter_partial={"prompt_sha": "old", "badge": True, "parts": []})
        arbiter.partial_map([c], FakeLLM(CORE_PLUS_GAP))
        self.assertNotIn("proof_state", c)
        self.assertNotIn("arbiter_partial", c)


class TestReuseAndFailure(unittest.TestCase):
    def test_same_prompt_sha_reused_without_a_call(self):
        c = claim()
        llm = FakeLLM(CORE_PLUS_GAP)
        arbiter.partial_map([c], llm)
        first_calls = llm.calls
        s2 = arbiter.partial_map([c], llm)
        self.assertEqual((first_calls, llm.calls), (1, 1))
        self.assertEqual(s2["reused"], 1)
        self.assertEqual(c["proof_state"], "partial_from_arbiter")

    def test_unparseable_response_leaves_no_field(self):
        c = claim()
        arbiter.partial_map([c], FakeLLM("I cannot answer in JSON, sorry."))
        self.assertNotIn("arbiter_partial", c)
        self.assertNotIn("proof_state", c)


class TestViewer(unittest.TestCase):
    def _html(self, c):
        analysis = {"text_claims": [c], "omitted": [],
                    "coverage": {"totals": {}, "per_source": {}},
                    "sources": [], "metadata": {"marker_errors": []}}
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "v.html")
            viewer.generate(analysis, out)
            return open(out).read()

    def test_badge_chip_and_filter_render(self):
        c = claim()
        arbiter.partial_map([c], FakeLLM(CORE_PLUS_GAP))
        html = self._html(c)
        self.assertIn("partly proven", html)
        self.assertIn('<span class="pparbchip"', html)
        self.assertIn('data-f="partlyarb"', html)
        self.assertIn("Quote one.", html)          # proven part shows its quote
        self.assertIn("not proven", html)          # the gap list renders

    def test_no_badge_no_chip(self):
        c = claim()
        arbiter.partial_map([c], FakeLLM(FRAMING_ONLY))
        html = self._html(c)
        self.assertNotIn('<span class="pparbchip"', html)
        self.assertNotIn('data-f="partlyarb"', html)


if __name__ == "__main__":
    unittest.main()
