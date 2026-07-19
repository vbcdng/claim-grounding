"""Coverage citation stats (owner walkthrough 2026-07-07, todo item 6): a
source's '0 used' must not read as 'source useless'. The matcher now records
cited_by / citing_supported / supported per source, and the viewer's coverage
row explains WHY a bar is empty. No API calls.

Run:  venv/bin/python3 -m unittest tests.test_coverage_stats -v
"""
import os
import sys
import json
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.papertrail import matcher
from modules.papertrail.viewer import _coverage_status


def _fake_cosine(a, b, **kw):
    return [[0.8] * len(b) for _ in a]


def _sources():
    return {
        "pw": {"title": "Winner", "key": "w",
               "sentences": [{"text": "The bridge spans two kilometres.", "page": 1}],
               "claims": [{"text": "The bridge is two kilometres long.",
                           "evidence": ["The bridge spans two kilometres."]}]},
        "pc": {"title": "CoCited", "key": "c",
               "sentences": [{"text": "Weather at the site varies a lot.", "page": 1}],
               "claims": [{"text": "Weather varies.",
                           "evidence": ["Weather at the site varies a lot."]}]},
        "pu": {"title": "Unlucky", "key": "u",
               "sentences": [{"text": "Something else entirely.", "page": 1}],
               "claims": [{"text": "Another topic.",
                           "evidence": ["Something else entirely."]}]},
    }


class TestCoverageStats(unittest.TestCase):

    def test_winner_cocited_and_unsupported_sources_are_distinguished(self):
        claims = [
            {"id": "t1", "text": "The bridge is long.", "markers": ["w", "c"],
             "paper_ids": ["pw", "pc"]},                      # pw wins, pc co-cited
            {"id": "t2", "text": "A claim nothing backs.", "markers": ["u"],
             "paper_ids": ["pu"]},                            # unsupported
        ]

        def call(p, **kw):
            if "evidence finder" in p:
                return json.dumps({"sentences": []})
            if "bridge spans" in p:
                return json.dumps({"supported": True, "reason": "stated"})
            return json.dumps({"supported": False, "reason": "nope"})

        llm = MagicMock()
        llm.call.side_effect = call
        with patch.object(matcher.embeddings, "cosine_matrix", side_effect=_fake_cosine):
            analysis = matcher.run(claims, _sources(), llm, partial_check=False)
        per = analysis["coverage"]["per_source"]
        self.assertEqual((per["pw"]["cited_by"], per["pw"]["citing_supported"],
                          per["pw"]["supported"]), (1, 1, 1))
        self.assertEqual((per["pc"]["cited_by"], per["pc"]["citing_supported"],
                          per["pc"]["supported"]), (1, 1, 0))   # co-cited, lost
        self.assertEqual((per["pu"]["cited_by"], per["pu"]["citing_supported"],
                          per["pu"]["supported"]), (1, 0, 0))   # citing claim unsupported

    def test_status_labels(self):
        self.assertIn("backs 2 of your claims",
                      _coverage_status({"cited_by": 3, "citing_supported": 2, "supported": 2}))
        self.assertIn("co-cited sources",
                      _coverage_status({"cited_by": 1, "citing_supported": 1, "supported": 0}))
        self.assertIn("none judged supported",
                      _coverage_status({"cited_by": 2, "citing_supported": 0, "supported": 0}))
        self.assertEqual(_coverage_status({}), "")   # pre-stats analyses: no label


if __name__ == "__main__":
    unittest.main()
