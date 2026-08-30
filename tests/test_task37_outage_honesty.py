"""Task #37 — a run during which model requests failed must never look like a
healthy run. A dead call reads as a negative answer to every voter, so before
this fix an outage minted phantom "partial support?" chips, phantom second-
opinion disagreements, and phantom amber coverage lines with no trace. Now:
the affected check's output is DROPPED, the claim carries `checks_failed`, the
result file's metadata tallies the failures, the viewer shows a top banner and
a per-card chip, a re-run retries exactly the affected claims, and the
benchmark scorers refuse to score a contaminated run. No API calls.

Run:  venv/bin/python3 -m unittest tests.test_task37_outage_honesty
"""
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "benchmarks"))

from modules.papertrail import matcher, rerun, second_opinion, viewer
import regression_check

CLAIM = "The grid is the binding constraint and demand more than doubles by 2030."


def _fake_cosine(a, b, **kw):
    return [[0.8] * len(b) for _ in a]


def _sources():
    return {
        "p1": {"title": "Energy and AI", "key": "iea",
               "sentences": [{"text": "Data-centre electricity use grows over the decade.",
                              "page": 1}], "claims": []},
        "p2": {"title": "Macroeconomics of AI", "key": "acemoglu",
               "sentences": [{"text": "The estimated productivity effect is modest.",
                              "page": 2}], "claims": []},
    }


def _multi_claim():
    return {"id": "t69", "text": CLAIM, "markers": ["iea", "acemoglu"],
            "paper_ids": ["p1", "p2"]}


class OutageLLM:
    """Per-source judge succeeds; every call whose prompt contains `fail_on`
    dies (returns None) and increments failed_calls, like the real client."""

    def __init__(self, fail_on):
        self.model = "fake/model"
        self.fail_on = fail_on
        self.failed_calls = 0

    def call(self, p, **kw):
        if self.fail_on in p:
            self.failed_calls += 1
            return None
        if "evidence finder" in p:
            return json.dumps({"sentences": []})
        if "CANDIDATE SENTENCES" in p:
            return json.dumps({"components": []})
        return json.dumps({"supported": True, "reason": "stated in the passage"})


def _run(llm, partial_check=True):
    with patch.object(matcher.embeddings, "cosine_matrix", side_effect=_fake_cosine):
        return matcher.run([_multi_claim()], _sources(), llm,
                           partial_check=partial_check)


class TestNoteCheckFailure(unittest.TestCase):

    def test_records_once_and_only_when_failures_rose(self):
        llm = OutageLLM(fail_on="<never>")
        out = {}
        self.assertFalse(matcher._note_check_failure(out, "partial_check", llm, 0))
        self.assertNotIn("checks_failed", out)
        llm.failed_calls = 2
        self.assertTrue(matcher._note_check_failure(out, "partial_check", llm, 0))
        self.assertTrue(matcher._note_check_failure(out, "partial_check", llm, 0))
        self.assertEqual(out["checks_failed"], ["partial_check"])  # no duplicate


class TestPhantomPartialFlagDropped(unittest.TestCase):

    def test_outage_during_partial_check_drops_the_flag_and_marks_the_claim(self):
        # The combined judge ("TAKEN TOGETHER") dies -> before the fix this
        # minted a phantom partial_support flag (the 2026-08-06 chimpanzee
        # run: 7 phantom chips from 233 refused calls).
        llm = OutageLLM(fail_on="TAKEN TOGETHER")
        c = _run(llm)["text_claims"][0]
        self.assertEqual(c["verdict"], "supported")
        self.assertNotIn("partial_support", c)
        self.assertIn("partial_check", c.get("checks_failed", []))
        self.assertNotIn("partial_checked", c)     # a re-run redoes the check

    def test_clean_run_still_gets_checked_and_carries_no_marker(self):
        llm = OutageLLM(fail_on="<never>")
        c = _run(llm)["text_claims"][0]
        self.assertNotIn("checks_failed", c)
        self.assertTrue(c.get("partial_checked"))


class TestPhantomCoveringDropped(unittest.TestCase):

    def test_outage_during_covering_drops_the_block_and_marks_the_claim(self):
        llm = OutageLLM(fail_on="CANDIDATE SENTENCES")
        c = _run(llm, partial_check=False)["text_claims"][0]
        self.assertEqual(c["verdict"], "supported")
        self.assertNotIn("covering", c)
        self.assertNotIn("proof_state", c)
        self.assertIn("covering", c.get("checks_failed", []))
        self.assertNotIn("covering_checked", c)    # a re-run rebuilds it


class TestRerunNeverReusesFailedChecks(unittest.TestCase):

    def test_checks_failed_blocks_reuse(self):
        c = {"id": "t1", "verdict": "supported", "reason": "judged",
             "checks_failed": ["partial_check"]}
        self.assertFalse(rerun.reusable(c))

    def test_clean_supported_claim_still_reusable(self):
        c = {"id": "t1", "verdict": "supported", "reason": "judged"}
        self.assertTrue(rerun.reusable(c))


class SOOutageLLM:
    """Second-opinion client: every call dies, like a full outage."""
    model = "fake/second"

    def __init__(self):
        self.failed_calls = 0

    def call(self, p, **kw):
        self.failed_calls += 1
        return None


class TestSecondOpinionOutage(unittest.TestCase):

    def _claim(self):
        return {"id": "t1", "text": "My claim.", "markers": ["a"],
                "verdict": "supported", "method": "llm", "reason": "judged",
                "evidences": [{"paper_id": "p1", "source_title": "Source A",
                               "supported": True, "sentence": "The source states X.",
                               "window": "Before. The source states X. After."}]}

    def test_dead_calls_write_no_opinion_so_a_rerun_retries(self):
        # Before the fix: 3 dead calls read as a unanimous 3-0 disagreement,
        # written forever (reuse keys on the model name only).
        c = self._claim()
        summary = second_opinion.run([c], SOOutageLLM(), workers=1)
        self.assertNotIn("second_opinion", c)
        self.assertEqual(summary["skipped_failed"], ["t1"])
        self.assertEqual(summary["fp_flags"], [])


class TestScorerRefusal(unittest.TestCase):

    def _analysis(self, **claim_extra):
        c = {"id": "t1", "text": "x", "verdict": "unsupported", "reason": "judged"}
        c.update(claim_extra)
        return {"metadata": {}, "text_claims": [c]}

    def test_clean_run_is_scored(self):
        self.assertEqual(regression_check.contamination(self._analysis()), "")

    def test_judge_error_refuses(self):
        msg = regression_check.contamination(self._analysis(judge_error=True))
        self.assertIn("REFUSED", msg)
        self.assertIn("t1", msg)

    def test_checks_failed_refuses(self):
        msg = regression_check.contamination(
            self._analysis(checks_failed=["partial_check"]))
        self.assertIn("REFUSED", msg)

    def test_legacy_reason_prefix_refuses(self):
        msg = regression_check.contamination(
            self._analysis(reason="no LLM response -> treated as unsupported"))
        self.assertIn("REFUSED", msg)

    def test_metadata_failed_calls_alone_refuses(self):
        a = self._analysis()
        a["metadata"]["llm_failures"] = {"failed_calls": 3, "claims_affected": []}
        self.assertIn("REFUSED", regression_check.contamination(a))

    def test_main_exits_2_on_contamination(self):
        with tempfile.TemporaryDirectory() as d:
            ap = os.path.join(d, "analysis.json")
            gp = os.path.join(d, "gt.json")
            with open(ap, "w") as f:
                json.dump(self._analysis(judge_error=True), f)
            with open(gp, "w") as f:
                json.dump({"claims": []}, f)
            rc = regression_check.main(["--analysis", ap, "--ground-truth", gp])
            self.assertEqual(rc, 2)


class TestViewerOutageDisplay(unittest.TestCase):

    def _analysis(self, meta=None, **claim_extra):
        c = {"id": "t1", "text": "My claim.", "markers": ["a"],
             "verdict": "supported", "method": "llm", "reason": "judged",
             "paper_ids": ["p1"], "cosine": 0.8,
             "evidence": {"paper_id": "p1", "supported": True,
                          "sentence": "The source states X."},
             "evidences": [{"paper_id": "p1", "source_title": "Source A",
                            "supported": True,
                            "sentence": "The source states X."}]}
        c.update(claim_extra)
        return {"metadata": meta or {}, "text_claims": [c], "omitted": [],
                "sources": [{"paper_id": "p1", "key": "a", "filename": "a.txt",
                             "title": "Source A", "num_claims": 0}]}

    def _render(self, analysis):
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "v.html")
            viewer.generate(analysis, out)
            with open(out, encoding="utf-8") as f:
                return f.read()

    def test_banner_and_chip_on_an_outage_run(self):
        meta = {"llm_failures": {"failed_calls": 4, "claims_affected": ["t1"]}}
        page = self._render(self._analysis(meta=meta,
                                           checks_failed=["partial_check"]))
        self.assertIn("request(s) to the model failed", page)
        self.assertIn("check not run — API failed", page)

    def test_no_banner_on_a_clean_run(self):
        meta = {"llm_failures": {"failed_calls": 0, "claims_affected": []}}
        page = self._render(self._analysis(meta=meta))
        self.assertNotIn("request(s) to the model failed", page)
        self.assertNotIn("check not run — API failed", page)

    def test_banner_fallback_for_old_result_files_without_the_tally(self):
        page = self._render(self._analysis(judge_error=True))
        self.assertIn("Model requests failed during this run", page)


if __name__ == "__main__":
    unittest.main()
