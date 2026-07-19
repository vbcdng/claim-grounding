"""Partial-support nudge: a multi-citation claim judged 'supported' by the
per-source OR (any one cited source backs its fragment) is re-checked by the
component-complete combined judge; if a specific component is in NONE of the
cited sources, the claim keeps its verdict but is flagged partial_support and
chipped in the viewer. A nudge, never a veto. No API calls.

Run:  venv/bin/python3 -m unittest tests.test_partial_support -v
"""
import os
import sys
import json
import tempfile
import unittest
from unittest.mock import MagicMock, patch


def _no_legend(page):
    """Drop the static 'How to read this' legend so chip-label sentinels match
    only real card chips, not the legend swatches (viewer.py legend, 2026-07-06)."""
    pre, _, rest = page.partition('<details class="legend">')
    _, _, post = rest.partition('</details>')
    return pre + post

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.papertrail import matcher, viewer

CLAIM = "The grid is the binding constraint and demand more than doubles by 2030."


def _fake_cosine(a, b, **kw):
    # on-topic but below AUTO_SUPPORT so every candidate is sent to the judge
    return [[0.8] * len(b) for _ in a]


def _run(claims, sources, llm, **kw):
    # partial_check is OPT-IN (default False after the 2026-07-05 re-audit found the
    # window-only judge over-flags); these tests exercise the detection logic, so
    # enable it explicitly unless a test overrides it.
    kw.setdefault("partial_check", True)
    with patch.object(matcher.embeddings, "cosine_matrix", side_effect=_fake_cosine):
        return matcher.run(claims, sources, llm, **kw)


def _llm(persrc_fn, combined_fn):
    """Route judgment calls: the combined judge's prompt says 'TAKEN TOGETHER';
    the per-source judge's does not; extraction ('evidence finder') finds nothing."""
    llm = MagicMock()

    def call(p, **kw):
        if "evidence finder" in p:
            return json.dumps({"sentences": []})
        if "TAKEN TOGETHER" in p:
            return json.dumps(combined_fn(p))
        return json.dumps(persrc_fn(p))

    llm.call.side_effect = call
    return llm


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


class TestPartialSupportMatcher(unittest.TestCase):

    def test_partial_flag_when_a_component_is_in_no_source(self):
        # per-source OR passes (p1 backs its fragment); combined judge says the
        # whole claim is NOT fully covered -> flag, verdict stays supported.
        llm = _llm(persrc_fn=lambda p: {"supported": True, "reason": "fragment"}
                   if "Energy and AI" in p else {"supported": False, "reason": "no"},
                   combined_fn=lambda p: {"supported": False,
                                          "reason": "the doubling-by-2030 figure is in no source"})
        c = _run([_multi_claim()], _sources(), llm)["text_claims"][0]
        self.assertEqual(c["verdict"], "supported")            # NEVER a veto
        self.assertIn("partial_support", c)
        self.assertIn("2030", c["partial_support"]["reason"])

    def test_no_flag_when_combined_judge_confirms_every_component(self):
        llm = _llm(persrc_fn=lambda p: {"supported": True, "reason": "fragment"}
                   if "Energy and AI" in p else {"supported": False, "reason": "no"},
                   combined_fn=lambda p: {"supported": True, "reason": "all covered"})
        c = _run([_multi_claim()], _sources(), llm)["text_claims"][0]
        self.assertEqual(c["verdict"], "supported")
        self.assertNotIn("partial_support", c)

    def test_single_citation_claim_is_partial_checked(self):
        # Owner walkthrough 2026-07-07 (t6): a single-citation compound claim
        # over-supports the same way — the check fires on it too now.
        tc = {"id": "t1", "text": CLAIM, "markers": ["iea"], "paper_ids": ["p1"]}
        llm = _llm(persrc_fn=lambda p: {"supported": True, "reason": "ok"},
                   combined_fn=lambda p: {"supported": False, "reason": "x"})
        c = _run([tc], _sources(), llm)["text_claims"][0]
        self.assertEqual(c["verdict"], "supported")             # a nudge, never a veto
        self.assertIn("partial_support", c)
        self.assertTrue(c["partial_support"]["escalated"])

    def test_component_hunt_names_the_other_source_that_backs_the_gap(self):
        # The judge names a missing component; it is in NO cited source, but the
        # project has another downloaded source that contains it -> the flag
        # carries a component_hunt entry pointing there.
        tc = {"id": "t1", "text": CLAIM, "markers": ["iea"], "paper_ids": ["p1"]}
        sources = _sources()
        sources["p3"] = {"title": "Grid Outlook", "key": "grid2030",
                         "sentences": [{"text": "Electricity demand more than doubles by 2030.",
                                        "page": 3}],
                         "claims": [{"text": "Demand more than doubles by 2030.",
                                     "evidence": ["Electricity demand more than doubles by 2030."]}]}
        hit = "Electricity demand more than doubles by 2030."

        def call(p, **kw):
            if "evidence finder" in p:
                # extraction finds the component only in p3's text
                return json.dumps({"sentences": [hit] if hit in p else []})
            if "TAKEN TOGETHER" in p:
                if hit in p:      # the component judged against p3's sentence
                    return json.dumps({"supported": True, "reason": "stated verbatim"})
                return json.dumps({"supported": False,
                                   "reason": "the passage does not state that demand "
                                             "more than doubles by 2030"})
            return json.dumps({"supported": True, "reason": "ok"})

        llm = MagicMock()
        llm.call.side_effect = call
        c = _run([tc], sources, llm)["text_claims"][0]
        self.assertEqual(c["verdict"], "supported")
        flag = c.get("partial_support") or {}
        hunt = flag.get("component_hunt") or []
        self.assertTrue(hunt, "expected a component_hunt on the flag")
        self.assertIn("demand more than doubles", hunt[0]["component"])
        self.assertEqual([f["paper_id"] for f in hunt[0]["found_in"]], ["p3"])

    def test_no_partial_check_flag_disables_it(self):
        combined = MagicMock(return_value={"supported": False, "reason": "x"})
        llm = _llm(persrc_fn=lambda p: {"supported": True, "reason": "fragment"}
                   if "Energy and AI" in p else {"supported": False, "reason": "no"},
                   combined_fn=combined)
        c = _run([_multi_claim()], _sources(), llm, partial_check=False)["text_claims"][0]
        self.assertEqual(c["verdict"], "supported")
        self.assertNotIn("partial_support", c)
        combined.assert_not_called()

    def test_unsupported_claim_is_not_partial_checked(self):
        combined = MagicMock(return_value={"supported": False, "reason": "x"})
        llm = _llm(persrc_fn=lambda p: {"supported": False, "reason": "no"},
                   combined_fn=combined)
        c = _run([_multi_claim()], _sources(), llm)["text_claims"][0]
        self.assertEqual(c["verdict"], "unsupported")
        self.assertNotIn("partial_support", c)


class TestPartialFlagsRounds(unittest.TestCase):
    """The 2026-07-05 accuracy fix, unit-level: round 1 injects each source's
    LEAD sentences (title/abstract) next to its window; a round-1 negative
    escalates to the source's cached decomposed claims; only a double negative
    flags. ALCE precision (over-citation) rides along when recall passes."""

    LEAD = "Could one country outgrow the rest of the world entirely."
    WIN = "Growth compounds under automation feedback."

    def _sources(self, with_claims=False):
        claims = ([{"text": "A single country can outgrow the rest of the world."}]
                  if with_claims else [])
        return {
            "p1": {"title": "Outgrowing the world",
                   "sentences": [{"text": self.LEAD, "page": 1}], "claims": claims},
            "p2": {"title": "Second source",
                   "sentences": [{"text": "Another lead sentence entirely.", "page": 1}],
                   "claims": []},
        }

    def _evidences(self, p1_supported=True):
        return [{"paper_id": "p1", "source_title": "Outgrowing the world",
                 "supported": p1_supported, "window": self.WIN},
                {"paper_id": "p2", "source_title": "Second source",
                 "supported": False, "window": "An unrelated window."}]

    def _llm(self, fn):
        llm = MagicMock()
        llm.call.side_effect = lambda p, **kw: json.dumps(fn(p))
        return llm

    def _flags(self, fn, sources=None, evidences=None):
        return matcher._partial_flags(
            "One country could outgrow the rest of the world.",
            ["p1", "p2"], sources or self._sources(), evidences or self._evidences(),
            self._llm(fn), "CLAIM {CLAIM} PASSAGE {PASSAGE}")

    def test_round1_passage_contains_lead_sentences_and_window(self):
        seen = []
        def fn(p):
            seen.append(p)
            return {"supported": True, "reason": "ok"}
        self._flags(fn)
        self.assertIn(self.LEAD, seen[0])          # i-a: lead injected
        self.assertIn(self.WIN, seen[0])           # window still present

    def test_lead_sentence_clears_the_false_alarm_without_escalation(self):
        # judge supports iff it can see the lead (the t8/davidson2025 class)
        calls = []
        def fn(p):
            calls.append(p)
            return {"supported": self.LEAD in p, "reason": "title states it"}
        flags = self._flags(fn, evidences=self._evidences())
        self.assertNotIn("partial_support", flags)
        self.assertNotIn("Claims this source makes:", "".join(calls))  # no round 2

    def test_negative_round1_escalates_to_decomposed_claims(self):
        # judge supports only once the decomposed claims are in the passage
        def fn(p):
            return {"supported": "Claims this source makes:" in p, "reason": "found"}
        flags = self._flags(fn, sources=self._sources(with_claims=True))
        self.assertNotIn("partial_support", flags)

    def test_missing_component_extraction(self):
        comps = matcher._missing_components(
            "The passage does not state that AI comes to substitute for human labor, "
            "nor that the share of income paid as wages falls.")
        self.assertEqual(comps, ["AI comes to substitute for human labor",
                                 "the share of income paid as wages falls"])
        self.assertEqual(matcher._missing_components("the figure is in no source"), [])

    def test_missing_component_contradiction_shapes(self):
        # rule-5 phrasings must be probeable too (t28's second blocker)
        self.assertEqual(
            matcher._missing_components(
                "The claim that gains from scale are levelling off is directly "
                "contradicted by the passage."),
            ["gains from scale are levelling off"])
        self.assertEqual(
            matcher._missing_components(
                "The passage contradicts the claim that persuasiveness keeps "
                "rising, which the sources dispute."),
            ["persuasiveness keeps rising"])
        self.assertEqual(
            matcher._missing_components(
                '"each generation is more persuasive" is contradicted by the '
                "Hackenburg finding — returns diminish with scale."),
            ["each generation is more persuasive"])
        # pronoun-only subjects are useless probe text -> no components
        self.assertEqual(
            matcher._missing_components("the passage contradicts it"), [])

    def test_round3_probe_clears_a_self_refuting_flag(self):
        # rounds 1+2 reject naming a component; probing that component ALONE
        # against the same evidence succeeds -> the flag refuted itself
        def fn(p):
            claim_part = p.split("PASSAGE")[0]
            if "the sky is blue" in claim_part:
                return {"supported": True, "reason": "stated verbatim"}
            return {"supported": False,
                    "reason": "The passage does not state that the sky is blue."}
        flags = self._flags(fn)
        self.assertNotIn("partial_support", flags)

    def test_round3_extraction_probe_clears_when_a_source_contains_component(self):
        # with extract_check wired (the production path), the probe asks the
        # chunked-extraction pipeline instead of the context judge
        def fn(p):
            return {"supported": False,
                    "reason": "The passage does not state that the sky is blue."}
        flags = matcher._partial_flags(
            "One country could outgrow the rest of the world.",
            ["p1", "p2"], self._sources(), self._evidences(),
            self._llm(fn), "CLAIM {CLAIM} PASSAGE {PASSAGE}",
            extract_check=lambda pid, comp: pid == "p1")
        self.assertNotIn("partial_support", flags)

    def test_round3_probe_keeps_a_genuinely_missing_component(self):
        def fn(p):
            return {"supported": False,
                    "reason": "The passage does not mention the doubling figure."}
        flags = self._flags(fn)
        self.assertIn("partial_support", flags)

    def test_double_negative_flags_with_escalated_marker(self):
        def fn(p):
            return {"supported": False, "reason": "the figure is in no source"}
        flags = self._flags(fn, sources=self._sources(with_claims=True))
        self.assertIn("partial_support", flags)
        self.assertTrue(flags["partial_support"]["escalated"])
        self.assertIn("figure", flags["partial_support"]["reason"])

    def test_overcite_flag_when_union_minus_source_still_entails(self):
        # recall passes; p2 backs nothing alone; union without p2 still entails
        def fn(p):
            return {"supported": True, "reason": "covered"}
        flags = self._flags(fn)
        oc = flags.get("over_citation", {}).get("sources", [])
        self.assertEqual([s["paper_id"] for s in oc], ["p2"])

    def test_no_overcite_when_the_source_is_needed(self):
        # the union-minus-p2 probe (no "Second source" label) fails -> p2 needed
        def fn(p):
            return {"supported": "Second source" in p, "reason": "needs p2"}
        flags = self._flags(fn)
        self.assertNotIn("over_citation", flags)

    def test_split_probe_votes_do_not_nudge(self):
        # over-cite needs a UNANIMOUS probe (t28's hackenburg false nudge came
        # from one lenient call) — a 2-1 probe majority must NOT flag
        n = [0]
        def fn(p):
            n[0] += 1
            # calls 1-2 = round 1 (agreeing) ; calls 3-5 = the p2 probe votes
            return {"supported": n[0] != 4, "reason": "ok"}
        flags = self._flags(fn)
        self.assertNotIn("over_citation", flags)

    def test_individually_supporting_sources_are_never_overcite_probed(self):
        probed = []
        def fn(p):
            probed.append(p)
            return {"supported": True, "reason": "ok"}
        self._flags(fn, evidences=self._evidences(p1_supported=True))
        # 1 vote-set (2 agreeing calls) for round 1 + ONE full-tally probe
        # (p2 only, all 3 votes — the unanimity bar needs the full tally)
        self.assertEqual(len(probed), 5)


class TestPartialSupportViewer(unittest.TestCase):

    def _analysis(self, partial=True):
        e1 = {"paper_id": "p1", "source_title": "Energy and AI", "supported": True,
              "sentence": "Data-centre electricity use grows.", "page": 1, "snippet": "Data"}
        e2 = {"paper_id": "p2", "source_title": "Macroeconomics of AI", "supported": False,
              "sentence": "The productivity effect is modest.", "page": 2, "snippet": "The"}
        c = {"id": "t69", "text": CLAIM, "markers": ["iea", "acemoglu"],
             "paper_ids": ["p1", "p2"], "verdict": "supported", "method": "llm",
             "reason": "fragment", "evidence": e1, "evidences": [e1, e2]}
        if partial:
            c["partial_support"] = {"reason": "the doubling-by-2030 figure is in no source",
                                    "votes": "2-0"}
        return {"text_claims": [c],
                "sources": [{"paper_id": "p1", "key": "iea", "filename": "iea.txt",
                             "title": "Energy and AI"},
                            {"paper_id": "p2", "key": "acemoglu", "filename": "acemoglu.txt",
                             "title": "Macroeconomics of AI"}],
                "coverage": {"totals": {"claims": 1, "supported": 1, "unsupported": 0,
                                        "own": 0, "omitted": 0}},
                "metadata": {}, "omitted": []}

    def _render(self, partial=True):
        out = os.path.join(tempfile.mkdtemp(), "v.html")
        viewer.generate(self._analysis(partial), out)
        with open(out, encoding="utf-8") as f:
            return f.read()

    def test_partial_chip_note_and_filter_present(self):
        page = self._render(partial=True)
        self.assertIn("partial support?", page)                 # the chip
        self.assertIn("partialchip", page)
        self.assertIn("partial-note", page)
        self.assertIn('data-f="partial"', page)                 # filter button
        self.assertIn("Partial support (1)", page)
        self.assertIn("doubling-by-2030", page)                 # the reason text

    def test_card_carries_partial_class(self):
        page = self._render(partial=True)
        card = page.split('id="card-t69"')[0].rsplit("<div", 1)[1]
        self.assertIn("partial", card)                          # class list on the card div

    def test_confidence_is_low_for_partial(self):
        conf = viewer._confidence(self._analysis(partial=True)["text_claims"][0])
        self.assertEqual(conf[0], "low")

    def test_plain_supported_card_has_no_partial_markup(self):
        page = self._render(partial=False)
        body = page.split("<style")[0]                          # CSS always defines the class
        cards = _no_legend(page)                                # legend swatches show every chip label
        self.assertNotIn("partial support?", cards)
        self.assertNotIn("partialchip", body)
        self.assertNotIn('data-f="partial"', page)
        self.assertNotIn("over-cited?", cards)
        self.assertNotIn('data-f="overcite"', page)

    def _render_analysis(self, a):
        out = os.path.join(tempfile.mkdtemp(), "v.html")
        viewer.generate(a, out)
        with open(out, encoding="utf-8") as f:
            return f.read()

    def test_overcite_chip_note_and_filter_present(self):
        a = self._analysis(partial=False)
        a["text_claims"][0]["over_citation"] = {
            "sources": [{"paper_id": "p2", "source_title": "Macroeconomics of AI"}]}
        page = self._render_analysis(a)
        self.assertIn("over-cited?", page)                      # the chip
        self.assertIn("overcite-note", page)
        self.assertIn('data-f="overcite"', page)                # filter button
        self.assertIn("Over-cited (1)", page)
        card = page.split('id="card-t69"')[0].rsplit("<div", 1)[1]
        self.assertIn("overcite", card)                         # class on the card div

    def test_escalated_partial_note_mentions_decomposed_claims(self):
        a = self._analysis(partial=True)
        a["text_claims"][0]["partial_support"]["escalated"] = True
        page = self._render_analysis(a)
        self.assertIn("full decomposed claims", page)


if __name__ == "__main__":
    unittest.main()
