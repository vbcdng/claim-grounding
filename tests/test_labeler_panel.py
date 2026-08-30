"""Offline tests for the task #15 labeling panel (no API calls, no network).

Covers the three hard requirements:
 1. a refused call is recorded as answered=False, never as a verdict;
 2. proof quotes are verbatim-checked against the source (with PDF-artifact
    tolerance) and the result recorded per part;
 3. the funnel sorts unanimous / split / insufficient correctly and never
    counts a refusal as a vote.
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from benchmarks.labeler.panel_runner import (
    run_panel, quote_in_sources, validate_verdict)
from benchmarks.labeler.funnel import sort_rows

SOURCE_TEXT = ("The study followed 4,000 adults for ten years. "
               "Egg consumption was associated with a 42% higher risk. "
               "The liver responds by suppression of cholesterol synthesis.")

ROW = {"row_id": "test:r1", "pile": "retreat_contested",
       "claim_text": "Eggs raise risk by 42%.", "context": "…",
       "old_label": "contested",
       "sources": [{"name": "src.txt", "chars": len(SOURCE_TEXT),
                    "garble_ratio": 0.0, "text": SOURCE_TEXT}]}

TEMPLATE = "CLAIM {CLAIM} CONTEXT {CONTEXT} SOURCES {SOURCES}"


def verdict_json(label, quote):
    return json.dumps({"strict_label": label,
                       "parts": [{"part": "42% higher risk",
                                  "classification": "proven",
                                  "quote": quote, "source": "src.txt"}],
                       "hard_note": None})


class StubClient:
    """Returns canned responses in order; None models a refused call."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def call(self, prompt, temperature=0.1, max_output_tokens=8000,
             purpose="untagged", claim_id=None):
        self.calls += 1
        return self.responses.pop(0) if self.responses else None


class TestPanelRunner(unittest.TestCase):

    def _run(self, factory, models, rows=None, out=None):
        if out is None:
            out = os.path.join(self.tmp.name, "verdicts.jsonl")
        counts = run_panel(rows or [ROW], models, out, client_factory=factory,
                           template=TEMPLATE, progress=lambda *_: None)
        with open(out) as f:
            recs = [json.loads(l) for l in f if l.strip()]
        return counts, recs

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def test_refusal_recorded_as_no_answer_never_a_verdict(self):
        counts, recs = self._run(lambda m: StubClient([None]), ["m1"])
        self.assertEqual(counts, {"judged": 0, "refused": 1, "skipped": 0})
        self.assertFalse(recs[0]["answered"])
        self.assertNotIn("strict_label", recs[0])

    def test_good_verdict_with_verbatim_quote_verifies(self):
        good = verdict_json(
            "pass", "Egg consumption was associated with a 42% higher risk.")
        counts, recs = self._run(lambda m: StubClient([good]), ["m1"])
        self.assertEqual(counts["judged"], 1)
        part = recs[0]["parts"][0]
        self.assertTrue(part["quote_verified"])
        self.assertEqual(part["quote_verified_in"], "src.txt")

    def test_fabricated_quote_marked_unverified_but_verdict_kept(self):
        bad = verdict_json(
            "pass", "Eggs were proven entirely harmless in all cohorts.")
        _, recs = self._run(lambda m: StubClient([bad]), ["m1"])
        self.assertTrue(recs[0]["answered"])
        self.assertFalse(recs[0]["parts"][0]["quote_verified"])

    def test_malformed_json_retried_once_then_no_answer(self):
        stub = StubClient(["not json at all", "{\"strict_label\": \"banana\"}"])
        counts, recs = self._run(lambda m: stub, ["m1"])
        self.assertEqual(stub.calls, 2)
        self.assertEqual(counts["refused"], 1)
        self.assertFalse(recs[0]["answered"])
        self.assertIn("malformed", recs[0]["reason"])

    def test_oversized_source_becomes_no_answer_not_truncated(self):
        big_row = dict(ROW, sources=[{"name": "big.txt", "chars": 1000,
                                      "garble_ratio": 0.0, "text": "x" * 1000}])
        stub = StubClient(["should never be called"])
        out = os.path.join(self.tmp.name, "verdicts.jsonl")
        counts = run_panel([big_row], ["m1"], out, client_factory=lambda m: stub,
                           template=TEMPLATE, progress=lambda *_: None,
                           max_prompt_chars=500)
        self.assertEqual(stub.calls, 0)
        self.assertEqual(counts["refused"], 1)
        with open(out) as f:
            rec = json.loads(f.readline())
        self.assertFalse(rec["answered"])
        self.assertIn("source too long", rec["reason"])

    def test_resume_skips_already_judged_pairs(self):
        good = verdict_json(
            "pass", "The study followed 4,000 adults for ten years.")
        out = os.path.join(self.tmp.name, "verdicts.jsonl")
        self._run(lambda m: StubClient([good]), ["m1"], out=out)
        counts, _ = self._run(lambda m: StubClient([good]), ["m1"], out=out)
        self.assertEqual(counts, {"judged": 0, "refused": 0, "skipped": 1})


class TestQuoteGateAndValidation(unittest.TestCase):

    def test_quote_check_tolerates_pdf_linebreak_artifacts(self):
        src = [{"name": "s", "text": ("the CRL4DCAF1 E3 ubiquitin ligase, leading "
                                      "to proteasome- dependent degradation of the protein.")}]
        self.assertEqual(quote_in_sources(
            "leading to proteasome-dependent degradation of the protein", src), "s")
        self.assertIsNone(quote_in_sources(
            "a sentence that is nowhere in the source at all", src))

    def test_validate_verdict_rejects_bad_shapes(self):
        self.assertIsNone(validate_verdict(None)[0])
        self.assertIsNone(validate_verdict({"strict_label": "pass", "parts": []})[0])
        self.assertIsNone(validate_verdict({"strict_label": "maybe", "parts": [{}]})[0])
        ok, err = validate_verdict({"strict_label": "fail_unproven",
                                    "parts": [{"part": "x",
                                               "classification": "unproven",
                                               "quote": None}]})
        self.assertIsNone(err)
        self.assertEqual(ok["strict_label"], "fail_unproven")


def _verdict(row_id, model, label, answered=True):
    rec = {"row_id": row_id, "model": model, "answered": answered}
    if answered:
        rec.update({"strict_label": label, "parts": []})
    else:
        rec["reason"] = "refused"
    return rec


class TestFunnel(unittest.TestCase):

    def test_sorts_unanimous_split_insufficient_and_ignores_refusals(self):
        rows = [dict(ROW, row_id="test:r%d" % i) for i in (1, 2, 3)]
        verdicts = [
            _verdict("test:r1", "m1", "pass"),
            _verdict("test:r1", "m2", "pass"),
            _verdict("test:r1", "m3", None, answered=False),
            _verdict("test:r2", "m1", "pass"),
            _verdict("test:r2", "m2", "fail_unproven"),
            _verdict("test:r3", "m1", "pass"),
            _verdict("test:r3", "m2", None, answered=False),
        ]
        out = {r["row_id"]: r for r in sort_rows(rows, verdicts)}
        self.assertEqual(out["test:r1"]["status"], "unanimous")
        self.assertEqual(out["test:r1"]["proposed_label"], "pass")
        self.assertEqual(out["test:r1"]["refusals"], ["m3"])
        self.assertEqual(out["test:r2"]["status"], "split")
        self.assertIsNone(out["test:r2"]["proposed_label"])
        # one answer + one refusal is NOT enough to propose anything
        self.assertEqual(out["test:r3"]["status"], "insufficient")


if __name__ == "__main__":
    unittest.main()
