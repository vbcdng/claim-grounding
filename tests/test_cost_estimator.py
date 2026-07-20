"""Unit tests for the cost estimator + error-reporting fixes. No API calls.

Run:  venv/bin/python3 -m unittest tests.test_cost_estimator -v
"""

import os
import sys
import json
import hashlib
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.papertrail import cost_estimator as ce
from modules.papertrail import source_decomposer, matcher, viewer


def pid_for(filename):
    return hashlib.sha1(filename.encode("utf-8")).hexdigest()


class TestPricingTable(unittest.TestCase):
    def test_real_model_options_table_parses(self):
        prices = ce.load_pricing()
        # the recommended default must be present with sane numbers
        lite = prices.get("gemini/gemini-2.5-flash-lite")
        self.assertIsNotNone(lite, f"flash-lite missing from parsed table: {list(prices)}")
        self.assertEqual(lite, {"input": 0.10, "output": 0.40})
        self.assertGreaterEqual(len(prices), 5)

    def test_unknown_model_gives_no_usd(self):
        with tempfile.TemporaryDirectory() as d:
            est = self._tiny_estimate(d, model="somehost/unknown-model")
            self.assertIsNone(est["usd"])

    def _tiny_estimate(self, d, model):
        src_dir = os.path.join(d, "sources"); os.makedirs(src_dir)
        with open(os.path.join(src_dir, "a.txt"), "w") as f:
            f.write("word " * 100)
        claims = [{"id": "t0", "text": "x", "markers": ["a"]}]
        return ce.estimate(claims, {"a": "a.txt"}, src_dir,
                           os.path.join(d, "cache"), model, pid_for)


class TestEstimate(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.src = os.path.join(self.d, "sources"); os.makedirs(self.src)
        self.cache = os.path.join(self.d, "cache"); os.makedirs(self.cache)

    def _write_source(self, name, words):
        with open(os.path.join(self.src, name), "w") as f:
            f.write("word " * words)

    def test_chunk_math_and_counts(self):
        self._write_source("big.txt", 3000)   # -> ceil(3000/1200) = 3 chunks
        claims = [{"id": "t0", "text": "c", "markers": ["big"]},
                  {"id": "t1", "text": "u", "markers": []}]
        est = ce.estimate(claims, {"big": "big.txt"}, self.src, self.cache,
                          "gemini/gemini-2.5-flash-lite", pid_for)
        self.assertEqual(est["decomposition_calls"], 3)
        self.assertEqual(est["sources_to_decompose"], 1)
        self.assertEqual(est["judgment_calls"], 1 * ce.JUDGE_CALLS_PER_PAIR)
        self.assertEqual(est["uncited_claims"], 1)
        self.assertIsNotNone(est["usd"])
        self.assertGreater(est["usd"]["high"], est["usd"]["low"])

    def test_cached_source_costs_nothing_to_decompose(self):
        self._write_source("a.txt", 2000)
        path = os.path.join(self.src, "a.txt")
        pid = pid_for("a.txt")
        with open(os.path.join(self.cache, f"{pid}.json"), "w") as f:
            json.dump({"file_hash": source_decomposer.file_hash(path)}, f)
        claims = [{"id": "t0", "text": "c", "markers": ["a"]}]
        est = ce.estimate(claims, {"a": "a.txt"}, self.src, self.cache,
                          "gemini/gemini-2.5-flash-lite", pid_for)
        self.assertEqual(est["sources_cached"], 1)
        self.assertEqual(est["decomposition_calls"], 0)
        self.assertGreater(est["judgment_calls"], 0)   # judgments still happen

    def test_preflight_warnings(self):
        self._write_source("empty.txt", 0)
        claims = [{"id": "t0", "text": "c", "markers": ["empty", "gone", "nomap"]}]
        est = ce.estimate(claims, {"empty": "empty.txt", "gone": "gone.pdf"},
                          self.src, self.cache, "gemini/gemini-2.5-flash-lite", pid_for)
        text = "\n".join(est["warnings"])
        self.assertIn("no extractable text", text)
        self.assertIn("gone.pdf", text)
        self.assertIn("no reference mapping", text)


class TestMissingFileReason(unittest.TestCase):
    def test_missing_file_distinct_from_uncited(self):
        claims = [
            {"id": "t0", "text": "cited but file missing", "markers": ["x"],
             "paper_ids": [], "missing_files": ["x.pdf"]},
            {"id": "t1", "text": "genuinely uncited", "markers": [], "paper_ids": []},
        ]
        analysis = matcher.run(claims, {}, llm=None)
        reasons = {c["id"]: c["reason"] for c in analysis["text_claims"]}
        self.assertEqual(reasons["t0"], "source_file_missing: x.pdf")
        self.assertEqual(reasons["t1"], "no_citation_marker")


class TestViewerWarningsBanner(unittest.TestCase):
    def _stub_analysis(self, marker_errors):
        return {"text_claims": [], "omitted": [],
                "coverage": {"totals": {}, "per_source": {}},
                "sources": [],
                "metadata": {"marker_errors": marker_errors}}

    def test_banner_present_when_errors(self):
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "v.html")
            viewer.generate(self._stub_analysis(["marker [[x]] -> file not found: x.pdf"]), out)
            html = open(out).read()
            self.assertIn('<div class="warnbanner">', html)
            self.assertIn("x.pdf", html)

    def test_no_banner_when_clean(self):
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "v.html")
            viewer.generate(self._stub_analysis([]), out)
            self.assertNotIn('<div class="warnbanner">', open(out).read())


class TestAddonWorstCase(unittest.TestCase):
    """The priced ceiling for the conditional passes (owner ask 2026-07-12)."""

    MODEL = "gemini/gemini-2.5-flash-lite"   # priced in docs/MODEL_OPTIONS.md

    def test_positive_and_scales_with_counts(self):
        small = ce.addon_worst_case(self.MODEL, n_own=1,
                                                n_partial=1, n_cover=1)
        big = ce.addon_worst_case(self.MODEL, n_own=10,
                                              n_partial=10, n_cover=10)
        self.assertIsNotNone(small)
        self.assertGreater(small, 0)
        self.assertAlmostEqual(big, small * 10, places=9)

    def test_zero_counts_cost_nothing(self):
        self.assertEqual(ce.addon_worst_case(self.MODEL), 0.0)

    def test_unpriced_model_returns_none(self):
        self.assertIsNone(ce.addon_worst_case("nosuch/model", n_partial=5))


if __name__ == "__main__":
    unittest.main()
