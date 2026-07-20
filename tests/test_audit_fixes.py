"""Tests for the paper1-audit fixes: degenerate-evidence filtering in the matcher
and content validation of downloaded/ingested files. No API, no network.

Run:  venv/bin/python3 -m unittest tests.test_audit_fixes -v
"""

import os
import sys
import json
import tempfile
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.papertrail import matcher
from modules.papertrail import direct_downloader as dd


class TestDegenerateFilters(unittest.TestCase):
    def test_degenerate_detection(self):
        self.assertTrue(matcher._degenerate("."))
        self.assertTrue(matcher._degenerate("1 2 3"))
        self.assertTrue(matcher._degenerate(""))
        self.assertFalse(matcher._degenerate("An ultraintelligent machine could design even better machines."))

    def test_claim_echo_detection(self):
        claim = "Venezuela's oil revenues fell by more than nine-tenths after 2014."
        self.assertTrue(matcher._is_claim_echo(claim, claim))
        self.assertTrue(matcher._is_claim_echo("Venezuela's oil revenues fell by more than nine-tenths", claim))
        self.assertFalse(matcher._is_claim_echo(
            "Oil exports, previously 95% of export revenue, fell by 93% between 2012 and 2020.", claim))

    def test_parse_sentences_drops_fragments(self):
        raw = '{"sentences": [".", "x", "A real supporting sentence with content."]}'
        self.assertEqual(matcher._parse_sentences(raw),
                         ["A real supporting sentence with content."])

    def test_extract_evidence_rejects_fragment_and_echo(self):
        claim = "The collapse was fast and severe across the whole economy."
        src = {"title": "S", "sentences": [
            {"text": "Completely unrelated sentence about fisheries and aquaculture.", "page": 1}]}
        llm = MagicMock()
        # extraction returns a fragment + an unmapped claim echo -> nothing usable
        llm.call.return_value = json.dumps(
            {"sentences": [".", "The collapse was fast and severe across the whole economy."]})
        e = matcher._extract_evidence(claim, "p1", src, llm, "{CLAIM}{SOURCE}", "{CLAIM}{PASSAGE}")
        self.assertFalse(e["supported"])
        self.assertIn("no usable source sentence", e["reason"])

    def test_extract_evidence_keeps_real_verbatim_quote(self):
        # a verbatim quote of a REAL source sentence maps to the index and must pass
        sentence = "Oil exports fell by 93 percent between 2012 and 2020 in Venezuela."
        src = {"title": "S", "sentences": [{"text": sentence, "page": 4}]}
        llm = MagicMock()
        llm.call.side_effect = [
            json.dumps({"sentences": [sentence]}),                         # extraction
            json.dumps({"supported": True, "reason": "stated directly"}),  # judgment vote 1
            json.dumps({"supported": True, "reason": "stated directly"}),  # judgment vote 2
        ]
        e = matcher._extract_evidence("Venezuelan oil exports collapsed.", "p1", src, llm,
                                      "{CLAIM}{SOURCE}", "{CLAIM}{PASSAGE}")
        self.assertTrue(e["supported"])
        self.assertEqual(e["page"], 4)

    def test_extract_evidence_rejects_fused_claim_tail(self):
        # BUG-2 (essay t9): the extractor returns a REAL source sentence with the
        # claim's own tail concatenated onto it. It is not a whole-string echo
        # (ratio < 0.85, not a substring of the claim) and maps to no index, so
        # the old guards let it through and the judge self-proved. The
        # source-membership gate must drop it: its words are not all in the source.
        real = "There is no evidence that extrapolation applies to AI."
        claim = ("Superforecasting leans on outside-view base rates. "
                 "AI capability growth, by contrast, is discontinuous and "
                 "compute-driven, leaving no comparable historical baseline.")
        fused = real + " AI capability growth, by contrast, is discontinuous and " \
                       "compute-driven, leaving no comparable historical baseline."
        src = {"title": "S", "sentences": [
            {"text": real, "page": 1},
            {"text": "An unrelated sentence about forecasting horizons.", "page": 1}]}
        llm = MagicMock()
        llm.call.return_value = json.dumps({"sentences": [fused]})
        e = matcher._extract_evidence(claim, "p1", src, llm, "{CLAIM}{SOURCE}", "{CLAIM}{PASSAGE}")
        self.assertFalse(e["supported"])
        self.assertNotIn("comparable historical baseline", e.get("window") or "")

    def test_extract_evidence_keeps_verbatim_with_punctuation_drift(self):
        # false-alarm control for the membership gate: a real quote that differs
        # from the stored sentence only by punctuation/curly-quote drift and maps
        # to no index must still survive (its words ARE in the source in order).
        stored = "The bank's assets rose 12 percent, the report said."
        quote = "the bank’s assets rose 12 percent the report said"  # curly + no punct
        src = {"title": "S", "sentences": [{"text": stored, "page": 2}]}
        llm = MagicMock()
        llm.call.side_effect = [
            json.dumps({"sentences": [quote]}),
            json.dumps({"supported": True, "reason": "stated"}),
            json.dumps({"supported": True, "reason": "stated"}),
        ]
        e = matcher._extract_evidence("Assets rose.", "p1", src, llm,
                                      "{CLAIM}{SOURCE}", "{CLAIM}{PASSAGE}")
        self.assertTrue(e["supported"])

    def test_extract_evidence_keeps_honest_condensation(self):
        # paper1 t27 regression (2026-07-17 gate run): the extractor CONDENSES a
        # long source sentence (drops the CI parenthetical), so it neither maps
        # (Jaccard < 0.6) nor matches verbatim — but it carries no claim wording
        # the source lacks. The strict membership gate starved the tail_rescue
        # judge of this honest quote and flipped a true supported to unsupported.
        stored = ("We estimate that the odds of greater agreement is +81.2% "
                  "(95% confidence interval (CI) [+26.0%, +160.7%], P < 0.01, "
                  "adjusted for topic, demographic strata, prior attitude, "
                  "debate order, and judge identity) higher in the personalized "
                  "condition.")
        condensed = ("We estimate that the odds of greater agreement is +81.2% "
                     "higher in the personalized condition.")
        src = {"title": "S", "sentences": [
            {"text": stored, "page": 3},
            {"text": "An unrelated methods sentence about recruitment.", "page": 3}]}
        llm = MagicMock()
        llm.call.side_effect = [
            json.dumps({"sentences": [condensed]}),
            json.dumps({"supported": True, "reason": "stated"}),
            json.dumps({"supported": True, "reason": "stated"}),
        ]
        e = matcher._extract_evidence(
            "Personalization raised the odds of agreement by about 80 percent.",
            "p1", src, llm, "{CLAIM}{SOURCE}", "{CLAIM}{PASSAGE}")
        self.assertTrue(e["supported"])

    def test_extract_evidence_keeps_quote_of_garbled_source(self):
        # spacing-garbled PDFs (glued/letter-spaced/hyphen-split words): an honest
        # quote matches the source only at the character-stream level. It must
        # survive the membership gate via the _charstream fast path.
        stored = "Thee ffectof pers onalizedde bate persuasion wasla rgeand robust."
        quote = "The effect of personalized debate persuasion was large and robust."
        src = {"title": "S", "sentences": [{"text": stored, "page": 1}]}
        llm = MagicMock()
        llm.call.side_effect = [
            json.dumps({"sentences": [quote]}),
            json.dumps({"supported": True, "reason": "stated"}),
            json.dumps({"supported": True, "reason": "stated"}),
        ]
        e = matcher._extract_evidence("Debate persuasion effects were robust.",
                                      "p1", src, llm, "{CLAIM}{SOURCE}", "{CLAIM}{PASSAGE}")
        self.assertTrue(e["supported"])

    def test_auto_support_skipped_when_window_retracts(self):
        # BUG-1 (synth prizerec): a >=0.97 cosine match to a sentence the window
        # walks back must NOT auto-accept; it must fall through to the judge.
        matched = "Early reports stated that Viktor Halm won the 1799 design prize."
        src = {"title": "S", "sentences": [
            {"text": matched, "page": 1},
            {"text": "The committee minutes show the award in fact went to Annelie Kron, "
                     "and the earlier reports were retracted.", "page": 1}]}
        llm = MagicMock()
        llm.call.return_value = json.dumps({"supported": False, "reason": "contradicted by retraction"})
        e = matcher._judge_source("Viktor Halm won the 1799 design prize.", "p1", src,
                                  [0.99, 0.10], llm, "{CLAIM}{PASSAGE}")
        self.assertTrue(llm.call.called)          # judge WAS consulted (no auto-accept)
        self.assertFalse(e["supported"])

    def test_auto_support_still_fires_without_contradiction(self):
        # false-alarm control for BUG-1: an ordinary >=0.97 match with a clean
        # window still auto-accepts with no LLM call.
        matched = "The United States hosts about three quarters of global compute."
        src = {"title": "S", "sentences": [
            {"text": matched, "page": 1},
            {"text": "China is second with roughly fourteen percent.", "page": 1}]}
        llm = MagicMock()
        e = matcher._judge_source("The US hosts about three quarters of global compute.",
                                  "p1", src, [0.99, 0.10], llm, "{CLAIM}{PASSAGE}")
        self.assertFalse(llm.call.called)         # auto-accepted, no judge call
        self.assertTrue(e["supported"])
        self.assertIn("near-verbatim", e["reason"])

    def test_judgment_prompt_carries_source_provenance(self):
        src = {"title": "The Next Great Divergence — UNDP", "sentences": [
            {"text": "AI may spark a next great divergence between countries.", "page": 3}]}
        llm = MagicMock()
        llm.call.return_value = json.dumps({"supported": True, "reason": "ok"})
        matcher._judge_source("claim", "p1", src, [0.8], llm, "{CLAIM}||{PASSAGE}")
        prompt_sent = llm.call.call_args[0][0]
        self.assertIn("From The Next Great Divergence — UNDP:", prompt_sent)

    def test_judge_source_skips_degenerate_candidates(self):
        src = {"title": "S", "sentences": [
            {"text": ".", "page": 1},
            {"text": "A meaningful candidate sentence about the actual topic.", "page": 2}]}
        llm = MagicMock()
        llm.call.return_value = json.dumps({"supported": True, "reason": "ok"})
        # "." has the higher cosine but must be skipped
        e = matcher._judge_source("claim text", "p1", src, [0.99, 0.80], llm, "{CLAIM}{PASSAGE}")
        self.assertEqual(e["page"], 2)
        self.assertTrue(e["supported"])


class TestChunkedExtraction(unittest.TestCase):
    def test_numeric_table_fragment_is_not_degenerate(self):
        # audit t6: "EU, 4.8% 4." is a real table row = real evidence
        self.assertFalse(matcher._degenerate("EU, 4.8% 4."))
        self.assertTrue(matcher._degenerate("1 2 3"))       # < 8 chars stays junk
        self.assertTrue(matcher._degenerate("."))

    def _long_source(self, needle, n=300):
        sents = [{"text": f"Filler sentence number {i} about something unrelated entirely.",
                  "page": 1 + i // 50} for i in range(n)]
        sents[250] = {"text": needle, "page": 6}
        return {"title": "Long Doc", "sentences": sents}

    def test_gated_chunks_reach_a_deep_needle(self):
        needle = "Oil revenues declined by more than nine-tenths after the year 2014."
        src = self._long_source(needle)
        row = [0.1] * 300
        row[250] = 0.9                       # cosine points at the needle sentence
        llm = MagicMock()
        llm.call.side_effect = lambda p, **kw: (
            json.dumps({"supported": True, "reason": "stated"}) if p.startswith("JG")
            else json.dumps({"sentences": [needle]}) if needle in p
            else json.dumps({"sentences": []}))
        e = matcher._extract_evidence("The claim.", "p1", src, llm,
                                      "EX {CLAIM} {SOURCE}", "JG {CLAIM} {PASSAGE}", row=row)
        self.assertEqual(e["page"], 6)
        self.assertIn(needle, e["window"])
        # gating: <= TOP_CHUNKS extraction calls + 1 judgment, not all ~30 chunks
        self.assertLessEqual(llm.call.call_count, matcher.EXTRACT_TOP_CHUNKS + 1)

    def test_retry_on_empty_extraction(self):
        src = {"title": "S", "sentences": [
            {"text": "A single meaningful sentence about the actual topic here.", "page": 1}]}
        llm = MagicMock()
        llm.call.side_effect = [
            json.dumps({"sentences": []}),   # first pass: empty (flaky)
            json.dumps({"sentences": ["A single meaningful sentence about the actual topic here."]}),
            json.dumps({"supported": True, "reason": "ok"}),   # judgment vote 1
            json.dumps({"supported": True, "reason": "ok"}),   # judgment vote 2
        ]
        e = matcher._extract_evidence("claim", "p1", src, llm,
                                      "EX {CLAIM} {SOURCE}", "JG {CLAIM} {PASSAGE}")
        self.assertTrue(e["supported"])
        self.assertEqual(llm.call.call_count, 4)

    def test_judge_sees_window_around_fragment(self):
        src = {"title": "T", "sentences": [
            {"text": "Performance share of leading AI supercomputers by country.", "page": 2},
            {"text": "USA, 74.5% 2.", "page": 2},
            {"text": "China, 14.1% 3.", "page": 2},
        ]}
        llm = MagicMock()
        llm.call.side_effect = [
            json.dumps({"sentences": ["USA, 74.5% 2."]}),
            json.dumps({"supported": True, "reason": "ok"}),   # judgment vote 1
            json.dumps({"supported": True, "reason": "ok"}),   # judgment vote 2
        ]
        e = matcher._extract_evidence("USA hosts about three quarters.", "p1", src, llm,
                                      "EX {CLAIM} {SOURCE}", "JG {CLAIM} {PASSAGE}")
        judge_prompt_sent = llm.call.call_args[0][0]
        # the ±1 window puts the table header and neighbor row in front of the judge
        self.assertIn("Performance share", judge_prompt_sent)
        self.assertIn("China, 14.1%", judge_prompt_sent)
        self.assertTrue(e["supported"])

    def test_pooled_hits_ranked_by_cosine(self):
        # audit t28/t49/t68: chunks emit hits in document order, so an early weak
        # hit used to become the primary sentence over the actual best match.
        weak = "An early loosely related sentence about the general topic area here."
        strong = "The precise statistic the claim cites appears verbatim right here."
        src = {"title": "T", "sentences": [
            {"text": weak, "page": 1},
            {"text": "Middle filler sentence with no relation to anything at all.", "page": 1},
            {"text": strong, "page": 2},
        ]}
        row = [0.55, 0.10, 0.92]
        llm = MagicMock()
        llm.call.side_effect = [
            json.dumps({"sentences": [weak, strong]}),         # document order
            json.dumps({"supported": True, "reason": "ok"}),   # judgment vote 1
            json.dumps({"supported": True, "reason": "ok"}),   # judgment vote 2
        ]
        e = matcher._extract_evidence("claim", "p1", src, llm,
                                      "EX {CLAIM} {SOURCE}", "JG {CLAIM} {PASSAGE}",
                                      row=row)
        self.assertEqual(e["sentence"], strong)   # primary = highest-cosine hit
        self.assertEqual(e["page"], 2)

    def test_lex_scores_rank_verbatim_figure_first(self):
        claim = "Roughly 70% of foundational models since 2017 were developed in the US."
        texts = ["Blue elephants wander across quiet plains during misty mornings.",
                 "Around 70% of foundational AI models have been developed in the US since 2017.",
                 "The weather report for the region mentions scattered showers."]
        scores = matcher._lex_scores(claim, texts)
        self.assertEqual(scores.index(max(scores)), 1)
        self.assertEqual(scores[0], 0.0)          # zero overlap scores zero

    def test_lexical_rescue_reaches_cosine_blind_needle(self):
        # run-7 t17 class: the needle quotes the claim's figure verbatim, but its
        # cosine is buried — the union gate must still read its chunk, and rank
        # fusion must keep the hit in front of the judge.
        claim = "Roughly 70% of foundational models since 2017 were developed in the US."
        needle = ("Around 70% of foundational AI models have been developed "
                  "in the US since 2017.")
        sents = [{"text": "Blue elephants wander across quiet plains during misty "
                          "winter mornings again.", "page": 1 + i // 100}
                 for i in range(1500)]
        sents[1200] = {"text": needle, "page": 13}
        src = {"title": "Report", "sentences": sents}
        row = [0.5] * 1500
        row[1200] = 0.1                        # cosine actively buries the needle
        llm = MagicMock()
        llm.call.side_effect = lambda p, **kw: (
            json.dumps({"supported": True, "reason": "figure matches"}) if p.startswith("JG")
            else json.dumps({"sentences": [needle]}) if needle in p
            else json.dumps({"sentences": []}))
        e = matcher._extract_evidence(claim, "p1", src, llm,
                                      "EX {CLAIM} {SOURCE}", "JG {CLAIM} {PASSAGE}",
                                      row=row)
        self.assertEqual(e["sentence"], needle)
        self.assertEqual(e["page"], 13)
        self.assertTrue(e["supported"])
        # cost stays bounded: <= (top + lexical-rescue) extractions + 2 votes
        self.assertLessEqual(llm.call.call_count,
                             matcher.EXTRACT_TOP_CHUNKS + matcher.EXTRACT_LEX_CHUNKS + 2)


class TestContentCheck(unittest.TestCase):
    def _tmp(self, content, suffix=".txt"):
        f = tempfile.NamedTemporaryFile(mode="w", suffix=suffix, delete=False)
        f.write(content); f.close()
        return f.name

    def test_matching_document_ok(self):
        p = self._tmp("Computing Power and the Governance of Artificial Intelligence\n"
                      "Girish Sastry et al.\n" + "body text " * 200)
        self.assertEqual(dd.content_check(
            p, "Computing Power and the Governance of Artificial Intelligence"), "ok")

    def test_wrong_document_mismatch(self):
        p = self._tmp("14th Five-Year National Fisheries Development Plan\n" + "渔业 fisheries aquaculture " * 100)
        self.assertEqual(dd.content_check(
            p, "Chinese Economic Statecraft and the Dual-Circulation Strategy"), "mismatch")

    def test_generic_title_unknown(self):
        p = self._tmp("whatever " * 100)
        self.assertEqual(dd.content_check(p, "Nature"), "unknown")
        self.assertEqual(dd.content_check(p, "International Security"), "unknown")

    def test_venue_title_rescued_by_author_surname(self):
        # bib bug: title is the VENUE; the paper never headlines it, but the
        # author's surname is on page one
        p = self._tmp("Evaluating persuasive influence of political microtargeting\n"
                      "Kobi Hackenburg and Helen Margetts\n" + "results " * 200)
        self.assertEqual(dd.content_check(
            p, "Proceedings of the National Academy of Sciences",
            "Hackenburg, K. and Margetts, H."), "mismatch" if False else "ok")

    def test_venue_title_without_author_still_mismatch(self):
        p = self._tmp("Marvell Teralynx 10 Data Center Ethernet Switch product brief " * 20)
        self.assertEqual(dd.content_check(
            p, "Proceedings of the National Academy of Sciences",
            "Hackenburg, K."), "mismatch")

    def test_spaced_letters_artifact_survives(self):
        # PyPDF2 sometimes yields "P r epar ing f or ..." — compact matching must cope
        p = self._tmp("P r epar ing f or the Int elligence Expl osion\n" + "b o d y " * 200)
        self.assertEqual(dd.content_check(p, "Preparing for the Intelligence Explosion"), "ok")


if __name__ == "__main__":
    unittest.main()
