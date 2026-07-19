"""Offline tests for origin_viewer — HTML generation only, no API/network.

Run:  venv/bin/python3 -m unittest tests.test_origin_viewer -v
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.papertrail import origin_viewer as ov


ANALYSIS = {
    "metadata": {"timestamp": "2026-07-05 08:23:57"},
    "text_claims": [
        {"id": "t0", "text": "Engelmann et al. report that chimpanzees prepare for outcomes."},
        {"id": "t9", "text": "A claim whose trail could not be resolved."},
    ],
}

TRACES = {
    "t0": {
        "chain": [
            {"paper_id": "LOCAL", "title": "Redshaw & Suddendorf comment",
             "role": "cited", "attribution": "cites:[5]", "confidence": 1.0,
             "reason": "attributes to [5]", "passage": "Engelmann et al. [5] report...",
             "url": None, "doi": None},
            {"paper_id": "S2X", "title": "Chimpanzees prepare for alternative outcomes",
             "role": "origin", "attribution": "own", "confidence": 1.0,
             "reason": "their own experiment", "passage": "We tested chimpanzees...",
             "url": "https://doi.org/10.1098/rsbl.2023.0179", "doi": "10.1098/rsbl.2023.0179"},
        ],
        "origin_found": True, "stopped_because": "primary", "depth": 1,
        "model": "gemini/gemini-2.5-flash-lite", "prompt_sha": "abc",
    },
    "t9": {
        "chain": [
            {"paper_id": "LOCAL", "title": "Some comment", "role": "cited",
             "attribution": "cites:[3]", "confidence": 0.9, "reason": "cites [3]",
             "passage": "as shown in [3]", "url": None, "doi": None},
        ],
        "origin_found": False, "stopped_because": "unfetchable", "depth": 0,
        "model": "gemini/gemini-2.5-flash-lite", "prompt_sha": "abc",
    },
}


class TestGenerate(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "origin_viewer.html")
        ov.generate(TRACES, ANALYSIS, self.path, title="Test trace")
        with open(self.path, encoding="utf-8") as f:
            self.doc = f.read()

    def test_is_self_contained_html(self):
        self.assertTrue(self.doc.startswith("<!doctype html>"))
        self.assertIn("</html>", self.doc)
        # no external resources (server-free / file://)
        self.assertNotIn("http-equiv=\"refresh\"", self.doc)
        self.assertNotIn("<link", self.doc)
        self.assertNotIn("src=\"http", self.doc)

    def test_shows_claim_text_and_chain_roles(self):
        self.assertIn("chimpanzees prepare for outcomes", self.doc)
        self.assertIn("Redshaw &amp; Suddendorf comment", self.doc)   # escaped
        self.assertIn(">cited<", self.doc)
        self.assertIn(">origin<", self.doc)
        self.assertIn("attributes to [5]", self.doc)
        self.assertIn("own assertion", self.doc)

    def test_outcome_badges_and_data_attrs(self):
        self.assertIn('data-origin="true"', self.doc)
        self.assertIn('data-stopped="unfetchable"', self.doc)
        self.assertIn("origin found", self.doc)
        self.assertIn("no origin", self.doc)

    def test_totals(self):
        self.assertIn("2 claims traced", self.doc)
        self.assertIn("1 origin found", self.doc)
        self.assertIn("1 unfetchable", self.doc)

    def test_origin_link_present(self):
        self.assertIn("https://doi.org/10.1098/rsbl.2023.0179", self.doc)

    def test_review_layer_keyed_to_run(self):
        self.assertIn("2026-07-05 08:23:57", self.doc)      # run id in localStorage key
        self.assertIn("downloadReview", self.doc)
        self.assertIn("origin-review.json", self.doc)

    def test_empty_traces_renders_placeholder(self):
        p = os.path.join(self.tmp, "empty.html")
        ov.generate({}, ANALYSIS, p)
        with open(p, encoding="utf-8") as f:
            doc = f.read()
        self.assertIn("No claims traced", doc)
        self.assertIn("0 claims traced", doc)

    def test_returns_output_path(self):
        p = os.path.join(self.tmp, "ret.html")
        self.assertEqual(ov.generate(TRACES, ANALYSIS, p), p)

    def test_html_escaping_of_claim_text(self):
        analysis = {"metadata": {}, "text_claims": [{"id": "t0", "text": "a < b & c > d"}]}
        traces = {"t0": {"chain": [], "origin_found": False,
                         "stopped_because": "low_conf", "depth": 0}}
        p = os.path.join(self.tmp, "esc.html")
        ov.generate(traces, analysis, p)
        with open(p, encoding="utf-8") as f:
            doc = f.read()
        self.assertIn("a &lt; b &amp; c &gt; d", doc)


if __name__ == "__main__":
    unittest.main()
