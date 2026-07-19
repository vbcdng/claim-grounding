"""Viewer UX from the owner walkthrough (2026-07-07, todo items 1-5, 7):
confidence filter chips, the 'other' triage mark, distinguishable review
filenames + remembered save location, the master top-panel toggle, and
two-way scroll sync. String-level checks on the generated page; no API calls.

Run:  venv/bin/python3 -m unittest tests.test_viewer_ux -v
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.papertrail import viewer


def _analysis():
    e_ok = {"paper_id": "p1", "source_title": "Alpha", "supported": True,
            "sentence": "The bridge is long.", "page": 1, "snippet": "The"}
    e_no = {"paper_id": "p1", "source_title": "Alpha", "supported": False,
            "sentence": "Nothing relevant.", "page": 1, "snippet": "Not",
            "via": "llm_fulltext", "cosine": 0.6}
    return {"text_claims": [
                {"id": "t1", "text": "The bridge is long.", "markers": ["a"],
                 "paper_ids": ["p1"], "verdict": "supported", "method": "llm",
                 "reason": "ok", "evidence": e_ok, "evidences": [e_ok]},
                {"id": "t2", "text": "The bridge is haunted.", "markers": ["a"],
                 "paper_ids": ["p1"], "verdict": "supported",
                 "method": "llm_fulltext", "cosine": 0.6, "reason": "weak",
                 "evidence": e_no, "evidences": [e_no]},   # -> medium confidence
            ],
            "sources": [{"paper_id": "p1", "key": "a", "filename": "a.txt",
                         "title": "Alpha"}],
            "coverage": {"totals": {"claims": 2, "supported": 2, "unsupported": 0,
                                    "own": 0, "omitted": 0}},
            "metadata": {"output_dir": "/tmp/runs/paper1_haiku"}, "omitted": []}


class TestViewerUX(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        out = os.path.join(tempfile.mkdtemp(), "v.html")
        viewer.generate(_analysis(), out)
        with open(out, encoding="utf-8") as f:
            cls.page = f.read()

    def test_confidence_filter_button_and_card_class(self):
        self.assertIn('data-f="conf-medium"', self.page)       # item 1
        self.assertIn("Medium confidence (1)", self.page)
        self.assertIn("conf-medium", self.page)                # card class feeds the filter

    def test_other_triage_mark(self):
        self.assertIn('data-mark="other"', self.page)          # item 2
        self.assertIn("'other (see note)'", self.page.replace('"other (see note)"', "'other (see note)'"))

    def test_review_filename_uses_run_name_and_date(self):
        self.assertIn('"run_name": "paper1_haiku"', self.page)  # item 3
        self.assertIn("reviewFileName", self.page)
        self.assertIn("'review_' + (REVIEW_DATA.run.run_name || 'run')", self.page)

    def test_save_location_machinery_present(self):
        self.assertIn("showDirectoryPicker", self.page)        # item 4
        self.assertIn("saveLocBtn", self.page)
        self.assertIn("indexedDB", self.page)

    def test_top_panel_toggle(self):
        self.assertIn('id="topPanel"', self.page)              # item 5
        self.assertIn("toggleTop", self.page)
        self.assertIn("ptui:tophidden", self.page)

    def test_two_way_brush(self):
        self.assertIn("brush('t1', 'text')", self.page)        # item 7
        self.assertIn("brush('t1', 'card')", self.page)
        self.assertIn("from === 'card'", self.page)


if __name__ == "__main__":
    unittest.main()
