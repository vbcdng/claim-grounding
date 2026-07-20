"""Queue #3: the 'Argument structure' viewer panel (argument map / crux /
independence). Offline — asserts the panel renders from an assessment dict and
that a run WITHOUT --argument-map produces no panel (backward compatibility)."""

import os
import tempfile
import unittest

from modules.papertrail import viewer


ANALYSIS = {
    "text_claims": [
        {"id": "t0", "verdict": "supported", "text": "Claim A", "markers": ["k1"]},
        {"id": "t1", "verdict": "unsupported", "text": "Claim B", "markers": ["k2"]},
        {"id": "t2", "verdict": "own", "text": "Thesis claim"},
    ],
    "omitted": [],
    "coverage": {"totals": {"claims": 3, "supported": 1, "own": 1, "omitted": 0},
                 "per_source": {}},
    "metadata": {"model": "gemini/gemini-2.5-flash-lite"},
}

ASSESSMENT = {
    "argument_map": {
        "nodes": [{"id": "t0", "text": "Claim A about behaviour"},
                  {"id": "t2", "text": "Thesis claim"}],
        "edges": [{"from": "t0", "to": "t2", "type": "supports", "confidence": 0.8, "reason": "r"},
                  {"from": "t1", "to": "t2", "type": "attacks", "confidence": 0.6, "reason": "r2"}],
        "thesis_ids": ["t2"], "method": "llm", "model": "gemini/gemini-2.5-flash-lite"},
    "crux": {"cruxes": [{"id": "t2", "text": "Thesis claim", "score": 3,
                         "why": "most depended on", "fragility": "high"}],
             "method": "topology+fragility"},
    "independence": {
        "sources": [{"key": "k1", "title": "Paper One"}, {"key": "k2", "title": "Paper Two"}],
        "pairs": [{"a": "k1", "b": "k2", "relations": ["shared_authors"],
                   "strength": "weak", "why": "same surname"}],
        "clusters": [["k1"], ["k2"]],
        "summary": {"n_sources": 2, "n_clusters": 2, "n_weak_pairs": 1}},
}


class TestAssessmentPanel(unittest.TestCase):
    def _render(self, assessment):
        fd, path = tempfile.mkstemp(suffix=".html")
        os.close(fd)
        self.addCleanup(lambda: os.path.exists(path) and os.remove(path))
        viewer.generate(ANALYSIS, path, title="T", assessment=assessment)
        with open(path, encoding="utf-8") as f:
            return f.read()

    def test_panel_renders_all_three_subsections(self):
        html = self._render(ASSESSMENT)
        for needle in ('id="assess"', "Argument structure", "Cruxes",
                       "Evidence independence", "Argument map", "Thesis claim",
                       "Paper One", "Paper Two", "most depended on",
                       "supports", "attacks", "toggleAssess"):
            self.assertIn(needle, html, f"missing: {needle}")

    def test_no_panel_without_assessment(self):
        # Backward compatibility: existing runs pass no assessment -> no panel.
        self.assertNotIn('id="assess"', self._render(None))
        self.assertNotIn('id="assess"', self._render({}))

    def test_no_flagged_pairs_message(self):
        a = {**ASSESSMENT, "independence": {**ASSESSMENT["independence"], "pairs": []}}
        html = self._render(a)
        self.assertIn("cited sources look independent", html)

    def test_partial_payload_does_not_crash(self):
        # Only crux present (argmap/independence failed and were skipped).
        html = self._render({"crux": ASSESSMENT["crux"]})
        self.assertIn("most depended on", html)
        self.assertIn("no edges inferred", html)

    def test_coverage_ratio_bar(self):
        # Prior-art item #6: the document-level supported/unsupported/own strip.
        html = self._render(None)  # independent of the assessment panel
        self.assertIn("covratio", html)
        self.assertIn("ccbar", html)
        self.assertIn("supported 1", html)   # 1 supported claim in ANALYSIS
        self.assertIn("your own 1", html)     # 1 own claim


class TestCoverageRatioUnit(unittest.TestCase):
    def test_segments_and_empty(self):
        self.assertEqual(viewer._coverage_ratio_bar(0, 0, 0, 0), "")
        bar = viewer._coverage_ratio_bar(3, 2, 1, 4)  # uns=2 incl. 1 unverifiable
        self.assertIn("supported 3", bar)
        self.assertIn("unsupported 1", bar)     # 2 - 1 unverifiable = 1 judged
        self.assertIn("unverifiable 1", bar)
        self.assertIn("your own 4", bar)


if __name__ == "__main__":
    unittest.main()
