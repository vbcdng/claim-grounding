"""Judge-confidence proxy (viewer._confidence): high/medium/low derived from the
signals a run already records — vote tallies, deciding pipeline stage, match
strength. No API calls.

Run:  venv/bin/python3 -m unittest tests.test_confidence -v
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.papertrail import viewer


def _claim(**kw):
    base = {"id": "t1", "text": "The bridge is 400 m long.", "markers": ["a"],
            "paper_ids": ["p1"], "verdict": "supported", "method": "llm",
            "reason": "stated verbatim", "cosine": 0.9, "votes": None,
            "evidences": [{"paper_id": "p1", "source_title": "S", "supported": True,
                           "sentence": "The bridge measures 400 m.", "votes": None}]}
    base.update(kw)
    return base


class TestConfidence(unittest.TestCase):

    def test_split_vote_is_low(self):
        lvl, why = viewer._confidence(_claim(verdict="unsupported", votes="2-1"))
        self.assertEqual(lvl, "low")
        self.assertIn("2–1", why)

    def test_split_vote_on_evidence_is_low(self):
        c = _claim(verdict="unsupported")
        c["evidences"][0]["votes"] = "2-1"
        self.assertEqual(viewer._confidence(c)[0], "low")

    def test_indirect_methods_are_medium(self):
        for m in ("combined_fulltext", "tail_rescue", "combined"):
            self.assertEqual(viewer._confidence(_claim(method=m))[0], "medium", m)

    def test_unsupported_with_no_sentence_found_is_medium(self):
        c = _claim(verdict="unsupported", method="llm_fulltext")
        c["evidences"][0]["sentence"] = None
        c["evidences"][0]["supported"] = False
        lvl, why = viewer._confidence(c)
        self.assertEqual(lvl, "medium")
        self.assertIn("missed", why)

    def test_fulltext_supported_with_weak_cosine_is_medium(self):
        c = _claim(method="llm_fulltext", cosine=0.6)
        self.assertEqual(viewer._confidence(c)[0], "medium")

    def test_clean_supported_and_unanimous_unsupported_are_high(self):
        self.assertEqual(viewer._confidence(_claim())[0], "high")
        c = _claim(verdict="unsupported", method="llm_fulltext", votes="3-0")
        c["evidences"][0]["supported"] = False
        self.assertEqual(viewer._confidence(c)[0], "high")

    def test_own_and_missing_file_get_no_tag(self):
        self.assertIsNone(viewer._confidence(_claim(verdict="own")))
        self.assertIsNone(viewer._confidence(
            _claim(verdict="unsupported", reason="source_file_missing: a.pdf")))

    def test_chip_rendered_on_card(self):
        analysis = {"text_claims": [_claim()], "sources": [], "omitted": [],
                    "coverage": {"totals": {"claims": 1, "supported": 1,
                                            "unsupported": 0, "own": 0, "omitted": 0}},
                    "metadata": {}}
        out = os.path.join(tempfile.mkdtemp(), "v.html")
        viewer.generate(analysis, out)
        with open(out, encoding="utf-8") as f:
            page = f.read()
        self.assertIn('confchip high', page)
        self.assertIn('high confidence</span>', page)


if __name__ == "__main__":
    unittest.main()
