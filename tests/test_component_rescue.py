"""Component rescue (owner walkthrough 2026-07-07, t23 false-unsupported):
a multi-component claim whose support is SPREAD across a source fails every
single-window judgment — the judge names one component as missing while other
parts crowd it out of the window. The rescue probes each named-missing
component alone via chunked full-text extraction; if every one is found, the
whole claim is re-judged on the union of windows, and only a UNANIMOUS positive
flips the verdict. No API calls.

Run:  venv/bin/python3 -m unittest tests.test_component_rescue -v
"""
import os
import sys
import json
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.papertrail import matcher

# Single sentence -> no tail-rescue suffixes muddying call counts.
CLAIM = ("The model has unusually strong security capabilities and was released "
         "only to trusted partners.")
S_RELEASE = "The company released the model only to a small group of trusted partners."
S_CAPABILITY = "The model shows exceptional strength in computer security tasks."
MISSING_REASON = ("the passage does not state that the model has unusually strong "
                  "security capabilities")


def _fake_cosine(a, b, **kw):
    return [[0.8] * len(b) for _ in a]


def _sources():
    return {"p1": {"title": "Bank Weekly", "key": "bank2026",
                   "sentences": [{"text": S_RELEASE, "page": 1},
                                 {"text": S_CAPABILITY, "page": 2}],
                   "claims": []}}


def _claim():
    return {"id": "t23", "text": CLAIM, "markers": ["bank2026"], "paper_ids": ["p1"]}


def _llm(union_supported: bool):
    """Route the fake calls: the per-source candidate judge always rejects; the
    full-claim extraction surfaces only the RELEASE sentence (the t23 shape: the
    capability sentence lives elsewhere in the document); judging the full claim
    against a passage WITHOUT the capability sentence rejects with a reason that
    names the missing component; the component probe extracts the capability
    sentence and confirms it; the union re-judge returns `union_supported`."""
    llm = MagicMock()

    def call(p, **kw):
        if "evidence finder" in p:
            # extraction: the full-claim pass (its {CLAIM} contains the release
            # phrase) surfaces only the release sentence; the component probe's
            # {CLAIM} is the bare capability component -> capability sentence
            if "released only to trusted partners" in p:
                return json.dumps({"sentences": [S_RELEASE]})
            return json.dumps({"sentences": [S_CAPABILITY]})
        if "TAKEN TOGETHER" in p:
            has_cap = S_CAPABILITY in p
            full_claim = "released only to trusted partners" in p
            if full_claim and has_cap:      # the union re-judge
                return json.dumps({"supported": union_supported,
                                   "reason": "every component is stated"
                                   if union_supported else "still not entailed"})
            if full_claim:                  # full claim vs release-only window
                return json.dumps({"supported": False, "reason": MISSING_REASON})
            # the bare component vs the capability sentence
            return json.dumps({"supported": has_cap,
                               "reason": "stated verbatim" if has_cap else "absent"})
        return json.dumps({"supported": False, "reason": "candidate rejected"})

    llm.call.side_effect = call
    return llm


def _run(llm):
    with patch.object(matcher.embeddings, "cosine_matrix", side_effect=_fake_cosine):
        return matcher.run([_claim()], _sources(), llm, partial_check=False)


class TestComponentRescue(unittest.TestCase):

    def test_unanimous_union_judge_flips_to_supported(self):
        c = _run(_llm(union_supported=True))["text_claims"][0]
        self.assertEqual(c["verdict"], "supported")
        self.assertEqual(c["method"], "component_rescue")
        cc = c.get("component_check") or {}
        self.assertTrue(cc.get("rescued"))
        self.assertEqual(cc.get("missing"), [])
        self.assertIn("unusually strong security capabilities", (cc.get("found") or [""])[0])
        # the capability sentence became real, clickable evidence
        self.assertTrue(any(e.get("sentence") == S_CAPABILITY and e.get("supported")
                            for e in c["evidences"]))

    def test_negative_union_judge_keeps_unsupported_but_records_the_check(self):
        c = _run(_llm(union_supported=False))["text_claims"][0]
        self.assertEqual(c["verdict"], "unsupported")
        cc = c.get("component_check") or {}
        self.assertFalse(cc.get("rescued"))
        self.assertTrue(cc.get("found"))
        # the individually-verified component evidence is preserved for the card
        self.assertTrue(any((e.get("sentence") == S_CAPABILITY)
                            for e in (cc.get("evidence") or [])))

    def test_unparseable_reason_skips_the_rescue(self):
        llm = MagicMock()

        def call(p, **kw):
            if "evidence finder" in p:
                return json.dumps({"sentences": [S_RELEASE]})
            if "TAKEN TOGETHER" in p:
                return json.dumps({"supported": False, "reason": "nope"})
            return json.dumps({"supported": False, "reason": "candidate rejected"})

        llm.call.side_effect = call
        c = _run(llm)["text_claims"][0]
        self.assertEqual(c["verdict"], "unsupported")
        self.assertNotIn("component_check", c)

    def test_rescued_verdict_is_exempt_from_partial_check(self):
        llm = _llm(union_supported=True)
        with patch.object(matcher.embeddings, "cosine_matrix", side_effect=_fake_cosine), \
             patch.object(matcher, "_partial_flags") as pf:
            analysis = matcher.run([_claim()], _sources(), llm, partial_check=True)
        self.assertEqual(analysis["text_claims"][0]["method"], "component_rescue")
        pf.assert_not_called()


if __name__ == "__main__":
    unittest.main()


def _llm_split(components, union_supported=True, findable=None):
    """Round-6 mock: the component SPLIT call returns `components`; extraction
    finds a sentence for a component iff findable(comp) (default: capability
    and release both findable); union re-judge returns union_supported."""
    llm = MagicMock()
    findable = findable or (lambda comp: True)

    def call(p, **kw):
        FULL = "and was released"          # appears only in the full claim text
        if "Split the CLAIM into its citable components" in p:
            return json.dumps({"components": components})
        if "evidence finder" in p:
            if FULL in p:                  # full-claim extraction: release only
                return json.dumps({"sentences": [S_RELEASE]})
            comp = next((c for c in components if c in p), None)
            if comp is not None and not findable(comp):
                return json.dumps({"sentences": []})
            return json.dumps({"sentences": [S_CAPABILITY if "security" in p
                                             else S_RELEASE]})
        if "TAKEN TOGETHER" in p:
            if FULL in p and S_CAPABILITY in p:   # the union re-judge
                return json.dumps({"supported": union_supported,
                                   "reason": "every component is stated"
                                   if union_supported else "still not entailed"})
            if FULL in p:                  # full claim vs release-only window
                return json.dumps({"supported": False, "reason": "nope"})
            return json.dumps({"supported": True, "reason": "stated"})
        return json.dumps({"supported": False, "reason": "candidate rejected"})

    llm.call.side_effect = call
    return llm


class TestSplitDrivenRescue(unittest.TestCase):
    """Round-6: the component list comes from a real LLM split (regex fallback),
    so an unmatched judge phrasing no longer skips rescue (r5 t1) and unnamed
    components can no longer sneak past the all-found bar (r5 t3)."""

    COMPS = ["the model was released only to trusted partners",
             "the model has unusually strong security capabilities"]

    def test_split_rescues_despite_unmatched_reason(self):
        # judge reason "nope" matches no regex — pre-round-6 this skipped rescue
        c = _run(_llm_split(self.COMPS))["text_claims"][0]
        self.assertEqual(c["verdict"], "supported")
        self.assertEqual(c["method"], "component_rescue")
        self.assertEqual((c.get("component_check") or {}).get("missing"), [])

    def test_unfound_split_component_blocks_the_flip(self):
        comps = self.COMPS + ["the model was trained during 2024"]
        c = _run(_llm_split(comps,
                            findable=lambda x: "2024" not in x))["text_claims"][0]
        self.assertEqual(c["verdict"], "unsupported")   # honest: a part is absent
        cc = c.get("component_check") or {}
        self.assertFalse(cc.get("rescued"))
        self.assertIn("the model was trained during 2024", cc.get("missing") or [])

    def test_non_unanimous_union_still_no_flip(self):
        c = _run(_llm_split(self.COMPS, union_supported=False))["text_claims"][0]
        self.assertEqual(c["verdict"], "unsupported")


class TestNumericCanon(unittest.TestCase):

    def test_grouped_numbers_share_a_token(self):
        self.assertEqual(matcher._canon_tok("100,000"), "100000")
        self.assertEqual(matcher._canon_tok("100 000"), "100000")
        self.assertEqual(matcher._canon_tok("1.000.000"), "1000000")

    def test_decimals_untouched(self):
        self.assertEqual(matcher._canon_tok("5.8"), "5.8")
        self.assertEqual(matcher._canon_tok("9.7%"), "9.7%")

    def test_lex_scores_match_across_grouping(self):
        scores = matcher._lex_scores(
            "a global homicide rate of 5.8 per 100,000 in 2021",
            ["The global homicide rate in 2021 is estimated at 5.8 victims per 100 000.",
             "Unrelated sentence about armed conflict trends."])
        self.assertGreater(scores[0], scores[1])
