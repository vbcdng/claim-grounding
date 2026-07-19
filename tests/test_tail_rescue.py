"""Tail rescue: a failed multi-sentence cited claim gets its marker-adjacent
tail re-judged alone; a supported tail rescues the claim and the uncited
lead-in is labeled as the author's own. No API calls.

Run:  venv/bin/python3 -m unittest tests.test_tail_rescue -v
"""
import os
import sys
import json
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.papertrail import matcher, viewer

LEAD1 = "Alpha framing sentence one is entirely the author's own."
LEAD2 = "Beta transition sentence two continues the author's own argument."
TAIL = "The bridge is exactly four hundred meters long."


def _fake_cosine(a, b, **kw):
    # on-topic but not AUTO_SUPPORT, so every candidate goes to the judge
    return [[0.8] * len(b) for _ in a]


def _run(claims, sources, llm):
    with patch.object(matcher.embeddings, "cosine_matrix", side_effect=_fake_cosine):
        return matcher.run(claims, sources, llm)


def _llm(judge_fn):
    """Judgment calls answer via judge_fn(prompt); extraction always finds nothing
    (keeps the whole-claim fallback failing without extra scripting)."""
    llm = MagicMock()

    def call(p, **kw):
        if "evidence finder" in p:
            return json.dumps({"sentences": []})
        return json.dumps(judge_fn(p))

    llm.call.side_effect = call
    return llm


def _sources():
    return {"p1": {"title": "Bridge Survey", "key": "a",
                   "sentences": [{"text": "The surveyed bridge measures 400 m in length.",
                                  "page": 3}],
                   "claims": []}}


class TestTailRescue(unittest.TestCase):

    def test_supported_tail_rescues_failed_claim(self):
        tc = {"id": "t7", "text": f"{LEAD1} {LEAD2} {TAIL}",
              "markers": ["a"], "paper_ids": ["p1"]}
        # judge: reject anything containing the lead-in, accept the bare tail
        llm = _llm(lambda p: {"supported": False, "reason": "lead-in absent"}
                   if LEAD1 in p else {"supported": True, "reason": "tail stated"})
        analysis = _run([tc], _sources(), llm)
        c = analysis["text_claims"][0]
        self.assertEqual(c["verdict"], "supported")
        self.assertEqual(c["method"], "tail_rescue")
        self.assertEqual(c["id"], "t7")                    # ids never shift
        self.assertEqual(c["tail_rescue"]["reach"], 1)
        self.assertEqual(c["tail_rescue"]["tail"], TAIL)
        self.assertEqual(c["tail_rescue"]["lead_in"], f"{LEAD1} {LEAD2}")
        self.assertTrue(c["evidences"][0]["supported"])    # evidences are the tail's
        self.assertEqual(analysis["coverage"]["totals"]["supported"], 1)
        self.assertEqual(analysis["coverage"]["totals"]["unsupported"], 0)

    def test_reach_extends_to_two_sentences(self):
        tc = {"id": "t1", "text": f"{LEAD1} {LEAD2} {TAIL}",
              "markers": ["a"], "paper_ids": ["p1"]}
        # only the LAST TWO sentences together pass; tail-1 alone fails
        llm = _llm(lambda p: {"supported": True, "reason": "both stated"}
                   if (LEAD1 not in p and LEAD2 in p and TAIL in p)
                   else {"supported": False, "reason": "no"})
        c = _run([tc], _sources(), llm)["text_claims"][0]
        self.assertEqual(c["verdict"], "supported")
        self.assertEqual(c["tail_rescue"]["reach"], 2)
        self.assertEqual(c["tail_rescue"]["lead_in"], LEAD1)
        self.assertEqual(c["tail_rescue"]["tail"], f"{LEAD2} {TAIL}")

    def test_all_suffixes_fail_keeps_original_verdict(self):
        tc = {"id": "t1", "text": f"{LEAD1} {LEAD2} {TAIL}",
              "markers": ["a"], "paper_ids": ["p1"]}
        llm = _llm(lambda p: {"supported": False, "reason": "never"})
        c = _run([tc], _sources(), llm)["text_claims"][0]
        self.assertEqual(c["verdict"], "unsupported")
        self.assertNotEqual(c["method"], "tail_rescue")
        self.assertEqual(c["tail_rescue"], {"supported": False, "tried": [1, 2]})
        self.assertTrue(c["evidences"])                    # card still has evidence

    def test_single_sentence_claim_gets_no_rescue(self):
        tc = {"id": "t1", "text": TAIL, "markers": ["a"], "paper_ids": ["p1"]}
        llm = _llm(lambda p: {"supported": False, "reason": "no"})
        c = _run([tc], _sources(), llm)["text_claims"][0]
        self.assertEqual(c["verdict"], "unsupported")
        self.assertNotIn("tail_rescue", c)

    def test_supported_claim_untouched(self):
        tc = {"id": "t1", "text": f"{LEAD1} {TAIL}", "markers": ["a"], "paper_ids": ["p1"]}
        llm = _llm(lambda p: {"supported": True, "reason": "fine as a whole"})
        c = _run([tc], _sources(), llm)["text_claims"][0]
        self.assertEqual(c["verdict"], "supported")
        self.assertEqual(c["method"], "llm")
        self.assertNotIn("tail_rescue", c)

    def test_own_and_missing_file_claims_get_no_rescue(self):
        own = {"id": "t1", "text": f"{LEAD1} {TAIL}", "markers": [], "paper_ids": []}
        missing = {"id": "t2", "text": f"{LEAD1} {TAIL}", "markers": ["a"],
                   "paper_ids": [], "missing_files": ["a.pdf"]}
        analysis = _run([own, missing], {}, MagicMock())
        self.assertEqual(analysis["text_claims"][0]["verdict"], "own")
        self.assertNotIn("tail_rescue", analysis["text_claims"][0])
        self.assertIn("source_file_missing", analysis["text_claims"][1]["reason"])
        self.assertNotIn("tail_rescue", analysis["text_claims"][1])


class TestViewerLeadIn(unittest.TestCase):

    def _analysis(self):
        e = {"paper_id": "p1", "source_title": "Bridge Survey", "supported": True,
             "sentence": "The surveyed bridge measures 400 m in length.",
             "page": 3, "snippet": "The surveyed bridge"}
        return {
            "text_claims": [{"id": "t7", "text": f"{LEAD1} {TAIL}",
                             "markers": ["a"], "paper_ids": ["p1"],
                             "verdict": "supported", "method": "tail_rescue",
                             "reason": "tail stated", "evidence": e, "evidences": [e],
                             "tail_rescue": {"supported": True, "reach": 1,
                                             "lead_in": LEAD1, "tail": TAIL}}],
            "sources": [{"paper_id": "p1", "key": "a", "filename": "a.txt",
                         "title": "Bridge Survey"}],
            "coverage": {"totals": {"claims": 1, "supported": 1, "unsupported": 0,
                                    "own": 0, "omitted": 0}},
            "metadata": {}, "omitted": [],
        }

    def _render(self):
        out = os.path.join(tempfile.mkdtemp(), "v.html")
        viewer.generate(self._analysis(), out)
        with open(out, encoding="utf-8") as f:
            return f.read()

    def test_card_shows_leadin_span_note_and_chip(self):
        page = self._render()
        self.assertIn(f'<span class="leadin">{viewer._esc(LEAD1)}</span>', page)
        self.assertIn("your own lead-in", page)                 # the leadin-note
        self.assertIn('<span class="leadin-chip">lead-in</span>', page)

    def test_left_column_splits_into_own_and_supported_spans(self):
        page = self._render()
        self.assertIn('class="claim own" data-card="card-t7"', page)   # lead-in span
        self.assertIn('id="text-t7"', page)                            # tail keeps the anchor
        # the lead-in must NOT sit inside the green supported span
        supported_span = page.split('id="text-t7"')[1][:400]
        self.assertIn(TAIL, supported_span)
        self.assertNotIn(viewer._esc(LEAD1), supported_span)

    def test_plain_supported_card_unchanged(self):
        a = self._analysis()
        c = a["text_claims"][0]
        del c["tail_rescue"]; c["method"] = "llm"
        out = os.path.join(tempfile.mkdtemp(), "v.html")
        viewer.generate(a, out)
        with open(out, encoding="utf-8") as f:
            page = f.read()
        self.assertNotIn("leadin-chip", page.split("<style")[0])   # no chip markup in body
        self.assertNotIn('<span class="leadin">', page)
        self.assertNotIn("your own lead-in", page)


if __name__ == "__main__":
    unittest.main()
