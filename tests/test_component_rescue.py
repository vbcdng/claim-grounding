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


class TestStructuredMissingPartsInRescue(unittest.TestCase):
    """2026-08-06: the regex-fallback slot in component rescue now tries the
    judge's own structured missing_parts before the reason-text regex.
    _split_components STAYS primary regardless -- that precedence is
    unchanged. No API calls."""

    CLAIM = ("The gadget is waterproof to 30 meters and lasts 12 hours on "
             "one charge.")
    S_CHARGE = "The gadget lasts 12 hours on a single charge."
    SPLIT_MARK = "Split the CLAIM into its citable components"

    def _sources(self):
        return {"p1": {"title": "Gadget Weekly", "key": "gadget2026",
                       "sentences": [{"text": self.S_CHARGE, "page": 1}],
                       "claims": []}}

    def _llm(self, split_components=None):
        """Extraction finds S_CHARGE only for a component naming '12 hours';
        any other component finds nothing. The union re-judge always
        supports. split_components=None means no split call is expected
        (split_prompt=None in the test); a list means the split call returns
        it verbatim."""
        def call(p, **kw):
            if self.SPLIT_MARK in p:
                return json.dumps({"components": split_components or []})
            if "evidence finder" in p:
                found = "12 hours" in p
                return json.dumps({"sentences": [self.S_CHARGE] if found else []})
            if "TAKEN TOGETHER" in p:
                return json.dumps({"supported": True, "reason": "every component is stated"})
            return json.dumps({"supported": False, "reason": "candidate rejected"})
        llm = MagicMock()
        llm.call.side_effect = call
        return llm

    def _rescue(self, llm, reason, split_prompt=None, structured_missing=None):
        return matcher._component_rescue(
            self.CLAIM, ["p1"], self._sources(), llm,
            "evidence finder CLAIM {CLAIM} SOURCE {SOURCE}",
            "TAKEN TOGETHER CLAIM {CLAIM} PASSAGE {PASSAGE}",
            reason, [], split_prompt=split_prompt,
            structured_missing=structured_missing)

    def test_split_stays_primary_even_when_structured_is_present(self):
        # split names the FINDABLE component; structured names a different,
        # UNFINDABLE one. If structured wrongly outranked split, the probe
        # would search for the unfindable component and never flip.
        llm = self._llm(split_components=["the gadget lasts 12 hours on one charge"])
        rescue = self._rescue(
            llm, reason="nope", split_prompt=self.SPLIT_MARK,
            structured_missing=["the gadget is waterproof to 30 meters"])
        self.assertIsNotNone(rescue)
        self.assertEqual(rescue["found"], ["the gadget lasts 12 hours on one charge"])
        self.assertTrue(rescue["flip"])

    def test_structured_used_when_split_returns_nothing(self):
        # No split_prompt at all (split skipped entirely) -> falls straight
        # to structured_missing, which names a component the extraction finds.
        llm = self._llm()
        rescue = self._rescue(
            llm, reason="nope, unmatched by any regex shape", split_prompt=None,
            structured_missing=["the gadget lasts 12 hours on one charge"])
        self.assertIsNotNone(rescue)
        self.assertEqual(rescue["found"], ["the gadget lasts 12 hours on one charge"])
        self.assertTrue(rescue["flip"])

    def test_regex_fallback_when_structured_absent(self):
        # No split_prompt, no structured_missing -> the pre-2026-08-06 path:
        # the regex over `reason` is the only source of components.
        llm = self._llm()
        rescue = self._rescue(
            llm,
            reason="the passage does not state that the gadget lasts 12 hours on one charge",
            split_prompt=None, structured_missing=None)
        self.assertIsNotNone(rescue)
        self.assertEqual(rescue["found"], ["the gadget lasts 12 hours on one charge"])

    def test_no_components_from_any_source_skips_the_rescue(self):
        # No split, no structured, and an unmatched reason -> _missing_from
        # returns [] just like the old regex-only path did.
        llm = self._llm()
        rescue = self._rescue(llm, reason="nope, no shape at all",
                              split_prompt=None, structured_missing=None)
        self.assertIsNone(rescue)


class TestJudgeMissingPartsField(unittest.TestCase):
    """2026-08-06 round 1: `judge_missing_parts` is purely additive on the
    claim's evaluation result -- recorded when the FINAL verdict is
    unsupported and the judge supplied a structured list, absent when it
    didn't. Nothing reads it yet (round 2). No API calls."""

    CLAIM = ("The device runs for 12 hours on one charge and is waterproof "
             "to 30 meters.")
    SENT = "The device runs for 12 hours on a single charge."

    def _sources(self):
        return {"p1": {"title": "Gadget Weekly", "key": "gadget2026",
                       "sentences": [{"text": self.SENT, "page": 1}],
                       "claims": []}}

    def _evaluate(self, llm):
        # cosine below OFFTOPIC on every candidate -> straight to the
        # full-text extraction fallback (component_rescue=False keeps this
        # test focused on the missing-parts field, not the rescue machinery).
        return matcher._evaluate(
            self.CLAIM, ["p1"], lambda pid: [0.5], self._sources(), llm,
            "CLAIM {CLAIM} PASSAGE {PASSAGE}",
            "evidence finder CLAIM {CLAIM} SOURCE {SOURCE}",
            "TAKEN TOGETHER CLAIM {CLAIM} PASSAGE {PASSAGE}",
            component_rescue=False)

    def test_structured_missing_parts_recorded_on_unsupported_claim(self):
        def call(p, **kw):
            if "evidence finder" in p:
                return json.dumps({"sentences": [self.SENT]})
            if "TAKEN TOGETHER" in p:
                return json.dumps({"supported": False,
                                   "reason": "waterproofing is never mentioned",
                                   "missing_parts": ["the device is waterproof to 30 meters"]})
            return json.dumps({"supported": False, "reason": "candidate rejected"})
        llm = MagicMock()
        llm.call.side_effect = call
        res = self._evaluate(llm)
        self.assertEqual(res["verdict"], "unsupported")
        self.assertEqual(res.get("judge_missing_parts"),
                         ["the device is waterproof to 30 meters"])

    def test_absent_when_judge_provides_no_field(self):
        def call(p, **kw):
            if "evidence finder" in p:
                return json.dumps({"sentences": [self.SENT]})
            if "TAKEN TOGETHER" in p:
                return json.dumps({"supported": False,
                                   "reason": "waterproofing is never mentioned"})
            return json.dumps({"supported": False, "reason": "candidate rejected"})
        llm = MagicMock()
        llm.call.side_effect = call
        res = self._evaluate(llm)
        self.assertEqual(res["verdict"], "unsupported")
        self.assertNotIn("judge_missing_parts", res)


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
