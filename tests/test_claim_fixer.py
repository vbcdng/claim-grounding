"""--fix-claim tests — stub LLM, no API/network calls.

Run:  venv/bin/python3 -m unittest tests.test_claim_fixer -v
"""
import os
import sys
import json
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.papertrail import claim_fixer, viewer


def make_analysis():
    return {
        "text_claims": [
            {"id": "t1", "text": "The programme eliminated sick days entirely.",
             "markers": ["a"], "paper_ids": ["pid1"], "verdict": "unsupported",
             "reason": "overstated",
             "evidences": [{"paper_id": "pid1", "source_title": "Study A",
                            "supported": False, "sentence": "23 percent fewer sick days.",
                            "page": 1, "snippet": "23 percent"}]},
            {"id": "t2", "text": "Uncited filler.", "markers": [], "paper_ids": [],
             "verdict": "unsupported", "reason": "no_citation_marker", "evidences": []},
        ],
        "sources": [{"paper_id": "pid1", "key": "a", "filename": "a.txt",
                     "title": "Study A", "num_claims": 1}],
        "coverage": {"totals": {"supported": 0, "unsupported": 2, "omitted": 0}},
        "metadata": {"model": "gemini/gemini-2.5-flash-lite",
                     "output_dir": "/abs/out", "text_file": "/abs/my.md"},
        "omitted": [],
    }


def make_sources():
    return {"pid1": {
        "title": "Study A",
        "sentences": [
            {"text": "Participants who completed the programme recorded 23 percent "
                     "fewer sick days than the control group.", "page": 1}],
        "claims": [],
    }}


class TestFixClaim(unittest.TestCase):

    def test_fix_attaches_verified_suggestion(self):
        analysis, sources = make_analysis(), make_sources()
        llm = MagicMock()
        # the rewritten text appears only in the verification judge's prompt;
        # every other call is evidence extraction and returns the real sentence
        llm.call.side_effect = lambda p, **kw: (
            json.dumps({"supported": True, "reason": "the figure matches"})
            if "among completers" in p
            else json.dumps({"sentences": ["Participants who completed the programme "
                                           "recorded 23 percent fewer sick days than "
                                           "the control group."]}))
        llm.call_json.return_value = {
            "rewritten": "The programme cut sick days by 23 percent among completers.",
            "changes": "Replaced the overstatement with the study's 23% figure."}
        sug = claim_fixer.fix_claim(analysis, sources, llm, "t1")
        self.assertTrue(sug["verified_supported"])
        self.assertIn("23 percent", sug["text"])
        tc = analysis["text_claims"][0]
        self.assertIs(tc["fix_suggestion"], sug)
        self.assertTrue(sug["passages"])           # grounded in real source text

    def test_unknown_claim_id_raises(self):
        with self.assertRaises(ValueError):
            claim_fixer.fix_claim(make_analysis(), make_sources(), MagicMock(), "t99")

    def test_uncited_claim_raises(self):
        with self.assertRaises(ValueError):
            claim_fixer.fix_claim(make_analysis(), make_sources(), MagicMock(), "t2")

    def test_bad_rewrite_json_raises(self):
        llm = MagicMock()
        llm.call.return_value = json.dumps(
            {"sentences": ["Participants who completed the programme recorded 23 "
                           "percent fewer sick days than the control group."]})
        llm.call_json.return_value = None
        with self.assertRaises(RuntimeError):
            claim_fixer.fix_claim(make_analysis(), make_sources(), llm, "t1")


class TestViewerFixSection(unittest.TestCase):

    def test_command_shown_for_judged_unsupported(self):
        import tempfile
        analysis = make_analysis()
        out = os.path.join(tempfile.mkdtemp(), "viewer.html")
        viewer.generate(analysis, out)
        page = open(out, encoding="utf-8").read()
        self.assertIn("--fix-claim t1", page)
        self.assertIn("--output-dir /abs/out", page)
        self.assertIn("--model gemini/gemini-2.5-flash-lite", page)
        self.assertNotIn("--fix-claim t2", page)   # uncited claim: nothing to fix against

    def test_suggestion_rendered_when_present(self):
        import tempfile
        analysis = make_analysis()
        analysis["text_claims"][0]["fix_suggestion"] = {
            "text": "The programme cut sick days by 23 percent.",
            "changes": "Replaced the overstatement.",
            "verified_supported": True, "verify_reason": "ok", "passages": ["From A: x"]}
        out = os.path.join(tempfile.mkdtemp(), "viewer.html")
        viewer.generate(analysis, out)
        page = open(out, encoding="utf-8").read()
        self.assertIn("Suggested fix", page)
        self.assertIn("The programme cut sick days by 23 percent.", page)
        self.assertIn("re-checked: supported", page)
        self.assertNotIn("--fix-claim t1", page)   # command replaced by the suggestion


if __name__ == "__main__":
    unittest.main()
