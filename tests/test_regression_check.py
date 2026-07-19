"""Regression harness (benchmarks/regression_check.py) — no API calls.

Run:  venv/bin/python3 -m unittest tests.test_regression_check
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from benchmarks import regression_check as rc


def gt(*entries):
    return {"paper": "paper1", "model": "gemini/gemini-2.5-flash-lite",
            "claims": list(entries)}


def analysis(*claims):
    return {"text_claims": list(claims), "metadata": {"model": "gemini/gemini-2.5-flash-lite"}}


class TestScore(unittest.TestCase):

    def test_matching_verdicts_pass(self):
        rep = rc.score(
            analysis({"id": "t1", "text": "A.", "verdict": "supported"},
                     {"id": "t2", "text": "B.", "verdict": "unsupported"}),
            gt({"id": "t1", "expect": "supported", "text": "A.", "note": ""},
               {"id": "t2", "expect": "unsupported", "text": "B.", "note": ""}))
        self.assertEqual(rep["passes"], 2)
        self.assertEqual(rep["failures"], [])

    def test_flipped_verdict_is_a_failure_in_both_directions(self):
        rep = rc.score(
            analysis({"id": "t1", "text": "A.", "verdict": "unsupported", "reason": "nope"},
                     {"id": "t2", "text": "B.", "verdict": "supported"}),
            gt({"id": "t1", "expect": "supported", "text": "A.", "note": "confirmed"},
               {"id": "t2", "expect": "unsupported", "text": "B.", "note": "audit CORRECT"}))
        self.assertEqual(len(rep["failures"]), 2)
        got = {f["id"]: f for f in rep["failures"]}
        self.assertEqual(got["t1"]["got"], "unsupported")
        self.assertEqual(got["t2"]["got"], "supported")   # the FP direction fails too

    def test_watch_claims_never_fail(self):
        rep = rc.score(
            analysis({"id": "t37", "text": "C.", "verdict": "supported"}),
            gt({"id": "t37", "expect": "watch", "at_creation": "supported",
                "improves_if": "unsupported", "text": "C.", "note": "suspected FP"}))
        self.assertEqual(rep["failures"], [])
        self.assertEqual(rep["watch"][0]["verdict"], "supported")
        self.assertNotIn("change", rep["watch"][0])

    def test_watch_flip_toward_improves_if_is_marked_improved(self):
        rep = rc.score(
            analysis({"id": "t37", "text": "C.", "verdict": "unsupported"}),
            gt({"id": "t37", "expect": "watch", "at_creation": "supported",
                "improves_if": "unsupported", "text": "C.", "note": "suspected FP"}))
        self.assertEqual(rep["watch"][0]["change"], "IMPROVED")

    def test_watch_flip_without_direction_says_review(self):
        rep = rc.score(
            analysis({"id": "t44", "text": "D.", "verdict": "unsupported"}),
            gt({"id": "t44", "expect": "watch", "at_creation": "supported",
                "text": "D.", "note": "owner call open"}))
        self.assertEqual(rep["watch"][0]["change"], "changed — review")

    def test_shifted_id_is_matched_by_text_and_noted(self):
        rep = rc.score(
            analysis({"id": "t9", "text": "Same words.", "verdict": "supported"}),
            gt({"id": "t8", "expect": "supported", "text": "Same  words.", "note": ""}))
        self.assertEqual(rep["passes"], 1)
        self.assertEqual(rep["drifted"][0][1], "t9")

    def test_missing_text_is_a_warning_not_a_failure(self):
        rep = rc.score(
            analysis({"id": "t8", "text": "Rewritten entirely.", "verdict": "supported"}),
            gt({"id": "t8", "expect": "supported", "text": "Original words.", "note": ""}))
        self.assertEqual(rep["failures"], [])
        self.assertEqual(rep["missing"][0]["id"], "t8")


class TestShippedGroundTruth(unittest.TestCase):
    """The checked-in ground truth file itself stays well-formed."""

    def setUp(self):
        with open(rc.DEFAULT_GT, encoding="utf-8") as f:
            self.gt = json.load(f)

    def test_shape_and_classes(self):
        self.assertEqual(self.gt["paper"], "paper1")
        for c in self.gt["claims"]:
            self.assertIn(c["expect"], ("supported", "unsupported", "watch"))
            self.assertTrue(c["text"].strip())
            self.assertTrue(c["note"].strip())
            if c["expect"] == "watch":
                self.assertIn("at_creation", c)

    def test_known_anchor_claims_present(self):
        by_id = {c["id"]: c for c in self.gt["claims"]}
        # t56: re-baselined to watch 2026-07-05 (drift-induced FP under flash-lite),
        # but the audit-correct answer (unsupported) must stay preserved as improves_if.
        self.assertEqual(by_id["t56"]["expect"], "watch")
        self.assertEqual(by_id["t56"].get("improves_if"), "unsupported")  # audit CORRECT
        self.assertEqual(by_id["t27"]["expect"], "supported")     # audit TOO_STRICT, fixed
        self.assertEqual(by_id["t37"]["expect"], "watch")         # suspected FP, owner call open
        self.assertEqual(by_id["t37"].get("improves_if"), "unsupported")

    def test_ids_are_unique(self):
        ids = [c["id"] for c in self.gt["claims"]]
        self.assertEqual(len(ids), len(set(ids)))


if __name__ == "__main__":
    unittest.main()
