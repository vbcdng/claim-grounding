"""Agent-walkthrough fixes (owner todo items 15-19, 2026-07-07): quoted-span
retrieval probe, reference-fragment evidence gate, broadened missing-component
verbs, over-cite attribution skip, and missing-source rows. Core support-decision
quality. No API calls (LLM mocked).

Run:  venv/bin/python3 -m unittest tests.test_walkthrough_fixes -v
"""
import os
import sys
import json
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.papertrail import matcher, viewer


def _fake_cosine_low(a, b, **kw):
    # everything on-topic-ish but below AUTO_SUPPORT and, for the quote test,
    # BELOW OFFTOPIC for the verbatim sentence so only the quote probe finds it
    return [[0.5] * len(b) for _ in a]


class TestReferenceFragmentGate(unittest.TestCase):
    def test_reference_fragments_detected_and_prose_kept(self):
        self.assertTrue(matcher._is_reference_fragment("Review of Economic Studies."))
        self.assertTrue(matcher._is_reference_fragment("Economics Letters"))
        self.assertTrue(matcher._is_reference_fragment("Journal of Comparative Economics"))
        self.assertFalse(matcher._is_reference_fragment(
            "Egg consumption raised LDL cholesterol modestly in the trial."))
        self.assertFalse(matcher._is_reference_fragment("The US hosts most GPU performance."))

    def test_unusable_evidence_combines_degenerate_and_reference(self):
        self.assertTrue(matcher._unusable_evidence("."))
        self.assertTrue(matcher._unusable_evidence("Review of Economic Studies."))
        self.assertFalse(matcher._unusable_evidence("A real supporting sentence here."))


class TestCitationHeaderGate(unittest.TestCase):
    """A DOI / reference-header line PDF extraction glued into a body 'sentence'
    (owner walkthrough t20, qin2018) must never be judged or shown as evidence —
    but real statistical prose must survive (a superscript-author heuristic was
    tried and dropped because it ate 'HR, 1.18'-style evidence)."""

    def test_doi_and_volpage_headers_flagged(self):
        self.assertTrue(matcher._is_citation_header(
            "al. Heart 2018;104:1756-1763. doi:10.1136/heartjnl-2017-312651Original research"))
        self.assertTrue(matcher._is_citation_header("N Engl J Med 2013;368:1575:1584."))
        self.assertTrue(matcher._is_citation_header("Available at https://doi.org/10.1001/jama.2019.1572"))

    def test_statistical_prose_survives(self):
        for s in ["The hazard ratio was 1.05 (95% CI 1.02-1.08) for each extra egg per day.",
                  "During a median follow-up of 17.5 years (range, 13.0-21.7), events occurred.",
                  "Higher intake raised LDL cholesterol (P < 0.001).",
                  "In 2018, the cohort enrolled 500,000 adults."]:
            self.assertFalse(matcher._is_citation_header(s), s)
            self.assertFalse(matcher._unusable_evidence(s), s)

    def test_unusable_evidence_includes_citation_header(self):
        self.assertTrue(matcher._unusable_evidence("Circulation 2020;141:e39:doi:10.1161/CIR.0"))

    def test_cite_request_boilerplate_flagged(self):
        # owner walkthrough t12 (carson2020): a "how to cite" instruction glued in
        self.assertTrue(matcher._is_citation_header(
            "The American Heart Association requests that this document be cited as "
            "follows: Carson JAS, Lichtenstein AH, Anderson CAM"))
        self.assertTrue(matcher._is_citation_header("Please cite this article as: Smith 2020."))
        # real prose that merely uses the word "cited" must survive
        self.assertFalse(matcher._is_citation_header(
            "Studies cited a strong association between eggs and CVD in this cohort."))


class TestQuotedSpanProbe(unittest.TestCase):
    def test_spans_extracted_straight_and_curly(self):
        self.assertEqual(matcher._quoted_spans('China is "a peer competitor in AI".'),
                         ["a peer competitor in AI"])
        self.assertEqual(matcher._quoted_spans('the “next great divergence” in growth'),
                         ["next great divergence"])
        self.assertEqual(matcher._quoted_spans('it is "safe" to eat'), [])   # too short

    def test_verbatim_quote_is_judged_and_can_flip_to_supported(self):
        # cosine buries the verbatim sentence (all 0.5, below OFFTOPIC 0.55) so the
        # ONLY way it reaches the judge is the quoted-span probe.
        claim = ('China is "a peer competitor in AI", the report says.')
        sents = [{"text": "The essay opens with a broad survey of the field.", "page": 1},
                 {"text": "Ultimately, China is a peer competitor in AI.", "page": 2}]
        src = {"title": "Williams", "key": "williams2025", "sentences": sents, "claims": []}
        llm = MagicMock()
        # judge says supported ONLY when the verbatim sentence is in the passage
        llm.call.side_effect = lambda p, **k: json.dumps(
            {"supported": "peer competitor in AI" in p, "reason": "verbatim" if "peer competitor" in p else "no"})
        row = [0.5, 0.5]
        prompt = "{CLAIM} {PASSAGE}"
        e = matcher._judge_source(claim, "w", src, row, llm, prompt)
        self.assertTrue(e["supported"])
        self.assertIn("peer competitor in AI", e["sentence"])

    def test_negated_quote_is_not_auto_accepted(self):
        # the quote is verbatim present but in negation -> the judge (seeing the
        # window) rejects, and the probe must NOT auto-flip.
        claim = 'The author claims eggs are "perfectly safe for everyone".'
        sents = [{"text": "It is a myth that eggs are perfectly safe for everyone.", "page": 1}]
        src = {"title": "X", "key": "x2020", "sentences": sents, "claims": []}
        llm = MagicMock()
        llm.call.side_effect = lambda p, **k: json.dumps(
            {"supported": False, "reason": "the passage negates the claim"})
        e = matcher._judge_source(claim, "x", src, [0.5], llm, "{CLAIM}{PASSAGE}")
        self.assertFalse(e["supported"])


class TestMissingComponentVerbs(unittest.TestCase):
    def test_establish_and_kin_now_parse(self):
        for verb in ("establish", "demonstrate", "show", "confirm", "indicate", "prove"):
            r = f"the passage does not {verb} that the effect on votes was measured"
            self.assertTrue(matcher._missing_components(r),
                            f"verb '{verb}' should yield a component")


class TestOverciteAttributionSkip(unittest.TestCase):
    def test_named_source_is_not_overcite_probed(self):
        self.assertTrue(matcher._claim_names_source("as noted by Drago and Laine",
                                                    {"key": "drago2025"}))
        self.assertFalse(matcher._claim_names_source("a claim with no attribution",
                                                     {"key": "drago2025"}))


class TestMissingSourceRow(unittest.TestCase):
    def _render(self, claim):
        analysis = {"text_claims": [claim],
                    "sources": [{"paper_id": "p1", "key": "a", "filename": "a.txt",
                                 "title": "Alpha"}],
                    "coverage": {"totals": {"claims": 1, "supported": 1, "unsupported": 0,
                                            "own": 0, "omitted": 0}},
                    "metadata": {}, "omitted": []}
        out = os.path.join(tempfile.mkdtemp(), "v.html")
        viewer.generate(analysis, out)
        return open(out, encoding="utf-8").read()

    def test_missing_marker_renders_a_row(self):
        e = {"paper_id": "p1", "source_title": "Alpha", "supported": True,
             "sentence": "Present source backs it.", "page": 1, "snippet": "Present"}
        claim = {"id": "t14", "text": "A multi-cite claim.", "markers": ["a", "b"],
                 "paper_ids": ["p1"], "verdict": "supported", "method": "llm",
                 "reason": "ok", "evidence": e, "evidences": [e],
                 "missing_markers": [{"key": "pomfret2019", "filename": "pomfret2019.pdf"}]}
        page = self._render(claim)
        self.assertIn("source file missing", page)
        self.assertIn("pomfret2019.pdf", page)
        self.assertIn("[[pomfret2019]]", page)


if __name__ == "__main__":
    unittest.main()
