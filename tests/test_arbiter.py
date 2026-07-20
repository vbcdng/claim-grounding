"""Arbiter tests — trigger set, verbatim quote gate, annotation, reuse, viewer.

All offline: the LLM is a fake. Run:
  venv/bin/python3 -m unittest tests.test_arbiter -v
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.papertrail import arbiter, viewer


class FakeLLM:
    def __init__(self, response):
        self.model = "fake/arbiter"
        self.response = response
        self.calls = 0

    def call(self, prompt, **kw):
        self.calls += 1
        self.last_prompt = prompt
        return self.response


def src(pid, sentences, title="A Source"):
    return {pid: {"title": title, "sentences": [{"text": s} for s in sentences]}}


def claim(cid="t1", verdict="unsupported", **kw):
    c = {"id": cid, "text": "The viaduct was built in 1887 by Lindqvist.",
         "verdict": verdict, "paper_ids": ["p1"],
         "evidences": [{"paper_id": "p1", "sentence": "Some sentence.",
                        "supported": verdict == "supported",
                        "source_title": "A Source"}]}
    c.update(kw)
    return c


class TestTrigger(unittest.TestCase):
    def test_unsupported_triggers(self):
        self.assertEqual(arbiter.trigger(claim(verdict="unsupported")), "unsupported")

    def test_clean_supported_full_never_triggers(self):
        self.assertIsNone(arbiter.trigger(claim(verdict="supported")))

    def test_partial_support_triggers(self):
        self.assertEqual(arbiter.trigger(claim(verdict="supported",
                                               partial_support={"reason": "x"})),
                         "partial_support")

    def test_uncovered_components_trigger(self):
        self.assertEqual(arbiter.trigger(claim(verdict="supported",
                                               covering={"uncovered": ["a part"]})),
                         "uncovered_components")

    def test_conflict_candidate_triggers(self):
        c = claim(verdict="supported")
        c["evidences"].append({"paper_id": "p1", "sentence": "Contrary sentence.",
                               "supported": False, "source_title": "A Source"})
        self.assertEqual(arbiter.trigger(c), "conflict_candidate")

    def test_own_missing_file_and_owner_flag_never_trigger(self):
        self.assertIsNone(arbiter.trigger(claim(verdict="own")))
        self.assertIsNone(arbiter.trigger(
            claim(verdict="unsupported", reason="source_file_missing: x.pdf")))
        self.assertIsNone(arbiter.trigger(
            claim(verdict="unsupported", owner_flag={"author_says": "wrong"})))


class TestQuoteGate(unittest.TestCase):
    def test_verbatim_kept_hallucinated_dropped(self):
        text = arbiter._norm("The viaduct was completed in 1887 to great acclaim. "
                             "Its designer walked the gorge at dawn every day.")
        kept, dropped = arbiter.verify_quotes(
            ["The viaduct was completed in 1887 to great acclaim",
             "This sentence appears nowhere in any source at all, honestly"], text)
        self.assertEqual(len(kept), 1)
        self.assertEqual(dropped, 1)

    def test_ligature_folding(self):
        text = arbiter._norm("This ﬁnding is consistent with the theory of strategic play.")
        kept, dropped = arbiter.verify_quotes(
            ["This finding is consistent with the theory of strategic play"], text)
        self.assertEqual((len(kept), dropped), (1, 0))

    def test_too_short_quotes_dropped(self):
        kept, dropped = arbiter.verify_quotes(["1887"], arbiter._norm("built in 1887"))
        self.assertEqual((kept, dropped), ([], 1))


class TestRun(unittest.TestCase):
    SOURCES = src("p1", ["The Aldermoor Viaduct was designed by Casper Lindqvist.",
                         "The viaduct was completed in 1887 after four years of work."])

    def _response(self, proofs, action="wrong_or_insufficient_evidence", conflict=None):
        return json.dumps({"action": action, "components": [],
                           "missing_subclaim": "the Lindqvist attribution",
                           "rewrite_suggestion": "", "proof_sentences": proofs,
                           "conflict": conflict, "why": "because"})

    def test_annotates_with_verified_proofs_only(self):
        c = claim()
        llm = FakeLLM(self._response(
            ["The viaduct was completed in 1887 after four years of work.",
             "A fabricated quote that exists in no source whatsoever, truly"]))
        s = arbiter.run([c], self.SOURCES, llm, workers=1)
        ab = c["arbiter"]
        self.assertEqual(ab["action"], "wrong_or_insufficient_evidence")
        self.assertEqual(ab["trigger"], "unsupported")
        self.assertEqual(len(ab["proofs"]), 1)
        self.assertEqual(ab["quotes_dropped"], 1)
        self.assertEqual(s["checked"], 1)
        self.assertEqual(s["proof_may_exist"], ["t1"])
        self.assertIn("{TRIGGER}" not in llm.last_prompt, [True])

    def test_conflict_sentence_gated_too(self):
        c = claim(verdict="supported", partial_support={"reason": "x"})
        llm = FakeLLM(self._response(
            [], action="add_citation_or_rewrite",
            conflict={"sentence": "Not a real source sentence at all, invented",
                      "why": "it contradicts"}))
        arbiter.run([c], self.SOURCES, llm, workers=1)
        self.assertIsNone(c["arbiter"]["conflict"])   # dropped by the gate
        self.assertGreaterEqual(c["arbiter"]["quotes_dropped"], 1)

    def test_verified_conflict_survives(self):
        c = claim(verdict="supported", partial_support={"reason": "x"})
        llm = FakeLLM(self._response(
            [], action="add_citation_or_rewrite",
            conflict={"sentence": "The Aldermoor Viaduct was designed by Casper Lindqvist.",
                      "why": "contradicts the claim's designer"}))
        s = arbiter.run([c], self.SOURCES, llm, workers=1)
        self.assertIsNotNone(c["arbiter"]["conflict"])
        self.assertEqual(s["conflicts"], ["t1"])

    def test_reuse_same_prompt_sha_skips_call(self):
        c = claim()
        llm = FakeLLM(self._response([]))
        arbiter.run([c], self.SOURCES, llm, workers=1)
        sha = c["arbiter"]["prompt_sha"]
        llm2 = FakeLLM(self._response([]))
        s = arbiter.run([c], self.SOURCES, llm2, workers=1)
        self.assertEqual(llm2.calls, 0)
        self.assertEqual(s["reused"], 1)
        self.assertEqual(c["arbiter"]["prompt_sha"], sha)

    def test_unparseable_leaves_no_field(self):
        c = claim()
        arbiter.run([c], self.SOURCES, FakeLLM("garbage not json"), workers=1)
        self.assertNotIn("arbiter", c)

    def test_owner_flag_clears_stale_result(self):
        c = claim(owner_flag={"author_says": "wrong"})
        c["arbiter"] = {"action": "supported", "prompt_sha": "old"}
        llm = FakeLLM(self._response([]))
        arbiter.run([c], self.SOURCES, llm, workers=1)
        self.assertNotIn("arbiter", c)
        self.assertEqual(llm.calls, 0)

    def test_clean_supported_not_called(self):
        c = claim(verdict="supported")
        llm = FakeLLM(self._response([]))
        s = arbiter.run([c], self.SOURCES, llm, workers=1)
        self.assertEqual((llm.calls, s["checked"]), (0, 0))


class TestViewerChips(unittest.TestCase):
    def _analysis(self, c):
        return {"text_claims": [c], "omitted": [],
                "coverage": {"totals": {}, "per_source": {}},
                "sources": [], "metadata": {"marker_errors": []}}

    def _html(self, c):
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "v.html")
            viewer.generate(self._analysis(c), out)
            return open(out).read()

    def test_proof_may_exist_chip(self):
        c = claim()
        c["arbiter"] = {"model": "fake/arbiter", "prompt_sha": "x",
                        "trigger": "unsupported",
                        "action": "wrong_or_insufficient_evidence",
                        "missing_subclaim": "", "rewrite_suggestion": "",
                        "proofs": ["The viaduct was completed in 1887."],
                        "quotes_dropped": 0, "conflict": None, "why": "w"}
        html = self._html(c)
        self.assertIn("proof may exist", html)
        self.assertIn("The viaduct was completed in 1887.", html)

    def test_conflict_chip(self):
        c = claim(verdict="supported", partial_support={"reason": "x"})
        c["arbiter"] = {"model": "fake/arbiter", "prompt_sha": "x",
                        "trigger": "partial_support",
                        "action": "add_citation_or_rewrite",
                        "missing_subclaim": "the year", "rewrite_suggestion": "",
                        "proofs": [], "quotes_dropped": 0,
                        "conflict": {"sentence": "A contrary source line.",
                                     "why": "contradicts"}, "why": "w"}
        html = self._html(c)
        self.assertIn("conflicting evidence?", html)
        self.assertIn("author fix?", html)
        self.assertIn("A contrary source line.", html)


class TestRescue(unittest.TestCase):
    """arbiter.rescue: the PRIMARY judge re-judges arbiter-fetched, verified
    proof windows; only a unanimous positive flips the verdict."""

    SOURCES = src("p1", ["Intro sentence one.",
                         "The viaduct was completed in 1887.",
                         "It was designed by Lindqvist.",
                         "Closing sentence."])

    def _fetch_claim(self, proofs=None):
        c = claim()
        c["arbiter"] = {"model": "fake/arbiter", "prompt_sha": "x",
                        "trigger": "unsupported",
                        "action": "wrong_or_insufficient_evidence",
                        "missing_subclaim": "", "rewrite_suggestion": "",
                        "proofs": proofs if proofs is not None else
                        ["The viaduct was completed in 1887.",
                         "It was designed by Lindqvist."],
                        "quotes_dropped": 0, "conflict": None, "why": "w"}
        return c

    def _judge(self, supported=True):
        return FakeLLM(json.dumps({"supported": supported, "reason": "judged"}))

    def test_unanimous_positive_flips(self):
        c = self._fetch_claim()
        s = arbiter.rescue([c], self.SOURCES, self._judge(True), workers=1)
        self.assertEqual(s["flipped"], ["t1"])
        self.assertEqual(c["verdict"], "supported")
        self.assertEqual(c["method"], "arbiter_rescue")
        self.assertTrue(c["arbiter"]["rescued"])
        self.assertTrue(any(e.get("via") == "arbiter_rescue"
                            for e in c["evidences"]))

    def test_negative_judge_holds_verdict(self):
        c = self._fetch_claim()
        s = arbiter.rescue([c], self.SOURCES, self._judge(False), workers=1)
        self.assertEqual(s["flipped"], [])
        self.assertEqual(c["verdict"], "unsupported")
        self.assertIs(c["arbiter"]["rescued"], False)

    def test_unlocatable_proof_never_flips(self):
        c = self._fetch_claim(proofs=["A sentence that exists in no source at all here."])
        llm = self._judge(True)
        s = arbiter.rescue([c], self.SOURCES, llm, workers=1)
        self.assertEqual((s["flipped"], llm.calls), ([], 0))
        self.assertEqual(c["verdict"], "unsupported")

    def test_author_fix_action_never_attempted(self):
        c = self._fetch_claim()
        c["arbiter"]["action"] = "add_citation_or_rewrite"
        llm = self._judge(True)
        s = arbiter.rescue([c], self.SOURCES, llm, workers=1)
        self.assertEqual((s["attempted"], llm.calls), (0, 0))

    def test_owner_flag_never_attempted(self):
        c = self._fetch_claim()
        c["owner_flag"] = {"author_says": "wrong"}
        s = arbiter.rescue([c], self.SOURCES, self._judge(True), workers=1)
        self.assertEqual(s["attempted"], 0)

    def test_held_attempt_not_rebought(self):
        c = self._fetch_claim()
        arbiter.rescue([c], self.SOURCES, self._judge(False), workers=1)
        llm2 = self._judge(True)
        s2 = arbiter.rescue([c], self.SOURCES, llm2, workers=1)
        self.assertEqual((s2["attempted"], llm2.calls), (0, 0))

    def test_flip_drops_stale_citation_scope(self):
        c = self._fetch_claim()
        c["citation_scope"] = {"scope": "related", "scoped_assertion": "x",
                               "reason": "r", "model": "m", "prompt_sha": "s"}
        arbiter.rescue([c], self.SOURCES, self._judge(True), workers=1)
        self.assertNotIn("citation_scope", c)

    def test_rescued_card_chip_in_viewer(self):
        c = self._fetch_claim()
        arbiter.rescue([c], self.SOURCES, self._judge(True), workers=1)
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "v.html")
            viewer.generate({"text_claims": [c], "omitted": [],
                             "coverage": {"totals": {}, "per_source": {}},
                             "sources": [], "metadata": {"marker_errors": []}}, out)
            html = open(out).read()
        self.assertIn("arbiter rescue", html)
        self.assertIn("badge supported", html)


if __name__ == "__main__":
    unittest.main()


class TestResolveAmbers(unittest.TestCase):
    """Amber resolution (owner ruling 2026-07-14): arbiter action=='supported'
    + gate-verified proofs clears proof_state 'partial' -> 'arbiter_resolved';
    everything else holds the amber (and reverts a stale resolution)."""

    def _amber(self, **kw):
        c = {"id": "t1", "verdict": "supported", "proof_state": "partial",
             "covering": {"uncovered": ["literacy below 20%"]}}
        c.update(kw)
        return c

    def test_resolves_on_supported_action_with_proofs(self):
        c = self._amber(arbiter={"action": "supported",
                                 "proofs": ["Rates were below twenty percent."],
                                 "model": "deepseek/x", "why": "found it"})
        s = arbiter.resolve_ambers([c])
        self.assertEqual(c["proof_state"], "arbiter_resolved")
        self.assertEqual(c["covering"]["arbiter_resolution"]["proofs"],
                         ["Rates were below twenty percent."])
        self.assertEqual(s, {"eligible": 1, "resolved": ["t1"], "held": []})

    def test_holds_without_proofs(self):
        c = self._amber(arbiter={"action": "supported", "proofs": []})
        s = arbiter.resolve_ambers([c])
        self.assertEqual(c["proof_state"], "partial")
        self.assertNotIn("arbiter_resolution", c["covering"])
        self.assertEqual(s["held"], ["t1"])

    def test_holds_on_author_fix_action(self):
        c = self._amber(arbiter={"action": "add_citation_or_rewrite",
                                 "proofs": ["Some verified quote here."]})
        arbiter.resolve_ambers([c])
        self.assertEqual(c["proof_state"], "partial")

    def test_verdict_field_never_touched(self):
        c = self._amber(arbiter={"action": "supported", "proofs": ["q" * 30]})
        arbiter.resolve_ambers([c])
        self.assertEqual(c["verdict"], "supported")

    def test_unsupported_and_unflagged_claims_ignored(self):
        uns = {"id": "t2", "verdict": "unsupported",
               "arbiter": {"action": "supported", "proofs": ["x" * 30]}}
        clean = {"id": "t3", "verdict": "supported",
                 "arbiter": {"action": "supported", "proofs": ["y" * 30]}}
        s = arbiter.resolve_ambers([uns, clean])
        self.assertEqual(s["eligible"], 0)
        self.assertEqual(uns["verdict"], "unsupported")
        self.assertNotIn("proof_state", clean)

    def test_stale_resolution_reverts_when_arbiter_no_longer_confirms(self):
        c = self._amber(proof_state="arbiter_resolved",
                        covering={"uncovered": ["part"],
                                  "arbiter_resolution": {"proofs": ["old"]}},
                        arbiter={"action": "add_citation_or_rewrite", "proofs": []})
        s = arbiter.resolve_ambers([c])
        self.assertEqual(c["proof_state"], "partial")
        self.assertNotIn("arbiter_resolution", c["covering"])
        self.assertEqual(s["held"], ["t1"])

    def test_no_arbiter_annotation_leaves_amber_untouched(self):
        c = self._amber()
        s = arbiter.resolve_ambers([c])
        self.assertEqual(c["proof_state"], "partial")
        self.assertEqual(s["eligible"], 0)

    def test_resolves_on_better_proof_action_with_proofs(self):
        # t5/Eskelson live case: the SHOWN evidence was insufficient, but the
        # arbiter fetched the exact missing sentences under
        # wrong_or_insufficient_evidence — that resolves too.
        c = self._amber(arbiter={"action": "wrong_or_insufficient_evidence",
                                 "proofs": ["Prior to the seventeenth century, "
                                            "literacy averaged eighteen percent."],
                                 "model": "claude-code/sonnet", "why": "found"})
        s = arbiter.resolve_ambers([c])
        self.assertEqual(c["proof_state"], "arbiter_resolved")
        self.assertEqual(s["resolved"], ["t1"])

    def test_better_proof_action_without_proofs_holds(self):
        c = self._amber(arbiter={"action": "wrong_or_insufficient_evidence",
                                 "proofs": []})
        arbiter.resolve_ambers([c])
        self.assertEqual(c["proof_state"], "partial")
