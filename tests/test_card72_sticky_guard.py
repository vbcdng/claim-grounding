"""Card 72 round 1 (2026-09-28; gate day72 paper1 t47): once the quantifier
guard caught a claim's large-share phrase on a source's small-share sentence,
a later positive from the SAME source must show that group's large share
itself — the fulltext judge accepting an unquantified sentence ("these
countries also rely increasingly on food imports") no longer re-buys "Most
countries". No API calls.

Run:  venv/bin/python3 -m unittest tests.test_card72_sticky_guard
"""
import os
import sys
import json
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.papertrail import matcher  # noqa: E402
from tests.test_quantifier_guard import T47, SOME, _fake_cosine  # noqa: E402

PLAIN = ("Even though their populations tend to be predominantly rural and their "
         "economies agricultural, these countries also rely increasingly on food "
         "imports and spend a high proportion of their foreign exchange earnings "
         "to purchase them.")
# the stored q110 fulltext window (abridged): loose share words and a large
# percentage, none about the claim's group
PLAIN_WINDOW = (PLAIN + " During most of the 1990s and early 2000s, developing "
                "countries were net importers of agricultural products. Since 1990, "
                "least developed countries have spent between 50 and 80 percent of "
                "the foreign exchange earned from exports to import food.")
MOST = "Most developing countries are net importers of food."
HIT = {"claim_phrase": "Most countries cannot feed themselves from",
       "evidence_phrase": "some developing countries, the collapse of",
       "paper_ids": ["p1"]}


class TestStickyRule(unittest.TestCase):

    def test_t47_stored_positive_is_held(self):
        q = matcher._q_sticky_overreach(T47, HIT, "p1", [PLAIN], [PLAIN_WINDOW])
        self.assertIsNotNone(q)
        self.assertEqual(q["claim_phrase"], HIT["claim_phrase"])

    def test_loose_share_words_do_not_disarm(self):
        # the per-sentence rule would call 'predominantly' / '80 percent' a
        # large share; the sticky hold needs the claim's own group
        self.assertTrue(matcher._q_large_share_present(PLAIN_WINDOW, ["country"]))
        self.assertIsNotNone(
            matcher._q_sticky_overreach(T47, HIT, "p1", [PLAIN], [PLAIN_WINDOW]))

    def test_same_group_large_share_disarms(self):
        self.assertIsNone(matcher._q_sticky_overreach(T47, HIT, "p1", [MOST], [MOST]))
        w = PLAIN_WINDOW + " " + MOST
        self.assertIsNone(matcher._q_sticky_overreach(T47, HIT, "p1", [PLAIN], [w]))

    def test_needs_an_earlier_hit_on_that_source(self):
        self.assertIsNone(matcher._q_sticky_overreach(T47, {}, "p1", [PLAIN], [PLAIN_WINDOW]))
        self.assertIsNone(matcher._q_sticky_overreach(T47, HIT, "p2", [PLAIN], [PLAIN_WINDOW]))

    def test_unknown_claim_phrase_is_off(self):
        hit = dict(HIT, claim_phrase="a phrase the claim does not contain")
        self.assertIsNone(matcher._q_sticky_overreach(T47, hit, "p1", [PLAIN], [PLAIN_WINDOW]))


def _llm(extracted):
    """Every judge says supported; the cosine stage sees SOME (caught by the
    guard), the fulltext extraction returns `extracted`."""
    llm = MagicMock()
    llm.model = "fake/judge"

    def call(p, **kw):
        if "evidence finder" in p:
            return json.dumps({"sentences": [extracted]})
        return json.dumps({"supported": True, "reason": "judged supported"})

    llm.call.side_effect = call
    return llm


def _cos_some_first(a, b, **kw):
    """Only the 'some' sentence is a cosine candidate (the q110 t47 shape)."""
    return [[0.9 if SOME in t else 0.3 for t in b] for _ in a]


def _sources(extra):
    sents = [SOME, "Economic shocks cut the export earnings of poor states.", extra]
    return {"p1": {"title": "State of Agricultural Commodity Markets 2004",
                   "key": "fao2004", "sentences": [{"text": s} for s in sents],
                   "claims": []}}


def _claim():
    return {"id": "t47", "text": T47, "markers": ["fao2004"], "paper_ids": ["p1"]}


class TestStickyInRun(unittest.TestCase):

    def _run(self, extracted):
        with patch.object(matcher.embeddings, "cosine_matrix", side_effect=_cos_some_first):
            res = matcher.run([_claim()], _sources(extracted), _llm(extracted))
        return res["text_claims"][0]

    def test_unquantified_fulltext_positive_does_not_rebuy_most(self):
        c = self._run(PLAIN)
        self.assertEqual(c["verdict"], "unsupported")
        self.assertIn("quantifier guard", c["reason"])
        self.assertEqual(c["quantifier_guard"]["paper_ids"], ["p1"])

    def test_tail_suffix_inherits_the_hit(self):
        # the tail rescue re-judges "Most countries ... fed." alone; its own
        # cosine stage never sees the 'some' sentence, so without the
        # inherited hit the unquantified fulltext positive would rescue it
        def cos(a, b, **kw):
            return [[0.9 if (SOME in t and x.startswith("Economic")) else 0.3
                     for t in b] for x in a]
        with patch.object(matcher.embeddings, "cosine_matrix", side_effect=cos):
            res = matcher.run([_claim()], _sources(PLAIN), _llm(PLAIN))
        c = res["text_claims"][0]
        self.assertEqual(c["verdict"], "unsupported")
        self.assertEqual(c["tail_rescue"], {"supported": False, "tried": [1]})

    def test_source_stating_the_large_share_still_proves_it(self):
        c = self._run(MOST)
        self.assertEqual(c["verdict"], "supported")


if __name__ == "__main__":
    unittest.main()
