"""Coverage-gate scorer (benchmarks/coverage_check.py, owner-approved
2026-07-10): grades the covering-set output against a ground truth distilled
from the Fable-grader answer keys. must_cover rows need every anchor in the
covered sentences; must_flag rows must be unsupported or amber-flagged on a
matching term (over-claiming fails); watch rows never fail. No API calls.

Run:  venv/bin/python3 -m unittest tests.test_coverage_check -v
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "benchmarks"))

import coverage_check


def _claim(cid, verdict="supported", covered=(), uncovered=()):
    return {"id": cid, "verdict": verdict,
            "covering": {"covered": [{"sentence": s} for s in covered],
                         "uncovered": list(uncovered)}}


def _run(claims, gt_claims):
    return coverage_check.check({"text_claims": claims}, {"claims": gt_claims})


class TestMustCover(unittest.TestCase):

    def test_all_anchors_present_passes(self):
        f, w, n = _run([_claim("t1", covered=["Experts UNDERESTIMATED AI progress.",
                                              "Superforecasters said 9.3% overall."])],
                       [{"id": "t1", "kind": "must_cover",
                         "anchors": ["underestimated ai progress", "9.3%"]}])
        self.assertEqual(f, [])
        self.assertEqual(n, 1)

    def test_missing_anchor_fails(self):
        f, _, _ = _run([_claim("t1", covered=["Experts underestimated AI progress."])],
                       [{"id": "t1", "kind": "must_cover",
                         "anchors": ["underestimated ai progress", "9.3%"]}])
        self.assertEqual(len(f), 1)
        self.assertIn("9.3%", f[0])

    def test_curly_quotes_and_dashes_normalize(self):
        f, _, _ = _run([_claim("t1", covered=["Forecasters’ scores — MATH, MMLU — fell."])],
                       [{"id": "t1", "kind": "must_cover",
                         "anchors": ["forecasters' scores - math"]}])
        self.assertEqual(f, [])

    def test_empty_covering_fails(self):
        f, _, _ = _run([_claim("t1")],
                       [{"id": "t1", "kind": "must_cover", "anchors": ["x"]}])
        self.assertIn("no covering set", f[0])

    def test_unsupported_verdict_fails(self):
        f, _, _ = _run([_claim("t1", verdict="unsupported", covered=["x"])],
                       [{"id": "t1", "kind": "must_cover", "anchors": ["x"]}])
        self.assertIn("expected supported", f[0])


class TestMustFlag(unittest.TestCase):

    def test_unsupported_verdict_passes(self):
        f, _, _ = _run([_claim("t9", verdict="unsupported")],
                       [{"id": "t9", "kind": "must_flag", "flag_terms": ["base rate"]}])
        self.assertEqual(f, [])

    def test_matching_uncovered_part_passes(self):
        f, _, _ = _run([_claim("t9", covered=["something"],
                               uncovered=["uses outside-view BASE RATES from past events"])],
                       [{"id": "t9", "kind": "must_flag", "flag_terms": ["base rate"]}])
        self.assertEqual(f, [])

    def test_overclaiming_fails(self):
        f, _, _ = _run([_claim("t9", covered=["something"], uncovered=[])],
                       [{"id": "t9", "kind": "must_flag",
                         "flag_terms": ["base rate"], "note": "framing"}])
        self.assertEqual(len(f), 1)
        self.assertIn("OVER-CLAIMING", f[0])


class TestWatchAndEdges(unittest.TestCase):

    def test_watch_never_fails_and_is_tracked(self):
        f, w, n = _run([_claim("t3", verdict="unsupported")],
                       [{"id": "t3", "kind": "watch", "note": "known miss"}])
        self.assertEqual(f, [])
        self.assertEqual(n, 0)
        self.assertEqual(len(w), 1)
        self.assertIn("t3", w[0])

    def test_missing_claim_fails_hard_rows(self):
        f, _, _ = _run([], [{"id": "t1", "kind": "must_cover", "anchors": ["x"]}])
        self.assertIn("not found", f[0])

    def test_gate_v2_essay_file_passes_against_live_round1_if_present(self):
        # Gate v2 (2026-07-11): the essay GT supersedes coverage_ground_truth_round1
        # and must hold on the frozen round-1 analysis too (same text).
        import json
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        analysis = os.path.join(root, "data/loop_rounds/round_1/app/analysis.json")
        gt = os.path.join(root, "benchmarks/coverage_ground_truth_essay.json")
        if not os.path.exists(analysis):
            self.skipTest("round-1 analysis not on this machine")
        f, _, n = coverage_check.check(json.load(open(analysis)), json.load(open(gt)))
        self.assertEqual(f, [], f"gate-v2 essay file failing on round-1 analysis: {f}")
        self.assertGreaterEqual(n, 8)


if __name__ == "__main__":
    unittest.main()
