"""Incremental re-verification: claim matching across runs (rerun.match_claims)
and the matcher's reuse short-circuit (zero LLM calls for unchanged claims).
No API calls.

Run:  venv/bin/python3 -m unittest tests.test_rerun -v
"""
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.papertrail import matcher, rerun


def _prev(id_, text, verdict="supported", markers=("a",), **kw):
    c = {"id": id_, "text": text, "markers": list(markers), "paper_ids": ["p1"],
         "verdict": verdict, "method": "llm", "reason": "ok", "cosine": 0.9,
         "evidences": [{"paper_id": "p1", "source_title": "Bridge Survey",
                        "supported": verdict == "supported",
                        "sentence": "The surveyed bridge measures 400 m in length.",
                        "page": 3}]}
    c.update(kw)
    return c


class TestMatchClaims(unittest.TestCase):

    def test_exact_match_reuses_despite_id_shift(self):
        prev = [_prev("t0", "The bridge is long.")]
        new = [{"id": "t5", "text": "The bridge is long.", "markers": ["a"]}]
        m = rerun.match_claims(prev, new)
        self.assertIsNotNone(m["t5"]["reuse"])
        self.assertIsNone(m["t5"]["prev"])

    def test_whitespace_and_case_are_normalized(self):
        prev = [_prev("t0", "The  bridge is\nlong.")]
        new = [{"id": "t0", "text": "the bridge is long.", "markers": ["a"]}]
        self.assertIsNotNone(rerun.match_claims(prev, new)["t0"]["reuse"])

    def test_marker_change_is_not_an_exact_match(self):
        prev = [_prev("t0", "The bridge is long.", markers=("a",))]
        new = [{"id": "t0", "text": "The bridge is long.", "markers": ["b"]}]
        m = rerun.match_claims(prev, new)
        self.assertIsNone(m["t0"]["reuse"])          # re-judged against the new source
        self.assertIsNotNone(m["t0"]["prev"])        # but still paired for the diff

    def test_edited_claim_gets_fuzzy_prev_for_the_diff(self):
        prev = [_prev("t0", "The bridge is exactly four hundred meters long.",
                      verdict="unsupported")]
        new = [{"id": "t0", "text": "The bridge is roughly four hundred meters long.",
                "markers": ["a"]}]
        m = rerun.match_claims(prev, new)
        self.assertIsNone(m["t0"]["reuse"])
        self.assertEqual(m["t0"]["prev"]["verdict"], "unsupported")
        self.assertIn("exactly", m["t0"]["prev"]["text"])

    def test_brand_new_claim_matches_nothing(self):
        prev = [_prev("t0", "The bridge is long.")]
        new = [{"id": "t1", "text": "Entirely different topic about glaciers.",
                "markers": ["z"]}]
        m = rerun.match_claims(prev, new)
        self.assertIsNone(m["t1"]["reuse"])
        self.assertIsNone(m["t1"]["prev"])

    def test_duplicate_texts_pair_one_to_one(self):
        prev = [_prev("t0", "Same sentence."), _prev("t1", "Same sentence.")]
        new = [{"id": "t0", "text": "Same sentence.", "markers": ["a"]},
               {"id": "t1", "text": "Same sentence.", "markers": ["a"]},
               {"id": "t2", "text": "Same sentence.", "markers": ["a"]}]
        m = rerun.match_claims(prev, new)
        reused = [i for i in ("t0", "t1", "t2") if m[i]["reuse"] is not None]
        self.assertEqual(len(reused), 2)             # only two previous copies exist

    def test_reusable_filter(self):
        self.assertTrue(rerun.reusable(_prev("t0", "x", verdict="supported")))
        self.assertTrue(rerun.reusable(_prev("t0", "x", verdict="unsupported")))
        # 'own' claims reuse: the verdict is free to rebuild but the paid
        # own_kind tag on the dict lives nowhere else.
        self.assertTrue(rerun.reusable(_prev("t0", "x", verdict="own")))
        self.assertFalse(rerun.reusable(_prev("t0", "x", verdict="unsupported",
                                              reason="source_file_missing: a.pdf")))
        # Legacy uncited claims (pre-'own'-verdict runs) must be re-derived so
        # they upgrade from red 'unsupported' to indigo 'own' (free, no LLM).
        self.assertFalse(rerun.reusable(_prev("t0", "x", verdict="unsupported",
                                              reason="no_citation_marker")))
        # Verdicts minted while the model API was failing are outage artifacts:
        # reusing them would make the corruption permanent — a plain re-run must
        # retry them. Flagged runs carry judge_error; older analyses only the
        # reason strings.
        self.assertFalse(rerun.reusable({**_prev("t0", "x", verdict="unsupported"),
                                         "judge_error": True}))
        self.assertFalse(rerun.reusable(_prev("t0", "x", verdict="unsupported",
                                              reason="no LLM response -> treated as unsupported")))
        self.assertFalse(rerun.reusable(_prev("t0", "x", verdict="unsupported",
                                              reason="LLM judgment unparseable -> treated as unsupported")))


class TestChangedSourceFiles(unittest.TestCase):

    def test_unchanged_hashes_change_nothing(self):
        self.assertEqual(rerun.changed_source_files({"a.pdf": "h1"}, {"a.pdf": "h1"}),
                         set())

    def test_replaced_and_new_files_are_flagged(self):
        prev = {"a.pdf": "h1", "b.txt": "h2"}
        cur = {"a.pdf": "DIFFERENT", "b.txt": "h2", "c.pdf": "h3"}
        self.assertEqual(rerun.changed_source_files(prev, cur), {"a.pdf", "c.pdf"})

    def test_pre_hash_analysis_returns_none(self):
        # analyses written before hash recording: the caller keeps the historic
        # trust-the-files behavior (and --full stays the manual escape hatch)
        self.assertIsNone(rerun.changed_source_files(None, {"a.pdf": "h1"}))

    def test_file_gone_from_current_run_is_not_flagged(self):
        # a source the new text no longer cites is irrelevant to reuse
        self.assertEqual(rerun.changed_source_files({"a.pdf": "h1", "old.pdf": "hx"},
                                                    {"a.pdf": "h1"}), set())


def _fake_cosine(a, b, **kw):
    return [[0.8] * len(b) for _ in a]


def _sources():
    # sc1's evidence sits two sentences away from sc0's: the coverage window is
    # ±1 around a used sentence (same as the live path), so adjacency would
    # legitimately mark it used too.
    return {"p1": {"title": "Bridge Survey", "key": "a",
                   "sentences": [{"text": "The surveyed bridge measures 400 m in length.",
                                  "page": 3},
                                 {"text": "A connective middle sentence sits between them.",
                                  "page": 3},
                                 {"text": "Unrelated filler sentence about weather patterns.",
                                  "page": 4}],
                   "claims": [{"id": "sc0", "text": "The bridge is 400 m long.",
                               "evidence": ["The surveyed bridge measures 400 m in length."]},
                              {"id": "sc1", "text": "Weather patterns vary.",
                               "evidence": ["Unrelated filler sentence about weather patterns."]}]}}


class TestMatcherReuse(unittest.TestCase):

    def test_reused_claim_makes_zero_llm_calls_and_keeps_verdict(self):
        tc = {"id": "t3", "text": "The bridge is long.", "markers": ["a"], "paper_ids": ["p1"]}
        prev = _prev("t0", "The bridge is long.")
        llm = MagicMock()
        llm.call.side_effect = AssertionError("reused claim must not call the LLM")
        with patch.object(matcher.embeddings, "cosine_matrix", side_effect=_fake_cosine):
            analysis = matcher.run([tc], _sources(), llm, reuse={"t3": prev})
        c = analysis["text_claims"][0]
        self.assertEqual(c["verdict"], "supported")
        self.assertEqual(c["id"], "t3")                       # identity refreshed
        self.assertEqual(c["reason"], "ok")
        self.assertEqual(analysis["coverage"]["totals"]["supported"], 1)

    def test_reused_evidence_still_counts_for_coverage_and_omitted(self):
        tc = {"id": "t3", "text": "The bridge is long.", "markers": ["a"], "paper_ids": ["p1"]}
        prev = _prev("t0", "The bridge is long.")
        llm = MagicMock()
        llm.call.side_effect = AssertionError("no LLM calls expected")
        with patch.object(matcher.embeddings, "cosine_matrix", side_effect=_fake_cosine):
            analysis = matcher.run([tc], _sources(), llm, reuse={"t3": prev})
        # the evidence sentence maps back to the source -> its claim is "used"
        self.assertEqual(analysis["coverage"]["per_source"]["p1"]["used"], 1)
        omitted_ids = [o["source_claim_id"] for o in analysis["omitted"]]
        self.assertEqual(omitted_ids, ["sc1"])

    def test_missing_file_now_blocks_reuse(self):
        tc = {"id": "t3", "text": "The bridge is long.", "markers": ["a"],
              "paper_ids": [], "missing_files": ["a.pdf"]}
        prev = _prev("t0", "The bridge is long.")
        with patch.object(matcher.embeddings, "cosine_matrix", side_effect=_fake_cosine):
            analysis = matcher.run([tc], {}, MagicMock(), reuse={"t3": prev})
        c = analysis["text_claims"][0]
        self.assertEqual(c["verdict"], "unsupported")
        self.assertIn("source_file_missing", c["reason"])

    def test_stale_prev_field_is_dropped_on_reuse(self):
        tc = {"id": "t3", "text": "The bridge is long.", "markers": ["a"], "paper_ids": ["p1"]}
        prev = _prev("t0", "The bridge is long.", prev={"changed": True, "text": "older"})
        llm = MagicMock()
        llm.call.side_effect = AssertionError("no LLM calls expected")
        with patch.object(matcher.embeddings, "cosine_matrix", side_effect=_fake_cosine):
            analysis = matcher.run([tc], _sources(), llm, reuse={"t3": prev})
        self.assertNotIn("prev", analysis["text_claims"][0])


def _sources2():
    s = _sources()
    s["p2"] = {"title": "Second Source", "key": "b",
               "sentences": [{"text": "Another supporting sentence entirely.", "page": 1}],
               "claims": [{"id": "sc9", "text": "Another claim.",
                           "evidence": ["Another supporting sentence entirely."]}]}
    return s


class TestReusePartialCheck(unittest.TestCase):
    """Reuse skips the GROUNDING chain, never the (default-on) partial-support
    nudge: a reused supported multi-citation claim without the partial_checked
    marker buys the check once; the marker then carries it forward."""

    def _tc(self):
        return {"id": "t3", "text": "The bridge is long and the weather varies.",
                "markers": ["a", "b"], "paper_ids": ["p1", "p2"]}

    def _prev_multi(self, **kw):
        c = _prev("t0", "The bridge is long and the weather varies.", markers=("a", "b"))
        c["paper_ids"] = ["p1", "p2"]
        c["evidences"].append({"paper_id": "p2", "source_title": "Second Source",
                               "supported": False,
                               "sentence": "Another supporting sentence entirely.",
                               "page": 1})
        c.update(kw)
        return c

    def test_unchecked_reused_claim_buys_the_partial_pass(self):
        llm = MagicMock()
        llm.call.side_effect = AssertionError("grounding chain must not run on reuse")
        with patch.object(matcher.embeddings, "cosine_matrix", side_effect=_fake_cosine), \
             patch.object(matcher, "_partial_flags",
                          return_value={"partial_support": {"reason": "x", "votes": "3-0",
                                                            "escalated": True}}) as pf:
            analysis = matcher.run([self._tc()], _sources2(), llm,
                                   reuse={"t3": self._prev_multi()}, partial_check=True)
        c = analysis["text_claims"][0]
        pf.assert_called_once()
        self.assertTrue(c.get("partial_checked"))
        self.assertIn("partial_support", c)
        self.assertEqual(c["verdict"], "supported")   # a nudge, never a veto

    def test_checked_reused_claim_skips_the_partial_pass(self):
        llm = MagicMock()
        llm.call.side_effect = AssertionError("no LLM calls expected")
        with patch.object(matcher.embeddings, "cosine_matrix", side_effect=_fake_cosine), \
             patch.object(matcher, "_partial_flags") as pf:
            analysis = matcher.run([self._tc()], _sources2(), llm,
                                   reuse={"t3": self._prev_multi(partial_checked=True)},
                                   partial_check=True)
        pf.assert_not_called()
        self.assertTrue(analysis["text_claims"][0].get("partial_checked"))

    def test_partial_flags_are_stripped_when_check_is_off(self):
        llm = MagicMock()
        llm.call.side_effect = AssertionError("no LLM calls expected")
        prev = self._prev_multi(partial_checked=True,
                                partial_support={"reason": "x", "votes": "3-0"},
                                over_citation={"sources": []})
        with patch.object(matcher.embeddings, "cosine_matrix", side_effect=_fake_cosine):
            analysis = matcher.run([self._tc()], _sources2(), llm,
                                   reuse={"t3": prev}, partial_check=False)
        c = analysis["text_claims"][0]
        for k in ("partial_support", "over_citation", "partial_checked"):
            self.assertNotIn(k, c)

    def test_single_citation_reused_claim_buys_the_partial_pass(self):
        # Single-citation claims qualify since the owner walkthrough (2026-07-07):
        # an unchecked reused one buys the nudge pass exactly like multi-citation.
        tc = {"id": "t3", "text": "The bridge is long.", "markers": ["a"],
              "paper_ids": ["p1"]}
        llm = MagicMock()
        llm.call.side_effect = AssertionError("grounding chain must not run on reuse")
        with patch.object(matcher.embeddings, "cosine_matrix", side_effect=_fake_cosine), \
             patch.object(matcher, "_partial_flags", return_value={}) as pf:
            analysis = matcher.run([tc], _sources(), llm,
                                   reuse={"t3": _prev("t0", "The bridge is long.")},
                                   partial_check=True)
        pf.assert_called_once()
        self.assertTrue(analysis["text_claims"][0].get("partial_checked"))


class TestOwnClaimReuse(unittest.TestCase):

    def test_own_claim_reuse_keeps_the_paid_tag(self):
        tc = {"id": "t3", "text": "I argue this is important.", "markers": [],
              "paper_ids": []}
        prev = {"id": "t0", "text": "I argue this is important.", "markers": [],
                "paper_ids": [], "verdict": "own", "method": "none", "cosine": None,
                "evidence": None, "evidences": [], "reason": "no_citation_marker",
                "own_kind": {"kind": "opinion", "reason": "author's judgment",
                             "model": "m", "prompt_sha": "abc12345"}}
        llm = MagicMock()
        llm.call.side_effect = AssertionError("no LLM calls expected")
        with patch.object(matcher.embeddings, "cosine_matrix", side_effect=_fake_cosine):
            analysis = matcher.run([tc], {}, llm, reuse={"t3": prev})
        c = analysis["text_claims"][0]
        self.assertEqual(c["verdict"], "own")
        self.assertEqual(c["own_kind"]["kind"], "opinion")   # tag not re-bought


if __name__ == "__main__":
    unittest.main()
