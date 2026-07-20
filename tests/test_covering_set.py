"""Covering-set evidence display pass (loop round-1 fix, owner-approved
2026-07-10): a SUPPORTED cited claim gets one extra small LLM call that maps
its citable components to the candidate sentences proving them, plus the
components no candidate proves. Display-only — the verdict is never touched;
the viewer renders the mapping + an amber "no evidence shown for: X" line.
No API calls.

Run:  venv/bin/python3 -m unittest tests.test_covering_set -v
"""
import os
import sys
import json
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.papertrail import matcher

# Single sentence -> no tail-rescue suffixes muddying routing.
CLAIM = "The 2022 tournament found experts gave higher AI-risk numbers than superforecasters."
S_MAIN = "In the tournament, domain experts assigned systematically higher probabilities to AI risk than superforecasters did."
S_YEAR = "The Existential Risk Persuasion Tournament ran from June to October 2022."
S_NOISE = "Participants received compensation for completing each survey wave."


def _fake_cosine(a, b, **kw):
    return [[0.8] * len(b) for _ in a]


def _sources():
    return {"p1": {"title": "XPT paper", "key": "xpt2023",
                   "sentences": [{"text": S_MAIN, "page": 3},
                                 {"text": S_YEAR, "page": 1},
                                 {"text": S_NOISE, "page": 9}],
                   "claims": []}}


def _claim():
    return {"id": "t1", "text": CLAIM, "markers": ["xpt2023"], "paper_ids": ["p1"]}


def _llm(covering_response):
    """Candidate judge supports on the first sentence; the covering-set call
    (recognizable by its CANDIDATE SENTENCES block) returns `covering_response`
    (a string, or an Exception to raise)."""
    llm = MagicMock()

    def call(p, **kw):
        if "CANDIDATE SENTENCES" in p:
            if isinstance(covering_response, Exception):
                raise covering_response
            return covering_response
        if "evidence finder" in p:            # escalation probe's extraction
            return json.dumps({"sentences": []})
        if "tournament happened in 2022" in p:  # the unprovable component alone
            return json.dumps({"supported": False, "reason": "absent"})
        return json.dumps({"supported": True, "reason": "stated in the passage"})

    llm.call.side_effect = call
    return llm


def _run(llm, claims=None, reuse=None):
    with patch.object(matcher.embeddings, "cosine_matrix", side_effect=_fake_cosine):
        return matcher.run(claims or [_claim()], _sources(), llm,
                           partial_check=False, reuse=reuse)


class TestParseCovering(unittest.TestCase):
    CANDS = [{"paper_id": "p1", "source_title": "XPT paper", "text": S_MAIN, "page": 3},
             {"paper_id": "p1", "source_title": "XPT paper", "text": S_YEAR, "page": 1}]

    def test_valid_picks_map_to_candidates(self):
        raw = json.dumps({"components": [
            {"part": "experts gave higher numbers", "picks": [1]},
            {"part": "the tournament was in 2022", "picks": [2]}]})
        cov = matcher._parse_covering(raw, self.CANDS)
        self.assertEqual(len(cov["covered"]), 2)
        self.assertEqual(cov["covered"][0]["sentence"], S_MAIN)
        self.assertEqual(cov["covered"][1]["sentence"], S_YEAR)
        self.assertEqual(cov["covered"][1]["page"], 1)
        self.assertEqual(cov["uncovered"], [])

    def test_empty_and_out_of_range_picks_become_uncovered(self):
        raw = json.dumps({"components": [
            {"part": "proven part", "picks": [2]},
            {"part": "unproven part", "picks": []},
            {"part": "hallucinated pick", "picks": [99, 0, "x"]}]})
        cov = matcher._parse_covering(raw, self.CANDS)
        self.assertEqual([c["component"] for c in cov["covered"]], ["proven part"])
        self.assertEqual(cov["uncovered"], ["unproven part", "hallucinated pick"])

    def test_string_picks_are_coerced(self):
        # flash-lite emits picks as strings about as often as ints (round-1 t4/t5)
        raw = json.dumps({"components": [
            {"part": "a", "picks": ["1"]},
            {"part": "b", "picks": [" 2 "]}]})
        cov = matcher._parse_covering(raw, self.CANDS)
        self.assertEqual(len(cov["covered"]), 2)
        self.assertEqual(cov["uncovered"], [])

    def test_garbage_returns_none(self):
        self.assertIsNone(matcher._parse_covering("not json at all", self.CANDS))
        self.assertIsNone(matcher._parse_covering(json.dumps({"components": "nope"}),
                                                  self.CANDS))
        self.assertIsNone(matcher._parse_covering("", self.CANDS))


class TestUncoveredEscalation(unittest.TestCase):
    """Round-2 fix: uncovered components are probed alone via full-text
    extraction; a hit moves them off the amber line, a miss keeps them."""

    def _cov(self, probe, n_uncovered=2):
        parts = [{"part": f"part {i}", "picks": []} for i in range(n_uncovered)]
        parts.insert(0, {"part": "proven part", "picks": [1]})
        resp = json.dumps({"components": parts})
        with patch.object(matcher, "_covering_candidates", return_value=[
                {"paper_id": "p1", "source_title": "XPT paper",
                 "text": S_MAIN, "page": 3}]):
            llm = MagicMock(); llm.call.return_value = resp
            return matcher._covering_set("claim", ["p1"], {}, lambda p: None,
                                         [], llm, "{CLAIM}{CANDIDATES}", probe=probe)

    def test_probe_hit_moves_part_to_covered(self):
        hit = {"component": "part 0", "paper_id": "p1", "source_title": "XPT paper",
               "sentence": S_YEAR, "page": 1, "snippet": "x", "via": "escalation"}
        cov = self._cov(lambda part: hit if part == "part 0" else None)
        self.assertIn("part 0", [c["component"] for c in cov["covered"]])
        self.assertEqual(cov["uncovered"], ["part 1"])

    def test_probe_miss_keeps_amber(self):
        cov = self._cov(lambda part: None)
        self.assertEqual(cov["uncovered"], ["part 0", "part 1"])

    def test_probe_capped(self):
        calls = []
        cov = self._cov(lambda part: calls.append(part) or None, n_uncovered=5)
        self.assertEqual(len(calls), matcher.COVER_ESCALATE_MAX)
        self.assertEqual(len(cov["uncovered"]), 5)   # none proven, all kept


class TestCoveringSpans(unittest.TestCase):
    """'Read it in context' spans (owner request 2026-07-11): used sentences +
    all original text between them; far-apart sentences split on an ellipsis."""

    SENTS = [{"text": f"Sentence number {i}.", "page": 1} for i in range(40)]

    def _cov(self, picked_idx):
        return {"covered": [{"paper_id": "p1",
                             "sentence": self.SENTS[i]["text"]} for i in picked_idx],
                "uncovered": []}

    def _spans(self, picked_idx):
        return matcher._covering_spans(self._cov(picked_idx), ["p1"],
                                       {"p1": {"title": "T", "sentences": self.SENTS}})

    def test_adjacent_sentences_join_with_between_text(self):
        s = self._spans([3, 6])[0]
        self.assertIn("Sentence number 3.", s["text"])
        self.assertIn("Sentence number 4.", s["text"])   # the between text
        self.assertIn("Sentence number 6.", s["text"])
        self.assertNotIn("[…]", s["text"])
        self.assertEqual(s["n_used"], 2)

    def test_far_apart_sentences_split_on_ellipsis(self):
        s = self._spans([2, 30])[0]
        self.assertIn("[…]", s["text"])
        self.assertNotIn("Sentence number 15.", s["text"])

    def test_runaway_cluster_falls_back_to_windows(self):
        idxs = list(range(0, 40, 7))   # gaps of 7 (< COVER_SPAN_GAP) -> one 36-sent cluster
        s = self._spans(idxs)[0]
        self.assertIn("[…]", s["text"])          # windows joined by ellipses
        self.assertNotIn("Sentence number 3.", s["text"])   # between-window text dropped

    def test_unmapped_sentence_yields_no_span(self):
        cov = {"covered": [{"paper_id": "p1", "sentence": "not in the source"}],
               "uncovered": []}
        self.assertEqual(matcher._covering_spans(cov, ["p1"],
                         {"p1": {"title": "T", "sentences": self.SENTS}}), [])


class TestCoveringPass(unittest.TestCase):

    def test_supported_claim_gains_covering_block(self):
        # Candidate 1 is always the already-shown evidence sentence (S_MAIN).
        resp = json.dumps({"components": [
            {"part": "experts higher than superforecasters", "picks": [1]},
            {"part": "the tournament happened in 2022", "picks": []}]})
        c = _run(_llm(resp))["text_claims"][0]
        self.assertEqual(c["verdict"], "supported")
        self.assertTrue(c.get("covering_checked"))
        cov = c["covering"]
        self.assertEqual(cov["covered"][0]["sentence"], S_MAIN)
        self.assertEqual(cov["uncovered"], ["the tournament happened in 2022"])

    def test_covering_failure_never_sinks_the_verdict(self):
        c = _run(_llm(RuntimeError("api down")))["text_claims"][0]
        self.assertEqual(c["verdict"], "supported")
        self.assertTrue(c.get("covering_checked"))
        self.assertNotIn("covering", c)

    def test_unsupported_claim_gets_no_covering_call(self):
        llm = MagicMock()
        llm.call.return_value = json.dumps({"supported": False, "reason": "absent"})
        c = _run(llm)["text_claims"][0]
        self.assertEqual(c["verdict"], "unsupported")
        self.assertNotIn("covering", c)
        self.assertFalse(any("CANDIDATE SENTENCES" in str(k)
                             for k, *_ in llm.call.call_args_list))

    def test_reuse_buys_covering_once(self):
        prev = {"id": "t1", "text": CLAIM, "markers": ["xpt2023"],
                "paper_ids": ["p1"], "verdict": "supported", "method": "llm",
                "reason": "cached", "evidences": [
                    {"paper_id": "p1", "source_title": "XPT paper", "supported": True,
                     "sentence": S_MAIN, "page": 3, "snippet": "", "cosine": 0.8}]}
        resp = json.dumps({"components": [{"part": "the finding", "picks": [1]}]})
        llm = _llm(resp)
        c = _run(llm, reuse={"t1": prev})["text_claims"][0]
        self.assertEqual(c["verdict"], "supported")     # verdict reused verbatim
        self.assertIn("covering", c)
        self.assertTrue(c.get("covering_checked"))
        # exactly TWO calls: the covering call + its round-5 pick-verify audit
        self.assertEqual(llm.call.call_count, 2)

    def test_reuse_with_covering_checked_makes_no_call(self):
        prev = {"id": "t1", "text": CLAIM, "markers": ["xpt2023"],
                "paper_ids": ["p1"], "verdict": "supported", "method": "llm",
                "reason": "cached", "covering_checked": True, "evidences": []}
        llm = MagicMock()
        c = _run(llm, reuse={"t1": prev})["text_claims"][0]
        self.assertEqual(c["verdict"], "supported")
        llm.call.assert_not_called()


if __name__ == "__main__":
    unittest.main()
