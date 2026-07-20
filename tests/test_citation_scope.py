"""Citation-scope tests — eligibility, classification, reuse, safety, viewer.

All offline: the LLM is a fake. Run:
  venv/bin/python3 -m unittest tests.test_citation_scope -v
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.papertrail import citation_scope, viewer


class FakeLLM:
    def __init__(self, response):
        self.model = "fake/judge"
        self.response = response
        self.calls = 0

    def call(self, prompt, **kw):
        self.calls += 1
        self.last_prompt = prompt
        return self.response


def resp(scope, assertion="follows X's power-analysis guidance", reason="own study"):
    return json.dumps({"scope": scope, "scoped_assertion": assertion,
                       "reason": reason})


def claim(cid="t1", verdict="unsupported", **kw):
    c = {"id": cid,
         "text": "We estimated a sample of 318 at 80 percent power per the guidelines.",
         "verdict": verdict, "markers": ["asendorpf2013"], "paper_ids": ["p1"],
         "evidences": []}
    c.update(kw)
    return c


class TestEligible(unittest.TestCase):
    def test_unsupported_cited_is_eligible(self):
        self.assertTrue(citation_scope.eligible(claim()))

    def test_supported_is_not(self):
        self.assertFalse(citation_scope.eligible(claim(verdict="supported")))

    def test_own_uncited_is_not(self):
        self.assertFalse(citation_scope.eligible(claim(verdict="own", markers=[])))

    def test_missing_file_is_not(self):
        self.assertFalse(citation_scope.eligible(
            claim(reason="source_file_missing: asendorpf2013.pdf")))

    def test_author_ruled_is_not(self):
        self.assertFalse(citation_scope.eligible(
            claim(owner_flag={"author_says": "wrong"})))


class TestClassify(unittest.TestCase):
    def test_tags_scoped_methods(self):
        c = claim()
        llm = FakeLLM(resp("methods"))
        s = citation_scope.classify([c], llm, workers=1)
        self.assertEqual(c["citation_scope"]["scope"], "methods")
        self.assertEqual(c["citation_scope"]["scoped_assertion"],
                         "follows X's power-analysis guidance")
        self.assertEqual(s["counts"]["methods"], 1)
        self.assertEqual(s["scoped_ids"], ["t1"])
        self.assertIn("asendorpf2013", llm.last_prompt)

    def test_full_is_tagged_but_not_scoped(self):
        c = claim()
        s = citation_scope.classify([c], FakeLLM(resp("full", "", "attribution")),
                                    workers=1)
        self.assertEqual(c["citation_scope"]["scope"], "full")
        self.assertEqual(s["scoped_ids"], [])

    def test_supported_claim_never_called(self):
        c = claim(verdict="supported")
        llm = FakeLLM(resp("methods"))
        s = citation_scope.classify([c], llm, workers=1)
        self.assertEqual((llm.calls, s["checked"]), (0, 0))
        self.assertNotIn("citation_scope", c)

    def test_unparseable_leaves_no_tag(self):
        c = claim()
        s = citation_scope.classify([c], FakeLLM("not json at all"), workers=1)
        self.assertNotIn("citation_scope", c)
        self.assertEqual(s["unparsed"], 1)

    def test_invalid_scope_value_leaves_no_tag(self):
        c = claim()
        citation_scope.classify([c], FakeLLM(resp("sideways")), workers=1)
        self.assertNotIn("citation_scope", c)

    def test_same_model_same_prompt_reused(self):
        c = claim()
        llm = FakeLLM(resp("methods"))
        citation_scope.classify([c], llm, workers=1)
        s2 = citation_scope.classify([c], llm, workers=1)
        self.assertEqual((llm.calls, s2["reused"]), (1, 1))

    def test_model_change_rebuys(self):
        c = claim()
        citation_scope.classify([c], FakeLLM(resp("methods")), workers=1)
        llm2 = FakeLLM(resp("related"))
        llm2.model = "other/model"
        citation_scope.classify([c], llm2, workers=1)
        self.assertEqual(c["citation_scope"]["scope"], "related")

    def test_stale_tag_dropped_when_verdict_flips(self):
        c = claim()
        citation_scope.classify([c], FakeLLM(resp("methods")), workers=1)
        c["verdict"] = "supported"       # e.g. component rescue on a re-run
        citation_scope.classify([c], FakeLLM(resp("methods")), workers=1)
        self.assertNotIn("citation_scope", c)

    def test_owner_flag_pops_tag_and_skips(self):
        c = claim()
        citation_scope.classify([c], FakeLLM(resp("methods")), workers=1)
        c["owner_flag"] = {"author_says": "wrong"}
        llm = FakeLLM(resp("related"))
        citation_scope.classify([c], llm, workers=1)
        self.assertNotIn("citation_scope", c)
        self.assertEqual(llm.calls, 0)


class TestViewer(unittest.TestCase):
    def _analysis(self, c):
        return {"text_claims": [c], "omitted": [],
                "coverage": {"totals": {}, "per_source": {}},
                "sources": [], "metadata": {"marker_errors": []}}

    def _html(self, c):
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "v.html")
            viewer.generate(self._analysis(c), out)
            return open(out).read()

    def _tagged(self, scope="methods"):
        c = claim()
        c["citation_scope"] = {"scope": scope,
                               "scoped_assertion": "follows the guidelines",
                               "reason": "authors' own power analysis",
                               "model": "fake/judge", "prompt_sha": "x"}
        return c

    def test_scoped_card_rebadged_indigo(self):
        html = self._html(self._tagged())
        self.assertIn("SCOPED CITATION (METHODS)", html)
        self.assertIn("badge unsupported scoped", html)
        self.assertIn("methods citation", html)          # chip
        self.assertIn("follows the guidelines", html)    # named scoped assertion
        self.assertIn("Scoped citation (1)", html)       # filter button

    def test_full_tag_keeps_red_badge(self):
        c = claim()
        c["citation_scope"] = {"scope": "full", "scoped_assertion": "",
                               "reason": "attribution", "model": "fake/judge",
                               "prompt_sha": "x"}
        html = self._html(c)
        self.assertIn(">UNSUPPORTED<", html)
        self.assertNotIn("SCOPED CITATION", html.replace(
            "SCOPED CITATION</span> the passage", ""))  # legend row aside

    def test_untagged_unchanged(self):
        html = self._html(claim())
        self.assertIn(">UNSUPPORTED<", html)
        self.assertNotIn('data-f="scoped"', html)   # no filter button

    def test_scoped_is_separate_class_not_unsupported(self):
        """Owner 2026-07-12: scoped cards must never be confused with real
        unsupported — own card class, own count, excluded from the
        Unsupported filter and count; the verdict field stays unchanged."""
        c = self._tagged()
        html = self._html(c)
        self.assertIn('class="card scopedcite', html)      # not "card unsupported"
        self.assertNotIn('class="card unsupported', html)
        self.assertIn('class="claim scopedcite"', html)    # left-column highlight
        self.assertIn("Unsupported (0)", html)             # excluded from count
        self.assertIn("scoped citation</b>", html)         # own totals entry
        self.assertEqual(c["verdict"], "unsupported")      # analysis untouched

    def test_real_unsupported_still_counted(self):
        html = self._html(claim())
        self.assertIn("Unsupported (1)", html)
        self.assertIn('class="card unsupported', html)


if __name__ == "__main__":
    unittest.main()
