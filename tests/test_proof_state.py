"""Proof-state badge (round-4 product fix, 2026-07-11): matcher derives
`proof_state` ("partial"/"full") from the covering pass on supported cited
claims — the verdict field NEVER changes; the viewer renders the amber
"NOT PROVEN AS WRITTEN" badge variant (round-8 fix B wording; was
"SUPPORTED — PARTLY PROVEN" in rounds 4-7) + filter chip + low confidence.

Run:  venv/bin/python3 -m unittest tests.test_proof_state -v
"""
import os
import sys
import json
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.papertrail import matcher, viewer

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
    llm = MagicMock()

    def call(p, **kw):
        if "CANDIDATE SENTENCES" in p:
            if isinstance(covering_response, Exception):
                raise covering_response
            return covering_response
        if "evidence finder" in p:              # escalation probe's extraction
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


RESP_PARTIAL = json.dumps({"components": [
    {"part": "experts higher than superforecasters", "picks": [1]},
    {"part": "the tournament happened in 2022", "picks": []}]})
RESP_FULL = json.dumps({"components": [
    {"part": "experts higher than superforecasters", "picks": [1]}]})


class TestMatcherProofState(unittest.TestCase):

    def test_uncovered_component_marks_partial_verdict_untouched(self):
        c = _run(_llm(RESP_PARTIAL))["text_claims"][0]
        self.assertEqual(c["verdict"], "supported")   # hard rule: never flips
        self.assertEqual(c["proof_state"], "partial")
        self.assertTrue(c["covering"]["uncovered"])

    def test_everything_covered_marks_full(self):
        c = _run(_llm(RESP_FULL))["text_claims"][0]
        self.assertEqual(c["verdict"], "supported")
        self.assertEqual(c["proof_state"], "full")
        self.assertEqual(c["covering"]["uncovered"], [])

    def test_no_covering_block_means_no_proof_state(self):
        c = _run(_llm(RuntimeError("api down")))["text_claims"][0]
        self.assertEqual(c["verdict"], "supported")
        self.assertNotIn("covering", c)
        self.assertNotIn("proof_state", c)

    def test_unsupported_claim_gets_no_proof_state(self):
        llm = MagicMock()
        llm.call.return_value = json.dumps({"supported": False, "reason": "absent"})
        c = _run(llm)["text_claims"][0]
        self.assertEqual(c["verdict"], "unsupported")
        self.assertNotIn("proof_state", c)


class TestReuseProofState(unittest.TestCase):

    def _prev(self, **kw):
        prev = {"id": "t1", "text": CLAIM, "markers": ["xpt2023"],
                "paper_ids": ["p1"], "verdict": "supported", "method": "llm",
                "reason": "cached", "evidences": [
                    {"paper_id": "p1", "source_title": "XPT paper", "supported": True,
                     "sentence": S_MAIN, "page": 3, "snippet": "", "cosine": 0.8}]}
        prev.update(kw)
        return prev

    def test_covering_rebuy_computes_proof_state(self):
        llm = _llm(RESP_PARTIAL)
        c = _run(llm, reuse={"t1": self._prev()})["text_claims"][0]
        self.assertEqual(c["verdict"], "supported")
        self.assertEqual(c["proof_state"], "partial")

    def test_cached_covering_predating_proof_state_buys_audit_once(self):
        prev = self._prev(covering_checked=True,
                          covering={"covered": [], "uncovered": ["the 2022 date"]})
        llm = MagicMock()
        llm.call.return_value = json.dumps(
            {"verified": [{"part": "the 2022 date", "keep": []}],
             "new_components": [], "common_knowledge": []})
        with patch.object(matcher, "_extract_evidence", return_value=None):
            c = _run(llm, reuse={"t1": prev})["text_claims"][0]
        self.assertEqual(c["proof_state"], "partial")
        # ONE call: the round-5 pick-verify audit the cache predates.
        self.assertEqual(llm.call.call_count, 1)
        self.assertTrue(c["covering"].get("pick_verified"))

    def test_cached_full_covering_goes_partial_on_unshown_year(self):
        # Round-7 fix A: the claim asserts 2022 but the only kept pick never
        # shows it — the deterministic entity check ambers the year (r4 t6
        # Finland semantics), so the cached "full" honestly rederives partial.
        prev = self._prev(covering_checked=True,
                          covering={"covered": [{"component": "x", "paper_id": "p1",
                                                 "sentence": S_MAIN}],
                                    "uncovered": []})
        llm = MagicMock()
        llm.call.return_value = json.dumps(
            {"verified": [{"part": "x", "keep": [1]}],
             "new_components": [], "common_knowledge": []})
        with patch.object(matcher, "_extract_evidence", return_value=None):
            c = _run(llm, reuse={"t1": prev})["text_claims"][0]
        self.assertEqual(c["proof_state"], "partial")
        self.assertIn("2022", c["covering"]["uncovered"])
        self.assertEqual(llm.call.call_count, 1)   # the audit only

    def test_all_gaps_common_knowledge_derives_full(self):
        prev = self._prev(covering_checked=True,
                          covering={"covered": [], "uncovered": ["obvious bit"],
                                    "common_knowledge": ["obvious bit"],
                                    "pick_verified": True})
        llm = MagicMock()
        c = _run(llm, reuse={"t1": prev})["text_claims"][0]
        self.assertEqual(c["proof_state"], "full")     # grey gaps don't count
        llm.call.assert_not_called()

    def test_cached_audited_covering_makes_no_call(self):
        prev = self._prev(covering_checked=True,
                          covering={"covered": [], "uncovered": ["the 2022 date"],
                                    "common_knowledge": [], "pick_verified": True})
        llm = MagicMock()
        c = _run(llm, reuse={"t1": prev})["text_claims"][0]
        self.assertEqual(c["proof_state"], "partial")
        llm.call.assert_not_called()              # audit mark carries forward

    def test_cached_checked_but_unparsed_covering_stays_stateless(self):
        prev = self._prev(covering_checked=True)  # checked, no block parsed
        llm = MagicMock()
        c = _run(llm, reuse={"t1": prev})["text_claims"][0]
        self.assertNotIn("proof_state", c)
        llm.call.assert_not_called()


def _analysis(claim):
    pids = claim.get("paper_ids", [])
    return {"text_claims": [claim],
            "sources": [{"paper_id": p, "key": p, "filename": f"{p}.txt",
                         "title": f"Source {p}"} for p in pids],
            "coverage": {"totals": {"claims": 1, "supported": 1, "unsupported": 0,
                                    "own": 0, "omitted": 0}},
            "metadata": {}, "omitted": []}


def _render(claim):
    out = os.path.join(tempfile.mkdtemp(), "v.html")
    viewer.generate(_analysis(claim), out)
    with open(out, encoding="utf-8") as f:
        page = f.read()
    # Drop the static legend so badge assertions match only real card badges.
    pre, _, rest = page.partition('<details class="legend">')
    _, _, post = rest.partition('</details>')
    return pre + post


def _viewer_claim(**kw):
    c = {"id": "t1", "text": CLAIM, "markers": ["xpt2023"], "paper_ids": ["p1"],
         "verdict": "supported", "method": "llm", "reason": "r",
         "evidences": [{"paper_id": "p1", "source_title": "XPT paper",
                        "supported": True, "sentence": S_MAIN, "page": 3,
                        "snippet": S_MAIN[:20]}]}
    c["evidence"] = c["evidences"][0]
    c.update(kw)
    return c


class TestViewerProofState(unittest.TestCase):

    def test_partial_renders_amber_badge_chip_and_low_confidence(self):
        page = _render(_viewer_claim(
            proof_state="partial", covering_checked=True,
            covering={"covered": [], "uncovered": ["the 2022 date"]}))
        self.assertIn("NOT PROVEN AS WRITTEN", page)
        self.assertNotIn("SUPPORTED — PARTLY", page)
        self.assertIn("partlyproven", page)               # card class + filter chip
        self.assertIn('data-f="partlyproven"', page)
        self.assertIn("Not proven as written (1)", page)
        self.assertIn("not proven as written)", page)     # header total
        self.assertIn('confchip low', page)

    def test_full_renders_plain_supported_badge(self):
        page = _render(_viewer_claim(
            proof_state="full", covering_checked=True,
            covering={"covered": [{"component": "x", "paper_id": "p1",
                                   "sentence": S_MAIN, "source_title": "XPT paper"}],
                      "uncovered": []}))
        self.assertNotIn("NOT PROVEN AS WRITTEN", page)
        self.assertNotIn('data-f="partlyproven"', page)
        self.assertIn(">SUPPORTED</span>", page)

    def test_no_proof_state_renders_like_before(self):
        page = _render(_viewer_claim())
        self.assertNotIn("NOT PROVEN AS WRITTEN", page)
        self.assertNotIn("partlyproven", page)

    def test_review_payload_carries_proof_state(self):
        data = viewer._review_data(
            _analysis(_viewer_claim(proof_state="partial")),
            [_viewer_claim(proof_state="partial")], "/tmp/x")
        self.assertEqual(data["claims"][0]["proof_state"], "partial")


if __name__ == "__main__":
    unittest.main()
