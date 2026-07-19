"""Second-opinion pass (--second-opinion) — no API calls.

Run:  venv/bin/python3 -m unittest tests.test_second_opinion
"""
import os
import sys
import json
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.papertrail import second_opinion, viewer


def _no_legend(page):
    """Drop the static 'How to read this' legend so chip-label sentinels match
    only real card chips, not the legend swatches (viewer.py legend, 2026-07-06)."""
    pre, _, rest = page.partition('<details class="legend">')
    _, _, post = rest.partition('</details>')
    return pre + post


class FakeLLM:
    """Returns canned judgment JSONs in order (last one repeats); counts calls."""

    def __init__(self, verdicts, model="gemini/gemini-2.5-flash"):
        self.model = model
        self.verdicts = list(verdicts)
        self.calls = 0
        self.prompts = []

    def call(self, prompt, **kw):
        self.prompts.append(prompt)
        v = self.verdicts[min(self.calls, len(self.verdicts) - 1)]
        self.calls += 1
        return json.dumps({"supported": v, "reason": f"second judge says {v}"})


def claim(cid="t1", verdict="supported", sentence="The source states X.", **kw):
    base = {"id": cid, "text": "My claim about X.", "markers": ["a"],
            "verdict": verdict, "method": "llm", "reason": "judged",
            "evidences": [{"paper_id": "p1", "source_title": "Source A",
                           "supported": verdict == "supported",
                           "sentence": sentence, "window": f"Before. {sentence} After."}]}
    base.update(kw)
    return base


class TestCheckable(unittest.TestCase):

    def test_judged_claims_are_checkable_both_verdicts(self):
        self.assertTrue(second_opinion.checkable(claim(verdict="supported")))
        self.assertTrue(second_opinion.checkable(claim(verdict="unsupported")))

    def test_own_missing_file_and_no_sentence_are_skipped(self):
        self.assertFalse(second_opinion.checkable(claim(verdict="own")))
        self.assertFalse(second_opinion.checkable(
            claim(verdict="unsupported", reason="source_file_missing: a.pdf")))
        c = claim(verdict="unsupported")
        c["evidences"] = [{"paper_id": "p1", "supported": False, "sentence": None}]
        self.assertFalse(second_opinion.checkable(c))

    def test_author_ruled_claims_are_skipped(self):
        c = claim()
        c["owner_flag"] = {"author_says": "wrong"}
        self.assertFalse(second_opinion.checkable(c))

    def test_author_ruled_claim_drops_a_stale_carried_opinion(self):
        # Incremental runs reuse the whole claim dict — a disagreement flagged
        # BEFORE the author ruled must not survive next to "author disputed".
        c = claim()
        c["owner_flag"] = {"author_says": "wrong"}
        c["second_opinion"] = {"model": "gemini/gemini-2.5-flash", "agrees": False,
                               "verdict": "unsupported", "reason": "old", "votes": "2-1"}
        llm = FakeLLM([True])
        summary = second_opinion.run([c], llm, workers=1)
        self.assertEqual(llm.calls, 0)
        self.assertNotIn("second_opinion", c)
        self.assertEqual(summary["fp_flags"], [])
        self.assertEqual(summary["reused"], 0)


class TestRun(unittest.TestCase):

    def test_agreement_costs_one_call(self):
        c = claim(verdict="supported")
        llm = FakeLLM([True])
        summary = second_opinion.run([c], llm, workers=1)
        self.assertEqual(llm.calls, 1)
        so = c["second_opinion"]
        self.assertTrue(so["agrees"])
        self.assertEqual(so["verdict"], "supported")
        self.assertIsNone(so["votes"])
        self.assertEqual(summary["fp_flags"], [])
        self.assertEqual(summary["checked"], 1)

    def test_confirmed_disagreement_flags_a_supported_claim(self):
        c = claim(verdict="supported")
        llm = FakeLLM([False, False, False])
        summary = second_opinion.run([c], llm, workers=1)
        self.assertEqual(llm.calls, 3)          # 1 + 2 confirmations
        so = c["second_opinion"]
        self.assertFalse(so["agrees"])
        self.assertEqual(so["verdict"], "unsupported")
        self.assertEqual(so["votes"], "3-0")
        self.assertEqual(summary["fp_flags"], ["t1"])
        self.assertEqual(summary["strict_flags"], [])

    def test_lone_dissent_is_overruled_by_the_confirmation_votes(self):
        c = claim(verdict="supported")
        llm = FakeLLM([False, True, True])
        second_opinion.run([c], llm, workers=1)
        so = c["second_opinion"]
        self.assertTrue(so["agrees"])           # 2 of 3 agreed with the verdict
        self.assertEqual(so["verdict"], "supported")
        self.assertEqual(so["votes"], "2-1")

    def test_too_strict_direction_flags_an_unsupported_claim(self):
        c = claim(verdict="unsupported")
        llm = FakeLLM([True, True, True])
        summary = second_opinion.run([c], llm, workers=1)
        self.assertFalse(c["second_opinion"]["agrees"])
        self.assertEqual(c["second_opinion"]["verdict"], "supported")
        self.assertEqual(summary["strict_flags"], ["t1"])
        self.assertEqual(summary["fp_flags"], [])

    def test_same_model_opinion_is_reused_without_calls(self):
        c = claim(verdict="supported",
                  second_opinion={"model": "gemini/gemini-2.5-flash",
                                  "verdict": "supported", "agrees": True,
                                  "reason": "prior", "votes": None})
        llm = FakeLLM([False])
        summary = second_opinion.run([c], llm, workers=1)
        self.assertEqual(llm.calls, 0)
        self.assertEqual(summary["reused"], 1)
        self.assertTrue(c["second_opinion"]["agrees"])   # untouched

    def test_different_model_opinion_is_rechecked(self):
        c = claim(verdict="supported",
                  second_opinion={"model": "deepseek/deepseek-chat",
                                  "verdict": "supported", "agrees": True})
        llm = FakeLLM([True])
        second_opinion.run([c], llm, workers=1)
        self.assertEqual(llm.calls, 1)
        self.assertEqual(c["second_opinion"]["model"], "gemini/gemini-2.5-flash")

    def test_judge_reads_the_labeled_window_not_the_bare_sentence(self):
        c = claim(verdict="supported")
        llm = FakeLLM([True])
        second_opinion.run([c], llm, workers=1)
        p = llm.prompts[0]
        self.assertIn("My claim about X.", p)
        self.assertIn("From Source A: Before. The source states X. After.", p)


class TestFeedback(unittest.TestCase):

    def test_matching_id_and_text_gets_the_owner_flag(self):
        c = claim("t37")
        n = second_opinion.annotate_feedback(
            [c], [{"claim_id": "t37", "text": "My claim about X.",
                   "author_says": "wrong", "note": "half is absent"}])
        self.assertEqual(n, 1)
        self.assertEqual(c["owner_flag"]["note"], "half is absent")

    def test_rewritten_claim_retires_the_stale_dispute(self):
        c = claim("t37", text="A completely rewritten sentence.")
        c["text"] = "A completely rewritten sentence."
        n = second_opinion.annotate_feedback(
            [c], [{"claim_id": "t37", "text": "My claim about X.", "author_says": "wrong"}])
        self.assertEqual(n, 0)
        self.assertNotIn("owner_flag", c)

    def test_stale_flags_from_reused_claims_are_cleared(self):
        c = claim("t5")
        c["owner_flag"] = {"author_says": "wrong"}    # carried in from a prev run
        second_opinion.annotate_feedback([c], [])
        self.assertNotIn("owner_flag", c)

    def test_missing_feedback_file_is_empty_list(self):
        self.assertEqual(second_opinion.load_feedback(tempfile.mkdtemp()), [])


class TestViewerFlags(unittest.TestCase):

    def _page(self, extra=None):
        c = claim("t1", verdict="supported")
        if extra:
            c.update(extra)
        analysis = {"text_claims": [c],
                    "sources": [{"paper_id": "p1", "key": "a", "filename": "a.txt",
                                 "title": "Source A"}],
                    "coverage": {"totals": {"claims": 1, "supported": 1,
                                            "unsupported": 0, "own": 0, "omitted": 0}},
                    "metadata": {}, "omitted": []}
        out = os.path.join(tempfile.mkdtemp(), "v.html")
        viewer.generate(analysis, out)
        with open(out, encoding="utf-8") as f:
            return f.read()

    def test_disagreement_renders_chip_note_low_confidence_and_header_count(self):
        page = self._page({"second_opinion": {
            "model": "gemini/gemini-2.5-flash", "verdict": "unsupported",
            "agrees": False, "reason": "the passage lacks half the claim",
            "votes": "3-0"}})
        self.assertIn("2nd opinion disagrees", page)
        self.assertIn("false-positive risk", page)
        self.assertIn("the passage lacks half the claim", page)
        self.assertIn("low confidence", page)
        self.assertIn("1 second-opinion flag", page)

    def test_agreement_renders_nothing(self):
        page = self._page({"second_opinion": {
            "model": "gemini/gemini-2.5-flash", "verdict": "supported",
            "agrees": True, "reason": "fine", "votes": None}})
        self.assertNotIn("2nd opinion", _no_legend(page))
        self.assertNotIn("second-opinion flag", page)

    def test_owner_flag_renders_author_disputed_chip(self):
        page = self._page({"owner_flag": {"author_says": "wrong",
                                          "note": "taxation half only",
                                          "timestamp": "2026-07-04"}})
        self.assertIn("author disputed", page)
        self.assertIn("taxation half only", page)


if __name__ == "__main__":
    unittest.main()
