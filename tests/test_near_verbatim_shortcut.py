"""Card 133: the cosine >= AUTO_SUPPORT shortcut may accept a claim without a
judge call only when the claim's words ARE the source sentence's words.
Cases are real pairs from benchmarks/card133_shortcut_sample.jsonl."""
import json
import unittest
from unittest.mock import MagicMock

from modules.papertrail import matcher


def _src(*texts):
    return {"title": "S", "sentences": [{"text": t, "page": 1} for t in texts]}


class TestNearVerbatimRule(unittest.TestCase):
    def test_eggs_t36_aside_is_not_near_verbatim(self):
        ok, _ = matcher._near_verbatim_ok(
            "A further meta-analysis reached compatible conclusions but is paywalled",
            "Our meta-analysis have several limitations.")
        self.assertFalse(ok)

    def test_identical_sentence_passes(self):
        s = ("On their first trial, 7/16 chimpanzees balanced both platforms in the "
             "control condition and 8/16 did so in the test condition.")
        self.assertTrue(matcher._near_verbatim_ok(s, s)[0])

    def test_reference_markers_and_pdf_hyphen_ignored(self):
        # nv006 / nv009: the claim text has its [[key]] markers removed; the
        # source prints "[ 6,8]" and a line-break hyphen "chim- panzees".
        claim = ("In our experiments with 14 primates (five chimpanzees across two "
                 "studies , five orangutans in one study ), we found that three "
                 "chimpanzees spontaneously covered both exits.")
        sent = ("In our experiments with 14 primates (five chimpanzees across two "
                "studies [ 6,8], five orangutans in one study [ 6]), we found that "
                "three chim- panzees spontaneously covered both exits.")
        self.assertTrue(matcher._near_verbatim_ok(claim, sent)[0])
        self.assertTrue(matcher._near_verbatim_ok(
            "Across our studies, we have found little evidence in this simple context .",
            "Across our studies, we have found little evidence in this simple context [6,8,9 ].")[0])

    def test_changed_figure_fails(self):
        # nv033: a planted wrong figure at cosine 0.995
        self.assertFalse(matcher._near_verbatim_ok(
            "Combining these indicators with alternative thresholds generated 37,457 "
            "perfect recession classifiers.",
            "We then combine these indicators with alternative thresholds to generate "
            "137,457 perfect recession classifiers.")[0])

    def test_dropped_lead_in_fails(self):
        # nv023: the source only REPORTS the claim
        self.assertFalse(matcher._near_verbatim_ok(
            "Viktor Halm won the provincial design prize of 1799.",
            "Early newspaper reports stated that Viktor Halm won the provincial "
            "design prize of 1799.")[0])

    def test_empty_never_passes(self):
        self.assertFalse(matcher._near_verbatim_ok("", "")[0])
        self.assertFalse(matcher._near_verbatim_ok("[3]", "[4]")[0])


class TestJudgeSourceShortcut(unittest.TestCase):
    def test_eggs_t36_goes_to_the_judge(self):
        # The last-sentence rescue re-evaluates only the tail; cosine 0.9768.
        src = _src("Several cohorts were included.",
                   "Our meta-analysis have several limitations.",
                   "First, residual confounding cannot be excluded.")
        llm = MagicMock()
        llm.call.return_value = json.dumps({"supported": False,
                                            "reason": "passage says nothing about another meta-analysis"})
        e = matcher._judge_source(
            "A further meta-analysis reached compatible conclusions but is paywalled",
            "p1", src, [0.60, 0.9768, 0.50], llm, "{CLAIM}{PASSAGE}")
        self.assertTrue(llm.call.called)
        self.assertFalse(e["supported"])
        self.assertNotIn("near-verbatim", e["reason"])

    def test_right_verbatim_acceptance_still_free(self):
        # nv005: identical sentence, cosine 1.0 — still accepted with no call.
        s = ("On their first trial, 7/16 chimpanzees balanced both platforms in the "
             "control condition and 8/16 did so in the test condition.")
        src = _src(s, "The difference was not significant.")
        llm = MagicMock()
        e = matcher._judge_source(s, "p1", src, [1.0, 0.3], llm, "{CLAIM}{PASSAGE}")
        self.assertFalse(llm.call.called)
        self.assertTrue(e["supported"])
        self.assertIn("near-verbatim", e["reason"])

    def test_right_paraphrase_now_costs_one_call(self):
        # nv014: a right acceptance at 0.9729 that is not word-identical is now
        # judged once; the judge's yes still supports it.
        src = _src("The victory secured him a two-year card on the World Snooker Tour.")
        llm = MagicMock()
        llm.call.return_value = json.dumps({"supported": True, "reason": "same fact"})
        e = matcher._judge_source("This victory earned him a two-year World Snooker Tour card.",
                                  "p1", src, [0.9729], llm, "{CLAIM}{PASSAGE}")
        self.assertEqual(llm.call.call_count, 1)
        self.assertTrue(e["supported"])


if __name__ == "__main__":
    unittest.main()
