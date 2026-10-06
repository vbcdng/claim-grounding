"""Quantifier guard (task #75, 2026-09-03; gate row paper1 t47): a positive
bought by a sentence that gives a group a SMALLER share than the claim
('For some developing countries...' vs 'Most countries...') is refused on
every acceptance path, and the arbiter rescue never re-buys it. No API calls.

Run:  venv/bin/python3 -m unittest tests.test_quantifier_guard -v
"""
import os
import sys
import json
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.papertrail import matcher, arbiter  # noqa: E402

T47 = ("Economic displacement of this kind is not merely impoverishment; it can "
       "become a threat to life. Most countries cannot feed themselves from their "
       "own farms — the great majority of developing states, and nearly all the "
       "poorest, are net importers of food — so a state stripped of its export "
       "earnings and the value of its currency loses, with them, the means to "
       "keep its population fed.")
SOME = ("For some developing countries, the collapse of commodity prices was "
        "traumatic, triggering rising rural unemployment and a steep decline in "
        "export earnings.")
WINDOW = ("most agricultural commodities are projected to rise above current "
          "levels, they would still remain below their mid-1990s peaks. " + SOME +
          " Lower income from exports has jeopardized their ability to pay for "
          "food imports, particularly in countries where food import bills "
          "account for a high share of the GDP.")


class TestOverreach(unittest.TestCase):

    def test_t47_fires(self):
        q = matcher._quantifier_overreach(T47, [SOME], [WINDOW])
        self.assertIsNotNone(q)
        self.assertTrue(q["claim_phrase"].lower().startswith("most countries"))
        self.assertIn("some developing countries", q["evidence_phrase"].lower())

    def test_large_share_in_evidence_disarms(self):
        s = "Most developing countries are net importers of food."
        self.assertIsNone(matcher._quantifier_overreach(T47, [s], [s]))
        s2 = "Some 70% of developing countries import more food than they export."
        self.assertIsNone(matcher._quantifier_overreach(T47, [s2], [s2]))
        s3 = "Developing countries are generally net food importers; some export."
        self.assertIsNone(matcher._quantifier_overreach(T47, [s3], [s3]))

    def test_large_share_in_window_disarms(self):
        w = "Most developing countries are net food importers. " + SOME
        self.assertIsNone(matcher._quantifier_overreach(T47, [SOME], [w]))

    def test_other_group_in_window_does_not_disarm(self):
        # the real t47 window quantifies 'agricultural commodities', not countries
        self.assertIsNotNone(matcher._quantifier_overreach(T47, [SOME], [WINDOW]))

    def test_different_noun_never_fires(self):
        s = "Many LDCs are net food importers."   # 'LDCs' is not 'countries'
        self.assertIsNone(matcher._quantifier_overreach(T47, [s], [s]))

    def test_claim_without_large_share_is_off(self):
        c = "Some countries cannot feed themselves from their own farms."
        self.assertIsNone(matcher._quantifier_overreach(c, [SOME], [WINDOW]))

    def test_superlative_and_adverb_most_are_not_shares(self):
        c1 = "The most vulnerable countries spend more of their earnings on food."
        c2 = "Countries most likely to suffer are the poorest ones."
        c3 = "At most a dozen countries are net food exporters."
        for c in (c1, c2, c3):
            self.assertIsNone(matcher._quantifier_overreach(c, [SOME], [SOME]), c)

    def test_many_versus_most_same_group_fires(self):
        c = "Most countries in the region are net food importers."
        s = "Many countries in the region import more food than they grow."
        self.assertIsNotNone(matcher._quantifier_overreach(c, [s], [s]))

    def test_no_evidence_text_is_off(self):
        self.assertIsNone(matcher._quantifier_overreach(T47, [None], [None]))


def _fake_cosine(a, b, **kw):
    return [[0.5] * len(b) for _ in a]


def _llm():
    """Every judge says supported; extraction returns the 'some' sentence.
    Without the guard this claim would be supported at the cosine stage."""
    llm = MagicMock()
    llm.model = "fake/judge"

    def call(p, **kw):
        if "evidence finder" in p:
            return json.dumps({"sentences": [SOME]})
        return json.dumps({"supported": True, "reason": "judged supported"})

    llm.call.side_effect = call
    return llm


def _sources():
    return {"p1": {"title": "State of Agricultural Commodity Markets 2004",
                   "key": "fao2004",
                   "sentences": [{"text": s} for s in WINDOW.split(". ")],
                   "claims": []}}


def _claim(text=T47):
    return {"id": "t47", "text": text, "markers": ["fao2004"], "paper_ids": ["p1"]}


class TestGuardInRun(unittest.TestCase):

    def test_some_never_proves_most(self):
        with patch.object(matcher.embeddings, "cosine_matrix", side_effect=_fake_cosine):
            res = matcher.run([_claim()], _sources(), _llm())
        c = res["text_claims"][0]
        self.assertEqual(c["verdict"], "unsupported")
        self.assertIn("quantifier guard", c["reason"])
        self.assertIn("some developing countries", c["reason"].lower())
        self.assertEqual(c["quantifier_guard"]["paper_ids"], ["p1"])
        # the card still shows what the judge read
        self.assertTrue(any(e.get("sentence") for e in c["evidences"]))

    def test_claim_with_matching_share_is_untouched(self):
        # control: the same machinery, a claim that says 'some' too
        text = ("For some developing countries the collapse of commodity prices "
                "was traumatic and cut export earnings steeply.")
        with patch.object(matcher.embeddings, "cosine_matrix", side_effect=_fake_cosine):
            res = matcher.run([_claim(text)], _sources(), _llm())
        c = res["text_claims"][0]
        self.assertEqual(c["verdict"], "supported")
        self.assertNotIn("quantifier_guard", c)


class TestGuardOnArbiterRescue(unittest.TestCase):

    def test_rescue_never_rebuys_an_overreaching_proof(self):
        c = _claim()
        c["verdict"] = "unsupported"
        c["evidences"] = [{"paper_id": "p1", "sentence": SOME, "supported": False,
                           "source_title": "fao"}]
        c["quantifier_guard"] = {"claim_phrase": "Most countries",
                                 "evidence_phrase": "some developing countries",
                                 "paper_ids": ["p1"]}
        c["arbiter"] = {"model": "fake/arbiter", "prompt_sha": "x",
                        "trigger": "unsupported",
                        "action": "wrong_or_insufficient_evidence",
                        "missing_subclaim": "", "rewrite_suggestion": "",
                        "proofs": [SOME], "quotes_dropped": 0,
                        "conflict": None, "why": "w"}
        llm = MagicMock()
        llm.model = "fake/judge"
        llm.call.return_value = json.dumps({"supported": True, "reason": "r"})
        s = arbiter.rescue([c], _sources(), llm, workers=1)
        self.assertEqual(s["flipped"], [])
        self.assertEqual(s["held"], ["t47"])
        llm.call.assert_not_called()


if __name__ == "__main__":
    unittest.main()


class TestSharedGroupNotSharedVerb(unittest.TestCase):
    """Review 2026-09-04, second check: a shared verb is not a shared group."""

    def test_different_group_nouns_do_not_fire(self):
        self.assertIsNone(matcher._quantifier_overreach(
            "Most hospitals reported drug shortages.",
            ["Some clinics reported drug shortages."], [None]))

    def test_same_group_noun_still_fires(self):
        self.assertIsNotNone(matcher._quantifier_overreach(
            "Most countries cannot feed themselves from their own farms.",
            ["For some developing countries, the collapse of commodity prices was traumatic."], [None]))

    def test_most_of_the_is_a_share_phrase(self):
        self.assertIsNotNone(matcher._quantifier_overreach(
            "Most of the countries cannot feed themselves.",
            ["Some countries cannot feed themselves."], [None]))
        self.assertIsNone(matcher._quantifier_overreach(
            "They made the most of the harvest.", ["Some countries export grain."], [None]))


class TestSyntheticSetFindings(unittest.TestCase):
    """2026-09-08: rules added after the 1,387-row synthetic set
    (benchmarks/quantifier_guard_synthetic/) found two defects and one
    leftover of the shared-verb problem."""

    C = "Most countries import more food than they export."

    def test_nearly_half_and_less_than_half_are_small(self):
        for s in ("Nearly half of countries import more food than they export.",
                  "Less than half of the countries import more food than they export."):
            self.assertIsNotNone(matcher._quantifier_overreach(self.C, [s], [s]), s)

    def test_more_than_half_and_over_half_are_large(self):
        for s in ("More than half of countries import more food than they export.",
                  "Over half of the countries import more food than they export.",
                  "About half of countries import more food than they export."):
            self.assertIsNone(matcher._quantifier_overreach(self.C, [s], [s]), s)

    def test_clause_final_adverb_most_is_not_a_share(self):
        s = "Some countries import more food than they export."
        for c in ("What matters most is that countries import more food than they export.",
                  "The countries that matter most import more food than they export.",
                  "Food imports hit them most in countries that export little."):
            self.assertIsNone(matcher._quantifier_overreach(c, [s], [s]), c)

    def test_pronoun_after_quantifier_names_no_group(self):
        # 'some of these reported ...' shares only the verb with the claim
        self.assertIsNone(matcher._quantifier_overreach(
            "Most hospitals reported drug shortages.",
            ["Some of these reported drug shortages."], [None]))

    def test_percentages_and_fractions_below_half_are_small(self):
        for s in ("Only 18% of countries import more food than they export.",
                  "One in five countries import more food than they export.",
                  "A quarter of countries import more food than they export."):
            self.assertIsNotNone(matcher._quantifier_overreach(self.C, [s], [s]), s)
        # a percentage that is not a share OF the group must not count
        s = "Food prices rose 12% for countries that import more than they export."
        self.assertIsNone(matcher._quantifier_overreach(self.C, [s], [s]))

    def test_all_and_every_and_percent_on_the_claim_side(self):
        s = "Some countries import more food than they export."
        for c in ("All countries import more food than they export.",
                  "All developing countries import more food than they export.",
                  "Every country imports more food than it exports.",
                  "About 80% of countries import more food than they export.",
                  "9 out of 10 countries import more food than they export."):
            self.assertIsNotNone(matcher._quantifier_overreach(c, [s], [s]), c)
        for c in ("Not all countries import more food than they export.",
                  "Countries do not import food at all.",
                  "After all, countries import food."):
            self.assertIsNone(matcher._quantifier_overreach(c, [s], [s]), c)

