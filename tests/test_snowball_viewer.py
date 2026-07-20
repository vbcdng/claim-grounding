"""Offline tests for snowball_viewer — HTML generation only, no API/network.

Run:  venv/bin/python3 -m unittest tests.test_snowball_viewer -v
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.papertrail import snowball_viewer as sv


RESULT = {
    "target": "chimpanzees prepare for multiple future outcomes",
    "candidates": [
        {"paper_id": "S1", "title": "Prospective cognition in great apes",
         "year": 2021, "abstract": "We review ape planning.", "doi": None,
         "url": "https://doi.org/10.1/abc", "relevance": 0.82,
         "found_via": ["S1"], "reason": "cosine 0.82 to target"},
        {"paper_id": "N1", "title": "Forked-tube task in chimpanzees & orangutans",
         "year": 2019, "abstract": "Two-exit tube experiment.", "doi": None,
         "url": "https://www.semanticscholar.org/paper/N1", "relevance": 0.55,
         "found_via": ["S1", "N1"], "reason": "on point"},
        {"paper_id": "D2", "title": "A loosely related paper", "year": 2010,
         "abstract": "", "doi": None, "url": None, "relevance": 0.31,
         "found_via": ["S1", "N1", "D2"], "reason": ""},
    ],
    "edges": [{"from": "S1", "to": "N1", "kind": "cites"},
              {"from": "N1", "to": "D2", "kind": "cited_by"}],
    "seeds": [{"paper_id": "S1", "title": "Prospective cognition in great apes",
               "relevance": 0.82}],
    "max_depth": 2,
    "status": "ok",
}


class TestGenerate(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "snowball_viewer.html")
        sv.generate(RESULT, self.path, title="Snowball test")
        with open(self.path, encoding="utf-8") as f:
            self.doc = f.read()

    def test_is_self_contained_html(self):
        self.assertTrue(self.doc.startswith("<!doctype html>"))
        self.assertIn("</html>", self.doc)
        self.assertNotIn("<link", self.doc)
        self.assertNotIn('src="http', self.doc)

    def test_shows_target_and_titles(self):
        self.assertIn("chimpanzees prepare for multiple future outcomes", self.doc)
        self.assertIn("Prospective cognition in great apes", self.doc)
        self.assertIn("Forked-tube task in chimpanzees &amp; orangutans", self.doc)  # escaped

    def test_relevance_bands(self):
        self.assertIn('data-band="high"', self.doc)      # 0.82
        self.assertIn('data-band="medium"', self.doc)    # 0.55
        self.assertIn('data-band="low"', self.doc)       # 0.31
        self.assertIn("relevance 0.82", self.doc)

    def test_seed_vs_hop_and_provenance(self):
        self.assertIn('data-seed="true"', self.doc)
        self.assertIn(">seed<", self.doc)
        self.assertIn(">1-hop<", self.doc)
        self.assertIn(">2-hop<", self.doc)
        self.assertIn("reached via", self.doc)
        self.assertIn("this paper", self.doc)            # last hop labelled

    def test_totals(self):
        self.assertIn("3 candidates", self.doc)
        self.assertIn("1 seeds", self.doc)
        self.assertIn("2 followed", self.doc)
        self.assertIn("2 citation edges", self.doc)

    def test_open_link_only_when_url(self):
        self.assertIn("https://doi.org/10.1/abc", self.doc)
        # D2 has no url -> its card must not contain an open link for a missing url
        self.assertEqual(self.doc.count("open ↗"), 2)    # S1 + N1 only

    def test_review_layer(self):
        self.assertIn("downloadReview", self.doc)
        self.assertIn("snowball-review.json", self.doc)
        self.assertIn("pursue", self.doc)
        self.assertIn("skip", self.doc)

    def test_run_id_stable_for_same_target(self):
        p2 = os.path.join(self.tmp, "again.html")
        sv.generate(RESULT, p2, title="Snowball test")
        with open(p2, encoding="utf-8") as f:
            doc2 = f.read()
        key = "snowball_" + __import__("hashlib").sha1(
            RESULT["target"].encode("utf-8")).hexdigest()[:10]
        self.assertIn(key, self.doc)
        self.assertIn(key, doc2)

    def test_returns_output_path(self):
        p = os.path.join(self.tmp, "ret.html")
        self.assertEqual(sv.generate(RESULT, p), p)


class TestStatusAndEmpty(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def _gen(self, result):
        p = os.path.join(self.tmp, "s.html")
        sv.generate(result, p)
        with open(p, encoding="utf-8") as f:
            return f.read()

    def test_search_failed_banner(self):
        doc = self._gen({"target": "t", "candidates": [], "edges": [], "seeds": [],
                         "max_depth": 2, "status": "search_failed"})
        self.assertIn("transient failure", doc)
        self.assertIn("banner warn", doc)

    def test_no_seeds_banner(self):
        doc = self._gen({"target": "t", "candidates": [], "edges": [], "seeds": [],
                         "max_depth": 2, "status": "no_seeds"})
        self.assertIn("matched no papers", doc)

    def test_empty_candidates_placeholder(self):
        doc = self._gen({"target": "t", "candidates": [], "edges": [], "seeds": [],
                         "max_depth": 2, "status": "ok"})
        self.assertIn("No candidates", doc)
        self.assertIn("0 candidates", doc)

    def test_missing_optional_fields_dont_crash(self):
        # a minimal result dict (viewer must be defensive)
        doc = self._gen({"target": "t"})
        self.assertIn("0 candidates", doc)


if __name__ == "__main__":
    unittest.main()
