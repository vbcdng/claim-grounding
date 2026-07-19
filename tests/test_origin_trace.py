"""Offline tests for origin_trace (Stream B) — claim-origin tracing.
Stub LLM (no API), mocked S2 resolution (no network). Verifies the walk,
the honesty rule (unfetchable -> stop, never guess), and that tracing NEVER
mutates the claim / flips its verdict.

Run:  venv/bin/python3 -m unittest tests.test_origin_trace -v
"""

import os
import sys
import copy
import json
import logging
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.papertrail import origin_trace as ot

# Several tests deliberately hit skip/not-found/unfetchable paths; quiet the logs.
logging.getLogger("modules.papertrail.origin_trace").setLevel(logging.CRITICAL)


# A compact but realistic cited source: a comment paper whose supporting passage
# attributes the finding to reference [5], with a numbered bibliography.
SOURCE_TEXT = (
    "In their Biology Letters article, Engelmann et al. [5] report evidence that "
    "chimpanzees also prepare for multiple possible outcomes. Here we point out "
    "concerns with that study.\n"
    "References\n"
    "1. Somebody A, Other B. 2019 An unrelated methods paper. Venue J. 3, 1. "
    "(doi:10.1/aaa)\n"
    "5. Engelmann JM, Voelter CJ, Goddu MK, Call J, Rakoczy H, Herrmann E. 2023 "
    "Chimpanzees prepare for alternative possible outcomes. Biol. Lett. 19, "
    "20230179. (doi:10.1098/rsbl.2023.0179)\n"
    "6. Redshaw J, Suddendorf T. 2016 Children and apes preparatory responses. "
    "Curr. Biol. 26, 1758. (doi:10.1016/j.cub.2016.04.062)\n"
)

CLAIM = {
    "id": "t0",
    "verdict": "supported",
    "paper_ids": ["P_CITED"],
    "text": ("Engelmann et al. report evidence that chimpanzees also prepare for "
             "multiple possible outcomes."),
    "evidence": {
        "paper_id": "P_CITED",
        "window": ("In their Biology Letters article, Engelmann et al. [5] report "
                   "evidence that chimpanzees also prepare for multiple possible "
                   "outcomes."),
    },
}

SOURCE_TEXTS = {"P_CITED": {"title": "Redshaw & Suddendorf comment", "text": SOURCE_TEXT}}

ENGELMANN_PAPER = {"paper_id": "S2_ENGELMANN", "title": "Chimpanzees prepare for "
                   "alternative possible outcomes",
                   "abstract": "We presented chimpanzees with a forked-tube task and "
                   "found they covered both exits, preparing for both outcomes.",
                   "year": 2023, "doi": "10.1098/rsbl.2023.0179"}


class StubLLM:
    """Returns scripted JSON dicts in order; records how many calls happened."""
    def __init__(self, script, model="stub/model"):
        self.script = list(script)
        self.model = model
        self.calls = 0

    def call_json(self, prompt, **kw):
        self.calls += 1
        return self.script.pop(0) if self.script else None


D = lambda ref, c=0.95: {"attribution": "derivative", "cited_ref": ref, "confidence": c, "reason": "cites another work"}
OWN = lambda c=0.9: {"attribution": "own", "cited_ref": None, "confidence": c, "reason": "own finding"}


class TestResolveReferenced(unittest.TestCase):
    def test_numeric_ref_resolves_doi_and_title(self):
        ref = ot.resolve_referenced("[5]", SOURCE_TEXT)
        self.assertIsNotNone(ref)
        self.assertEqual(ref["ref_num"], 5)
        self.assertEqual(ref["doi"], "10.1098/rsbl.2023.0179")
        self.assertEqual(ref["year"], "2023")
        self.assertIn("alternative possible outcomes", ref["title"])

    def test_ref_not_in_bibliography_returns_none(self):
        self.assertIsNone(ot.resolve_referenced("[42]", SOURCE_TEXT))

    def test_author_year_ref_not_guessed(self):
        # No number -> we do NOT guess (author-year resolution is future work).
        self.assertIsNone(ot.resolve_referenced("Engelmann et al. 2023", SOURCE_TEXT))

    def test_ref_number_accepts_numbered_markers_only(self):
        self.assertEqual(ot._ref_number("[12]"), 12)
        self.assertEqual(ot._ref_number("12"), 12)
        self.assertEqual(ot._ref_number("ref 12"), 12)
        self.assertEqual(ot._ref_number("[12, 14]"), 12)
        # An author-year string's year digits are NOT a bibliography number —
        # the unanchored regex used to read '202' out of '(2020)' and follow a
        # wrong entry in any source with >=190 numbered refs.
        self.assertIsNone(ot._ref_number("Smith et al. (2020)"))
        self.assertIsNone(ot._ref_number("Engelmann et al. 2023"))
        self.assertIsNone(ot._ref_number("see Figure 5 of [the report]"))

    def test_empty_inputs(self):
        self.assertIsNone(ot.resolve_referenced("", SOURCE_TEXT))
        self.assertIsNone(ot.resolve_referenced("[5]", ""))


class TestJudgeAttribution(unittest.TestCase):
    def test_parses_derivative(self):
        llm = StubLLM([D("[5]")])
        out = ot.judge_attribution("claim", "passage [5]", "src", llm)
        self.assertEqual(out["attribution"], "derivative")
        self.assertEqual(out["cited_ref"], "[5]")
        self.assertAlmostEqual(out["confidence"], 0.95)

    def test_bad_json_becomes_unknown_low_conf(self):
        llm = StubLLM([None])
        out = ot.judge_attribution("c", "p", "s", llm)
        self.assertEqual(out["attribution"], "unknown")
        self.assertEqual(out["confidence"], 0.0)

    def test_null_string_cited_ref_normalized(self):
        llm = StubLLM([{"attribution": "own", "cited_ref": "null", "confidence": 0.9}])
        out = ot.judge_attribution("c", "p", "s", llm)
        self.assertIsNone(out["cited_ref"])


class TestTraceClaim(unittest.TestCase):
    def test_relay_to_origin_depth2(self):
        llm = StubLLM([D("[5]"), OWN()])       # cited=derivative, origin=own
        with patch.object(ot, "_s2_paper_from_ref", return_value=ENGELMANN_PAPER):
            out = ot.trace_claim(CLAIM, "/nonexistent/sources", llm,
                                 sources=[{"paper_id": "P_CITED", "title": "comment"}],
                                 source_texts=SOURCE_TEXTS, max_depth=2)
        self.assertTrue(out["origin_found"])
        self.assertEqual(out["stopped_because"], "primary")
        self.assertEqual(len(out["chain"]), 2)
        self.assertEqual([n["role"] for n in out["chain"]], ["cited", "origin"])
        self.assertEqual(out["chain"][0]["attribution"], "cites:[5]")
        self.assertEqual(out["chain"][1]["paper_id"], "S2_ENGELMANN")
        self.assertEqual(llm.calls, 2)

    def test_never_mutates_claim_or_verdict(self):
        before = copy.deepcopy(CLAIM)
        llm = StubLLM([D("[5]"), OWN()])
        with patch.object(ot, "_s2_paper_from_ref", return_value=ENGELMANN_PAPER):
            ot.trace_claim(CLAIM, "/x", llm, source_texts=SOURCE_TEXTS, max_depth=2)
        self.assertEqual(CLAIM, before)        # read-only: nothing changed
        self.assertEqual(CLAIM["verdict"], "supported")

    def test_own_at_hop0_is_immediate_origin(self):
        llm = StubLLM([OWN()])
        out = ot.trace_claim(CLAIM, "/x", llm, source_texts=SOURCE_TEXTS, max_depth=2)
        self.assertTrue(out["origin_found"])
        self.assertEqual(out["stopped_because"], "primary")
        self.assertEqual(len(out["chain"]), 1)
        self.assertEqual(out["chain"][0]["role"], "origin")

    def test_unfetchable_when_ref_unresolvable(self):
        # derivative, but the passage points to a ref not in the bibliography
        llm = StubLLM([D("[99]")])
        out = ot.trace_claim(CLAIM, "/x", llm, source_texts=SOURCE_TEXTS, max_depth=2)
        self.assertFalse(out["origin_found"])
        self.assertEqual(out["stopped_because"], "unfetchable")
        self.assertEqual(len(out["chain"]), 1)

    def test_unfetchable_when_s2_cannot_find_paper(self):
        llm = StubLLM([D("[5]")])
        with patch.object(ot, "_s2_paper_from_ref", return_value=None):
            out = ot.trace_claim(CLAIM, "/x", llm, source_texts=SOURCE_TEXTS, max_depth=2)
        self.assertEqual(out["stopped_because"], "unfetchable")

    def test_low_confidence_stops_without_guessing(self):
        llm = StubLLM([D("[5]", c=0.2)])
        out = ot.trace_claim(CLAIM, "/x", llm, source_texts=SOURCE_TEXTS, max_depth=2)
        self.assertFalse(out["origin_found"])
        self.assertEqual(out["stopped_because"], "low_conf")

    def test_max_depth_stop(self):
        # depth budget 1: hop0 and hop1 both derivative & confident -> max_depth
        llm = StubLLM([D("[5]"), D("[6]")])
        with patch.object(ot, "_s2_paper_from_ref", return_value=ENGELMANN_PAPER):
            out = ot.trace_claim(CLAIM, "/x", llm, source_texts=SOURCE_TEXTS, max_depth=1)
        self.assertFalse(out["origin_found"])
        self.assertEqual(out["stopped_because"], "max_depth")
        self.assertEqual(out["depth"], 1)


class TestAttributionCache(unittest.TestCase):
    def test_second_call_served_from_cache(self):
        with tempfile.TemporaryDirectory() as d:
            llm = StubLLM([OWN(), OWN()])
            first = ot._cached_attribution(CLAIM, {"paper_id": "P_CITED",
                                                   "passage": "p", "text": "t"}, llm, d)
            self.assertEqual(llm.calls, 1)
            second = ot._cached_attribution(CLAIM, {"paper_id": "P_CITED",
                                                    "passage": "p", "text": "t"}, llm, d)
            self.assertEqual(llm.calls, 1)     # no new LLM call
            self.assertEqual(first, second)


class TestTraceRun(unittest.TestCase):
    def test_no_claim_ids_traces_nothing(self):
        llm = StubLLM([])
        self.assertEqual(ot.trace_run({"text_claims": [CLAIM]}, "/x", llm), {})
        self.assertEqual(llm.calls, 0)

    def test_opt_in_traces_selected_and_writes_file(self):
        analysis = {"text_claims": [CLAIM,
                                    {"id": "t1", "verdict": "unsupported", "paper_ids": []}],
                    "sources": [{"paper_id": "P_CITED", "title": "comment"}]}
        llm = StubLLM([OWN()])
        with tempfile.TemporaryDirectory() as d:
            out_path = os.path.join(d, "origin_trace.json")
            # _load_source_texts finds no cache dir -> falls back to source title
            with patch.object(ot, "_load_source_texts", return_value=SOURCE_TEXTS):
                res = ot.trace_run(analysis, "/x", llm, claim_ids=["t0", "t1", "tX"],
                                   out_path=out_path)
            self.assertIn("t0", res)
            self.assertNotIn("t1", res)        # unsupported -> skipped
            self.assertNotIn("tX", res)        # missing -> skipped
            self.assertTrue(os.path.exists(out_path))
            with open(out_path) as f:
                self.assertEqual(json.load(f)["t0"]["origin_found"], True)


if __name__ == "__main__":
    import logging
    logging.disable(logging.CRITICAL)
    unittest.main()
