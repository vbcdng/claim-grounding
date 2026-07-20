"""Real-data integration smoke for snowball — the WHOLE loop on captured S2
fixtures with REAL SPECTER embeddings (no network). This is the offline stand-in
for the live smoke (which the keyless S2 pool rate-limits): it proves seed
normalize -> pick_relevant real cosine -> neighbors real parse -> edges +
found_via provenance all compose on genuine Semantic Scholar data.

Loads the SPECTER model (~seconds), so it is OPT-IN — it does not run in the
default suite. Enable with:

    SNOWBALL_FIXTURE_INTEGRATION=1 venv/bin/python3 -m unittest tests.test_snowball_integration
"""

import os
import json
import sys
import logging
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.papertrail import paper_search as ps

logging.getLogger("modules.papertrail.paper_search").setLevel(logging.CRITICAL)

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


def _load(name):
    with open(os.path.join(FIX, name), "r", encoding="utf-8") as f:
        return json.load(f)


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


@unittest.skipUnless(os.environ.get("SNOWBALL_FIXTURE_INTEGRATION"),
                     "opt-in: loads SPECTER; set SNOWBALL_FIXTURE_INTEGRATION=1")
class TestSnowballOnRealFixtures(unittest.TestCase):
    def setUp(self):
        ps.s2._key_rejected = False
        self.refs = _load("s2_references_attention.json")
        self.cites = _load("s2_citations_attention.json")
        # real seed papers: a /paper/search hit has the same shape as citedPaper
        self.seed_raw = [d["citedPaper"] for d in self.refs["data"][:2]
                         if d.get("citedPaper")]

    def _router(self, url, params=None, headers=None, timeout=None):
        params = params or {}
        if params.get("offset", 0) > 0:
            return _FakeResp({"data": [], "next": None})
        if url.rstrip("/").endswith("references"):
            return _FakeResp(self.refs)
        if url.rstrip("/").endswith("citations"):
            return _FakeResp(self.cites)
        raise AssertionError(f"unexpected url {url}")

    def test_full_loop_on_real_data(self):
        target = ("Attention mechanisms for neural machine translation and "
                  "sequence transduction; the Transformer architecture.")
        with patch.object(ps.s2, "search_papers", return_value=self.seed_raw), \
             patch.object(ps, "requests") as mock_req:
            mock_req.get.side_effect = self._router
            mock_req.exceptions = ps.requests.exceptions
            res = ps.snowball(target,
                              "attention transformer neural machine translation",
                              llm=None, max_depth=1, branching=5)

        self.assertEqual(res["status"], "ok")
        self.assertEqual(len(res["seeds"]), len(self.seed_raw))
        self.assertGreaterEqual(len(res["candidates"]), len(self.seed_raw))
        self.assertTrue(res["edges"], "expected real citation edges")
        # every candidate carries a provenance path back to a seed
        self.assertTrue(all(c["found_via"] for c in res["candidates"]))
        # candidates sorted by real SPECTER relevance, descending
        rels = [c["relevance"] for c in res["candidates"]]
        self.assertEqual(rels, sorted(rels, reverse=True))
        # relevance is a real cosine in [-1, 1]; on-topic seeds should score well
        self.assertGreater(rels[0], 0.5)
        # real titles flowed through (not ids)
        self.assertTrue(any(c["title"] for c in res["candidates"]))


if __name__ == "__main__":
    unittest.main()
