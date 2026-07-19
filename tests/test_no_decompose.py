"""Decomposition default-OFF (2026-07-10): extract_claims=False must build the
sentence index with ZERO LLM calls, cache round-trips must be honest
(sentence-only cache upgrades to claims when --decompose arrives; a cache
that already has claims is kept in both modes), and the partial-check's
escalated context must degrade gracefully with no claims. No API calls.

Run:  venv/bin/python3 -m unittest tests.test_no_decompose -v
"""
import os
import sys
import json
import tempfile
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.papertrail import source_decomposer, matcher


def _forbidden_llm():
    llm = MagicMock()
    llm.call_json.side_effect = AssertionError("LLM must not be called")
    llm.call.side_effect = AssertionError("LLM must not be called")
    return llm


def _write_source(dirpath: str) -> str:
    p = os.path.join(dirpath, "src.txt")
    with open(p, "w", encoding="utf-8") as f:
        f.write("Egg intake raised LDL cholesterol in the trial. "
                "The cohort enrolled five hundred adults over two years. "
                "Results were consistent across both study sites.")
    return p


class TestNoDecomposeMode(unittest.TestCase):
    def test_sentence_index_without_llm(self):
        with tempfile.TemporaryDirectory() as d:
            path = _write_source(d)
            out = source_decomposer.decompose_source(
                path, "pid1", "key1", os.path.join(d, "cache"),
                _forbidden_llm(), extract_claims=False)
            self.assertEqual(out["claims"], [])
            self.assertFalse(out["decomposed"])
            self.assertGreaterEqual(len(out["sentences"]), 2)

    def test_sentence_only_cache_reused_without_llm(self):
        with tempfile.TemporaryDirectory() as d:
            path = _write_source(d)
            cache = os.path.join(d, "cache")
            source_decomposer.decompose_source(path, "pid1", "key1", cache,
                                               _forbidden_llm(), extract_claims=False)
            again = source_decomposer.decompose_source(path, "pid1", "key1", cache,
                                                       _forbidden_llm(), extract_claims=False)
            self.assertEqual(again["claims"], [])

    def test_decompose_upgrades_sentence_only_cache(self):
        with tempfile.TemporaryDirectory() as d:
            path = _write_source(d)
            cache = os.path.join(d, "cache")
            source_decomposer.decompose_source(path, "pid1", "key1", cache,
                                               _forbidden_llm(), extract_claims=False)
            llm = MagicMock()
            llm.call_json.return_value = ["Egg intake raised LDL cholesterol."]
            out = source_decomposer.decompose_source(path, "pid1", "key1", cache,
                                                     llm, extract_claims=True)
            self.assertTrue(out["decomposed"])
            self.assertEqual(len(out["claims"]), 1)
            self.assertTrue(llm.call_json.called)

    def test_existing_claims_cache_kept_in_no_decompose_mode(self):
        # paid data must never be thrown away: a cache WITH claims is used
        # as-is even when the run has decomposition off.
        with tempfile.TemporaryDirectory() as d:
            path = _write_source(d)
            cache = os.path.join(d, "cache")
            llm = MagicMock()
            llm.call_json.return_value = ["Egg intake raised LDL cholesterol."]
            source_decomposer.decompose_source(path, "pid1", "key1", cache,
                                               llm, extract_claims=True)
            out = source_decomposer.decompose_source(path, "pid1", "key1", cache,
                                                     _forbidden_llm(), extract_claims=False)
            self.assertEqual(len(out["claims"]), 1)


class TestEscalatedContextWithoutClaims(unittest.TestCase):
    def test_partial_round2_context_degrades_to_sentences(self):
        src = {"title": "X", "claims": [],
               "sentences": [{"text": "Egg intake raised LDL cholesterol in the trial.", "page": 1},
                             {"text": "The cohort enrolled five hundred adults.", "page": 1}]}
        ctx = matcher._escalated_context("egg intake raised LDL", src)
        self.assertIn("LDL", ctx)
        self.assertNotIn("Claims this source makes:", ctx)


if __name__ == "__main__":
    unittest.main()
