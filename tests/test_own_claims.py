"""'Own claim' category + document-order cards — no API calls.

Run:  venv/bin/python3 -m unittest tests.test_own_claims -v
"""
import os
import sys
import json
import tempfile
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.papertrail import matcher, viewer


class TestOwnVerdict(unittest.TestCase):

    def test_uncited_claim_is_own_not_unsupported(self):
        tc = {"id": "t1", "text": "My original thesis about the world.",
              "markers": [], "paper_ids": []}
        analysis = matcher.run([tc], {}, MagicMock())
        c = analysis["text_claims"][0]
        self.assertEqual(c["verdict"], "own")
        self.assertEqual(c["reason"], "no_citation_marker")
        self.assertEqual(analysis["coverage"]["totals"]["own"], 1)
        self.assertEqual(analysis["coverage"]["totals"]["unsupported"], 0)

    def test_missing_file_stays_unsupported(self):
        tc = {"id": "t1", "text": "A cited claim.", "markers": ["a"],
              "paper_ids": [], "missing_files": ["a.pdf"]}
        analysis = matcher.run([tc], {}, MagicMock())
        c = analysis["text_claims"][0]
        self.assertEqual(c["verdict"], "unsupported")
        self.assertIn("source_file_missing", c["reason"])


class TestViewerOrderAndFilter(unittest.TestCase):

    def _analysis(self):
        def claim(i, verdict, **kw):
            base = {"id": f"t{i}", "text": f"Claim number {i} text.", "markers": [],
                    "paper_ids": [], "verdict": verdict, "reason": "", "evidences": []}
            base.update(kw)
            return base
        return {
            "text_claims": [
                claim(1, "unsupported", markers=["a"], paper_ids=["p1"], reason="not stated"),
                claim(2, "own", reason="no_citation_marker"),
                claim(3, "supported", markers=["a"], paper_ids=["p1"]),
            ],
            "sources": [{"paper_id": "p1", "key": "a", "filename": "a.txt", "title": "S"}],
            "coverage": {"totals": {"claims": 3, "supported": 1, "unsupported": 1,
                                    "own": 1, "omitted": 0}},
            "metadata": {}, "omitted": [],
        }

    def _render(self):
        out = os.path.join(tempfile.mkdtemp(), "v.html")
        viewer.generate(self._analysis(), out)
        with open(out, encoding="utf-8") as f:
            return f.read()

    def test_cards_follow_text_order_not_verdict_groups(self):
        page = self._render()
        i1, i2, i3 = (page.index('id="card-t1"'), page.index('id="card-t2"'),
                      page.index('id="card-t3"'))
        self.assertTrue(i1 < i2 < i3)     # document order, though verdicts alternate

    def test_filter_bar_present_with_counts(self):
        page = self._render()
        self.assertIn('data-f="all">All (3)', page)
        self.assertIn('data-f="supported">Supported (1)', page)
        self.assertIn('data-f="unsupported">Unsupported (1)', page)
        self.assertIn('data-f="own">Your own (1)', page)

    def test_own_card_badge_and_note(self):
        page = self._render()
        self.assertIn("YOUR OWN CLAIM", page)
        self.assertIn("your own idea, argument, or transition", page)
        self.assertIn("3 claims", page)   # header totals include the own count
        self.assertIn("1 your own", page)

    def test_header_scope_note_and_honest_unsupported_count(self):
        page = self._render()
        self.assertIn("not that the source is strong or the claim is true", page)
        self.assertIn("1 unsupported", page)
        self.assertNotIn("unverifiable", page)     # nothing missing in this fixture

    def test_missing_file_claims_split_out_of_unsupported(self):
        analysis = self._analysis()
        analysis["text_claims"].append(
            {"id": "t4", "text": "Cites a missing file.", "markers": ["b"],
             "paper_ids": [], "verdict": "unsupported",
             "reason": "source_file_missing: b.pdf", "evidences": []})
        analysis["coverage"]["totals"].update({"claims": 4, "unsupported": 2})
        out = os.path.join(tempfile.mkdtemp(), "v.html")
        viewer.generate(analysis, out)
        with open(out, encoding="utf-8") as f:
            page = f.read()
        self.assertIn("1 unsupported", page)       # only the JUDGED failure
        self.assertIn("1 unverifiable", page)
        self.assertIn("source file missing", page)

    def test_claim_id_visible_for_cross_referencing(self):
        page = self._render()
        self.assertIn('<span class="claimno">t1</span>', page)
        self.assertIn('<span class="claimno">t2</span>', page)
        self.assertIn('<span class="claimno">t3</span>', page)


if __name__ == "__main__":
    unittest.main()
