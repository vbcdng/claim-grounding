"""Own-claim split (structural / opinion / fact, module own_claims) — no API calls.

Run:  venv/bin/python3 -m unittest tests.test_own_split
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.papertrail import own_claims, viewer


def _no_legend(page):
    """Drop the static 'How to read this' legend so chip-label sentinels match
    only real card chips, not the legend swatches (viewer.py legend, 2026-07-06)."""
    pre, _, rest = page.partition('<details class="legend">')
    _, _, post = rest.partition('</details>')
    return pre + post


class FakeLLM:
    """Returns canned classification JSONs in order (last repeats); counts calls."""

    def __init__(self, kinds, model="gemini/gemini-2.5-flash-lite"):
        self.model = model
        self.kinds = list(kinds)
        self.calls = 0
        self.prompts = []

    def call(self, prompt, **kw):
        self.prompts.append(prompt)
        k = self.kinds[min(self.calls, len(self.kinds) - 1)]
        self.calls += 1
        if k == "garbage":
            return "not json at all"
        return json.dumps({"kind": k, "reason": f"looks {k}"})


def own(cid="t1", text="AI labs spent $40B on data centers in 2025."):
    return {"id": cid, "text": text, "markers": [], "verdict": "own",
            "method": "uncited", "reason": "no citation marker", "evidences": []}


class TestClassify(unittest.TestCase):

    def test_only_own_claims_are_classified(self):
        cited = {"id": "t0", "text": "Cited.", "markers": ["a"], "verdict": "supported",
                 "evidences": []}
        c = own("t1")
        llm = FakeLLM(["fact"])
        s = own_claims.classify([cited, c], llm, workers=1)
        self.assertEqual(llm.calls, 1)
        self.assertNotIn("own_kind", cited)
        self.assertEqual(c["own_kind"]["kind"], "fact")
        self.assertEqual(s["checked"], 1)
        self.assertEqual(s["fact_ids"], ["t1"])

    def test_counts_cover_all_three_kinds(self):
        cs = [own("t1"), own("t2", "**What could prevent this?**"),
              own("t3", "I believe this is the decisive factor.")]
        s = own_claims.classify(cs, FakeLLM(["fact", "structural", "opinion"]), workers=1)
        self.assertEqual(s["counts"], {"structural": 1, "opinion": 1, "fact": 1})
        self.assertEqual(s["fact_ids"], ["t1"])

    def _current_sha(self):
        from modules.papertrail import matcher
        return own_claims._prompt_sha(matcher._load_prompt(own_claims.PROMPT_FILE))

    def test_same_model_and_prompt_tag_is_reused_without_calls(self):
        c = own("t1")
        c["own_kind"] = {"kind": "opinion", "reason": "prior",
                         "model": "gemini/gemini-2.5-flash-lite",
                         "prompt_sha": self._current_sha()}
        llm = FakeLLM(["fact"])
        s = own_claims.classify([c], llm, workers=1)
        self.assertEqual(llm.calls, 0)
        self.assertEqual(s["reused"], 1)
        self.assertEqual(c["own_kind"]["kind"], "opinion")   # untouched

    def test_different_model_tag_is_rebought(self):
        c = own("t1")
        c["own_kind"] = {"kind": "opinion", "reason": "prior", "model": "other/model",
                         "prompt_sha": self._current_sha()}
        llm = FakeLLM(["fact"])
        own_claims.classify([c], llm, workers=1)
        self.assertEqual(llm.calls, 1)
        self.assertEqual(c["own_kind"]["kind"], "fact")

    def test_changed_prompt_rebuys_the_tag(self):
        c = own("t1")
        c["own_kind"] = {"kind": "opinion", "reason": "prior",
                         "model": "gemini/gemini-2.5-flash-lite",
                         "prompt_sha": "0ld5ha00"}
        llm = FakeLLM(["fact"])
        own_claims.classify([c], llm, workers=1)
        self.assertEqual(llm.calls, 1)
        self.assertEqual(c["own_kind"]["kind"], "fact")
        self.assertEqual(c["own_kind"]["prompt_sha"], self._current_sha())

    def test_unparseable_response_leaves_the_claim_untagged(self):
        c = own("t1")
        s = own_claims.classify([c], FakeLLM(["garbage"]), workers=1)
        self.assertNotIn("own_kind", c)
        self.assertEqual(s["unparsed"], 1)
        self.assertEqual(s["counts"]["fact"], 0)

    def test_prompt_contains_the_claim_text(self):
        c = own("t1", "A very specific uncited assertion.")
        llm = FakeLLM(["opinion"])
        own_claims.classify([c], llm, workers=1)
        self.assertIn("A very specific uncited assertion.", llm.prompts[0])

    def test_parse_survives_truncated_json(self):
        kind, reason = own_claims._parse_kind('{"kind": "fact", "reason": "cut off he')
        self.assertEqual(kind, "fact")
        self.assertEqual(reason, "cut off he")


class TestViewer(unittest.TestCase):

    def _page(self, own_kind=None):
        c = own("t1")
        if own_kind:
            c["own_kind"] = own_kind
        analysis = {"text_claims": [c], "sources": [],
                    "coverage": {"totals": {"claims": 1, "supported": 0,
                                            "unsupported": 0, "own": 1, "omitted": 0}},
                    "metadata": {}, "omitted": []}
        out = os.path.join(tempfile.mkdtemp(), "v.html")
        viewer.generate(analysis, out)
        with open(out, encoding="utf-8") as f:
            return f.read()

    def test_fact_renders_chip_note_filter_and_header_count(self):
        page = self._page({"kind": "fact", "reason": "a checkable spending figure",
                           "model": "m"})
        self.assertIn("citation needed?", page)
        self.assertIn("a checkable spending figure", page)
        self.assertIn('class="card own citeneeded"', page)
        self.assertIn("Citation needed (1)", page)          # filter button
        self.assertIn("1 citation suggestion", page)        # header total

    def test_structural_renders_a_muted_kind_chip_only(self):
        page = self._page({"kind": "structural", "reason": "a heading", "model": "m"})
        self.assertIn("kindchip", page)
        self.assertNotIn("citation needed?", _no_legend(page))
        self.assertNotIn("Citation needed (", page)

    def test_untagged_own_claim_renders_no_kind_chips(self):
        page = self._page(None)
        self.assertNotIn('<span class="kindchip"', page)
        self.assertNotIn("citation needed?", _no_legend(page))

    def test_review_data_embeds_the_kind(self):
        page = self._page({"kind": "fact", "reason": "r", "model": "m"})
        self.assertIn('"own_kind": {"kind": "fact"', page)


if __name__ == "__main__":
    unittest.main()
