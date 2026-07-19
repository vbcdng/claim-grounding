"""Human-review layer tests: vote tallies, judged-passage persistence, and
never-empty evidence. Stub LLM, no API calls.

Run:  venv/bin/python3 -m unittest tests.test_review_layer -v
"""
import os
import sys
import json
import tempfile
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.papertrail import matcher, viewer


class TestVoteTally(unittest.TestCase):

    def test_unanimous_tally(self):
        llm = MagicMock()
        llm.call.return_value = json.dumps({"supported": True, "reason": "ok"})
        ok, reason, tally = matcher._vote_support(llm, "JG")
        self.assertTrue(ok)
        self.assertEqual(tally, "2-0")            # first two agree -> early exit
        self.assertEqual(llm.call.call_count, 2)

    def test_split_tally(self):
        llm = MagicMock()
        llm.call.side_effect = [
            json.dumps({"supported": True, "reason": "yes"}),
            json.dumps({"supported": False, "reason": "no"}),
            json.dumps({"supported": False, "reason": "no again"}),
        ]
        ok, reason, tally = matcher._vote_support(llm, "JG")
        self.assertFalse(ok)
        self.assertEqual(tally, "2-1")            # the borderline signal
        self.assertEqual(llm.call.call_count, 3)

    def test_extract_evidence_records_votes_and_window(self):
        sent = "A single meaningful sentence about the actual topic of interest here."
        src = {"title": "S", "sentences": [{"text": sent, "page": 1}]}
        llm = MagicMock()
        llm.call.side_effect = [
            json.dumps({"sentences": [sent]}),
            json.dumps({"supported": False, "reason": "not quite"}),
            json.dumps({"supported": True, "reason": "actually yes"}),
            json.dumps({"supported": False, "reason": "tie-break: no"}),
        ]
        e = matcher._extract_evidence("claim", "p1", src, llm,
                                      "EX {CLAIM} {SOURCE}", "JG {CLAIM} {PASSAGE}")
        self.assertFalse(e["supported"])
        self.assertEqual(e["votes"], "2-1")
        self.assertTrue(e["window"])


class TestNeverEmptyEvidence(unittest.TestCase):

    def test_candidate_sentence_kept_when_extraction_empty(self):
        """t31 class: extraction finds nothing -> the card must still carry the
        candidate stage's closest sentence for the human to read."""
        closest = "The policy restricts political campaign and lobbying use of the models."
        sents = [{"text": closest, "page": 2},
                 {"text": "Another unrelated policy line about something different.", "page": 2}]
        sources = {"p1": {"title": "Policy", "sentences": sents, "claims": []}}
        tc = {"id": "t1", "text": "The usage policy forbids political campaigning.",
              "markers": ["a"], "paper_ids": ["p1"]}
        llm = MagicMock()
        llm.call.side_effect = lambda p, **kw: (
            json.dumps({"supported": False, "reason": "no"}) if p.startswith("JG") or "JG" in p[:5]
            else json.dumps({"sentences": []}))          # extraction always empty
        analysis = matcher.run([tc], sources, llm)
        c = analysis["text_claims"][0]
        self.assertEqual(c["verdict"], "unsupported")
        ev_sentences = [e.get("sentence") for e in c["evidences"]]
        self.assertIn(closest, ev_sentences)             # not an empty card


class TestViewerReviewLayer(unittest.TestCase):

    def _analysis(self, votes=None, window=None):
        e = {"paper_id": "p1", "source_title": "S", "supported": False,
             "sentence": "The closest sentence found in the source document text.",
             "page": 1, "snippet": "closest"}
        if votes:
            e["votes"] = votes
        if window:
            e["window"] = window
        return {
            "text_claims": [{"id": "t1", "text": "Claim.", "markers": ["a"],
                             "paper_ids": ["p1"], "verdict": "unsupported",
                             "reason": "not stated", "evidences": [e], "evidence": e}],
            "sources": [{"paper_id": "p1", "key": "a", "filename": "a.txt", "title": "S"}],
            "coverage": {"totals": {"supported": 0, "unsupported": 1, "omitted": 0}},
            "metadata": {}, "omitted": [],
        }

    def _render(self, analysis):
        out = os.path.join(tempfile.mkdtemp(), "v.html")
        viewer.generate(analysis, out)
        with open(out, encoding="utf-8") as f:
            return f.read()

    def test_borderline_banner_on_split_vote(self):
        page = self._render(self._analysis(votes="2-1"))
        self.assertIn("Borderline", page)
        self.assertIn("2–1", page)

    def test_no_banner_on_unanimous(self):
        page = self._render(self._analysis(votes="2-0"))
        self.assertNotIn("Borderline", page)

    def test_judged_passage_rendered(self):
        window = ("The closest sentence found in the source document text. Plus a "
                  "second extracted sentence the judge also read before deciding.")
        page = self._render(self._analysis(window=window))
        self.assertIn("what the judge read", page)   # "Context — what the judge read"
        self.assertIn("second extracted sentence", page)

    def test_old_analyses_render_without_new_fields(self):
        page = self._render(self._analysis())        # no votes, no window
        self.assertNotIn("what the judge read", page)
        self.assertNotIn("Borderline", page)


if __name__ == "__main__":
    unittest.main()
