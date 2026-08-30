"""Round-3 missing-parts tests (2026-08-11): the structured list is produced
by ONE follow-up call AFTER the final verdict, never during judging — the
round-2 in-prompt demand tipped a borderline verdict (gate row paper1/t17).
Stub LLM, no API calls.

Run:  venv/bin/python3 -m unittest tests.test_missing_parts_followup -v
"""
import os
import sys
import json
import tempfile
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.papertrail import matcher


class TestFollowupFunction(unittest.TestCase):

    WINDOWS = [("Gadget Weekly", "The device runs for 12 hours on a single charge.")]

    def test_returns_coerced_list_and_ignores_extra_keys(self):
        llm = MagicMock()
        llm.call.return_value = json.dumps({
            "missing_parts": ["the device is waterproof to 30 meters",
                              "the device is waterproof to 30 meters"],
            "background_gaps": ["a judging-prompt field this parser must ignore"]})
        parts = matcher._missing_parts_followup(
            "claim", self.WINDOWS, "waterproofing never mentioned", llm,
            "MISSING {CLAIM} {PASSAGE} {REASON}")
        self.assertEqual(parts, ["the device is waterproof to 30 meters"])
        self.assertEqual(llm.call.call_count, 1)
        self.assertEqual(llm.call.call_args.kwargs.get("purpose"), "missing_parts")
        sent = llm.call.call_args.args[0]
        self.assertIn("From Gadget Weekly:", sent)
        self.assertIn("waterproofing never mentioned", sent)

    def test_unusable_replies_return_none(self):
        for reply in (None, "", "no json here",
                      json.dumps({"missing_parts": "a string"}),
                      json.dumps({"missing_parts": []}),
                      json.dumps(["a bare list"])):
            llm = MagicMock()
            llm.call.return_value = reply
            self.assertIsNone(matcher._missing_parts_followup(
                "claim", self.WINDOWS, "r", llm,
                "M {CLAIM} {PASSAGE} {REASON}"), reply)


class TestRunWiring(unittest.TestCase):
    """run(): an unsupported claim pays exactly one follow-up call; a supported
    claim never pays it. Prompts are swapped in via PROMPT_OVERRIDES (the same
    seam the gate's PROMPTS= mode uses), so each stage is recognizable."""

    SENT = "The device runs for 12 hours on a single charge."

    PROMPTS = {
        "pt_support_judgment_prompt.txt": "JUDGE {CLAIM} {PASSAGE}",
        "pt_extract_evidence_prompt.txt": "EXTRACT {CLAIM} {SOURCE}",
        "pt_combined_judgment_prompt.txt": "COMBINED {CLAIM} {PASSAGE}",
        "pt_covering_set_prompt.txt": "COVER {CLAIM} {SENTENCES}",
        "pt_pick_verify_prompt.txt": "PICK {CLAIM} {PASSAGE}",
        "pt_component_split_prompt.txt": "SPLIT {CLAIM}",
        "pt_missing_parts_prompt.txt": "MISSING {CLAIM} {PASSAGE} {REASON}",
    }

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._saved = dict(matcher.PROMPT_OVERRIDES)
        for name, content in self.PROMPTS.items():
            path = os.path.join(self._tmp, name)
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            matcher.PROMPT_OVERRIDES[name] = path

    def tearDown(self):
        matcher.PROMPT_OVERRIDES.clear()
        matcher.PROMPT_OVERRIDES.update(self._saved)

    def _sources(self):
        return {"p1": {"title": "Gadget Weekly", "key": "gadget2026",
                       "sentences": [{"text": self.SENT, "page": 1}],
                       "claims": []}}

    def _claim(self):
        return {"id": "t1", "text": "The device is waterproof to 30 meters.",
                "markers": ["a"], "paper_ids": ["p1"]}

    def test_unsupported_claim_gets_list_from_one_followup_call(self):
        def call(p, **kw):
            if p.startswith("EXTRACT"):
                return json.dumps({"sentences": [self.SENT]})
            if p.startswith("MISSING"):
                return json.dumps(
                    {"missing_parts": ["the device is waterproof to 30 meters"],
                     "background_gaps": ["must be ignored"]})
            return json.dumps({"supported": False,
                               "reason": "waterproofing is never mentioned"})
        llm = MagicMock()
        llm.call.side_effect = call
        analysis = matcher.run([self._claim()], self._sources(), llm)
        c = analysis["text_claims"][0]
        self.assertEqual(c["verdict"], "unsupported")
        self.assertEqual(c.get("judge_missing_parts"),
                         ["the device is waterproof to 30 meters"])
        followups = [a.args[0] for a in llm.call.call_args_list
                     if a.args[0].startswith("MISSING")]
        self.assertEqual(len(followups), 1)

    def test_supported_claim_never_pays_the_followup(self):
        def call(p, **kw):
            if p.startswith("EXTRACT"):
                return json.dumps({"sentences": [self.SENT]})
            if p.startswith("MISSING"):
                raise AssertionError("follow-up ran on a supported claim")
            if p.startswith("COVER") or p.startswith("PICK"):
                return json.dumps({})
            return json.dumps({"supported": True, "reason": "stated"})
        llm = MagicMock()
        llm.call.side_effect = call
        analysis = matcher.run([self._claim()], self._sources(), llm)
        c = analysis["text_claims"][0]
        self.assertEqual(c["verdict"], "supported")
        self.assertNotIn("judge_missing_parts", c)


if __name__ == "__main__":
    unittest.main()
