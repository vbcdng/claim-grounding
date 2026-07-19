"""Subject-entity guard (2026-07-12, WiCE train2 waleedmajid false-support):
a fulltext-extraction positive from a source whose entire text never names the
claim's LEADING subject entity is rejected; the arbiter rescue and component
rescue must not re-buy the same positive. Strictly leading + a frozen
common-words set keep the guard off ordinary sentence openers and buried
attribution shapes ("... Shin and colleagues found ..."). No API calls.

Run:  venv/bin/python3 -m unittest tests.test_subject_guard -v
"""
import os
import sys
import json
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.papertrail import matcher, arbiter


class TestSubjectTokens(unittest.TestCase):
    """_subject_tokens: leading proper-noun run, folded, common-filtered."""

    def test_leading_name_is_the_subject(self):
        self.assertEqual(matcher._subject_tokens(
            "Majid has played in several World Cup of Pool events."), ["majid"])

    def test_common_opener_disarms(self):
        # 'Reviews' is capitalized only because sentence-initial
        self.assertEqual(matcher._subject_tokens(
            "Reviews of the randomized-trial literature reach the same verdict."), [])

    def test_collapsed_multitoken_run_disarms(self):
        # paper1 t27 (2026-07-17 gate): "Frontier AI" loses 'AI' to the length
        # filter; demanding the generic fragment 'frontier' verbatim in the
        # source killed a true tail_rescue positive (salvi2025 proves the GPT-4
        # persuasion claim, never says 'frontier'). A multi-token run collapsed
        # to one checkable token is a phrase fragment -> guard off.
        self.assertEqual(matcher._subject_tokens(
            "Frontier AI is already a potent instrument of persuasion."), [])

    def test_fully_kept_multitoken_run_still_arms(self):
        # false-alarm control: a real two-token name keeps the guard armed
        self.assertEqual(matcher._subject_tokens(
            "Marilyn Castonguay stars in the film."), ["marilyn", "castonguay"])

    def test_article_then_lowercase_has_no_subject(self):
        # the eggs t29 shape: the true attribution is buried mid-claim and
        # must NOT arm the guard (paper text can lack the author byline)
        self.assertEqual(matcher._subject_tokens(
            "The first is diabetes, both as an outcome and as an effect "
            "modifier. Shin and colleagues found no overall association."), [])

    def test_adverb_opener_disarms(self):
        self.assertEqual(matcher._subject_tokens(
            "Without dialogue, the film stars Marilyn Castonguay."), [])
        self.assertEqual(matcher._subject_tokens(
            "Nor would freely available open-weight models close the gap."), [])

    def test_pronoun_disarms(self):
        self.assertEqual(matcher._subject_tokens(
            "She competed in the women's team foil event."), [])

    def test_quoted_multiword_title(self):
        self.assertEqual(matcher._subject_tokens(
            '"Panzer Dragoon Zwei" was released in March 1996 in Japan.'),
            ["panzer", "dragoon", "zwei"])

    def test_leading_article_is_skipped_not_fatal(self):
        self.assertEqual(matcher._subject_tokens(
            "The Shining premiered in 1980."), ["shining"])

    def test_diacritics_fold(self):
        self.assertEqual(matcher._subject_tokens(
            "Divna Ljubojević is a Serbian singer."), ["divna", "ljubojevic"])

    def test_plural_common_noun_disarms(self):
        # 'Americans' folds to common 'american'; 'Many' is common
        self.assertEqual(matcher._subject_tokens(
            "Many Americans believe the economy is weak."), [])

    def test_connector_run(self):
        self.assertEqual(matcher._subject_tokens(
            "University of Oklahoma won the conference meet."),
            ["university", "oklahoma"])


class TestSubjectInSource(unittest.TestCase):
    def _src(self, *sentences):
        return {"sentences": [{"text": s} for s in sentences]}

    def test_present(self):
        self.assertTrue(matcher._subject_in_source(
            ["majid"], self._src("Waleed Majid reached the final.")))

    def test_absent(self):
        self.assertFalse(matcher._subject_in_source(
            ["majid"], self._src("Qatar reached the quarter-finals.")))

    def test_fold_both_directions(self):
        self.assertTrue(matcher._subject_in_source(
            ["ljubojevic"], self._src("Divna Ljubojević sang in Belgrade.")))


CLAIM = ("Majid has played in several World Cup of Pool events representing "
         "Qatar, including reaching the quarter-finals at the 2015 event.")
QF_SENT = ("Qatar put on a masterful performance to reach the quarter-finals "
           "for the first time in their history.")


def _fake_cosine(a, b, **kw):
    return [[0.5] * len(b) for _ in a]


def _llm(extract_sentence, judge_supported):
    """per-source judge says unsupported (forces the fulltext fallback);
    extraction finds `extract_sentence`; the fulltext judge says
    `judge_supported`."""
    llm = MagicMock()
    llm.model = "fake/judge"

    def call(p, **kw):
        if "evidence finder" in p:
            return json.dumps({"sentences": [extract_sentence]})
        if "TAKEN TOGETHER" in p:
            return json.dumps({"supported": judge_supported, "reason": "judged"})
        return json.dumps({"supported": False, "reason": "not in candidates"})

    llm.call.side_effect = call
    return llm


def _run(claims, sources, llm, **kw):
    with patch.object(matcher.embeddings, "cosine_matrix", side_effect=_fake_cosine):
        return matcher.run(claims, sources, llm, **kw)


def _sources(sentences):
    return {"p1": {"title": "World Cup of Pool 2015 news", "key": "waleedmajid",
                   "sentences": [{"text": s} for s in sentences], "claims": []}}


def _claim():
    return {"id": "t26", "text": CLAIM, "markers": ["waleedmajid"],
            "paper_ids": ["p1"]}


class TestGuardOnFulltextPath(unittest.TestCase):

    def test_subjectless_source_positive_is_rejected(self):
        # the waleedmajid case: extraction finds the team result, the judge
        # accepts it — the guard must keep the claim red and say why
        srcs = _sources(["Round 2 scores were posted.", QF_SENT])
        res = _run([_claim()], srcs, _llm(QF_SENT, True))
        c = res["text_claims"][0]
        self.assertEqual(c["verdict"], "unsupported")
        self.assertIn("majid", c["reason"])
        self.assertIn("never mentioned", c["reason"])
        self.assertEqual(c["subject_guard"]["missing_from"], ["p1"])

    def test_source_naming_the_subject_is_accepted(self):
        srcs = _sources(["Waleed Majid led Qatar at the World Cup of Pool.",
                         QF_SENT])
        res = _run([_claim()], srcs, _llm(QF_SENT, True))
        c = res["text_claims"][0]
        self.assertEqual(c["verdict"], "supported")
        self.assertEqual(c["method"], "llm_fulltext")
        self.assertNotIn("subject_guard", c)

    def test_common_opener_claim_is_never_guarded(self):
        c = _claim()
        c["text"] = ("Reviews of the tournament praised Qatar's "
                     "quarter-final run at the 2015 event.")
        srcs = _sources(["Round 2 scores were posted.", QF_SENT])
        res = _run([c], srcs, _llm(QF_SENT, True))
        out = res["text_claims"][0]
        self.assertEqual(out["verdict"], "supported")

    def test_component_rescue_skipped_when_all_sources_guarded(self):
        srcs = _sources(["Round 2 scores were posted.", QF_SENT])
        with patch.object(matcher, "_component_rescue") as cr:
            _run([_claim()], srcs, _llm(QF_SENT, True))
            cr.assert_not_called()


class TestClaimEntitySets(unittest.TestCase):
    """_claim_entity_sets: leading subject + non-leading MULTI-TOKEN runs
    (the wildskin/Castonguay extension), compounds and commons filtered."""

    def test_midsentence_person_is_an_entity(self):
        sets = matcher._claim_entity_sets(
            "Without dialogue, the film stars Marilyn Castonguay as a woman.")
        self.assertEqual(sets, [("Marilyn Castonguay",
                                 ["marilyn", "castonguay"])])

    def test_nationality_compound_is_filtered(self):
        # 'Egyptian-born French' must NOT arm the guard (measured false fire)
        sets = matcher._claim_entity_sets(
            "Tiana Tolstoi is an Egyptian-born French model.")
        self.assertEqual([d for d, _ in sets], ["tiana tolstoi"])

    def test_single_token_nonleading_is_ignored(self):
        # too alias-prone: only multi-token non-leading runs count
        sets = matcher._claim_entity_sets(
            "The results were confirmed by Shin and colleagues.")
        self.assertEqual(sets, [])

    def test_leading_and_nonleading_combine(self):
        sets = matcher._claim_entity_sets(
            "Majid has played in several World Cup of Pool events.")
        self.assertEqual([d for d, _ in sets],
                         ["majid", "World Cup of Pool"])


class TestGuardOnMidSentenceEntity(unittest.TestCase):
    """The wildskin class: a mid-sentence star name absent from the source."""

    def test_midsentence_entity_absent_blocks_positive(self):
        c = {"id": "t16", "markers": ["wildskin"], "paper_ids": ["p1"],
             "text": ("Without dialogue, the film stars Marilyn Castonguay "
                      "as a woman who finds a python in her apartment.")}
        film_sent = ("The wordless short revolves around a young woman who "
                     "discovers a python in her apartment.")
        srcs = {"p1": {"title": "festival report", "key": "wildskin",
                       "sentences": [{"text": film_sent}], "claims": []}}
        res = _run([c], srcs, _llm(film_sent, True))
        out = res["text_claims"][0]
        self.assertEqual(out["verdict"], "unsupported")
        self.assertIn("Marilyn Castonguay", out["reason"])
        self.assertEqual(out["subject_guard"]["missing_from"], ["p1"])

    def test_midsentence_entity_present_passes(self):
        c = {"id": "t16", "markers": ["wildskin"], "paper_ids": ["p1"],
             "text": ("Without dialogue, the film stars Marilyn Castonguay "
                      "as a woman who finds a python in her apartment.")}
        film_sent = ("Marilyn Castonguay carries the wordless short about a "
                     "woman who discovers a python in her apartment.")
        srcs = {"p1": {"title": "festival report", "key": "wildskin",
                       "sentences": [{"text": film_sent}], "claims": []}}
        res = _run([c], srcs, _llm(film_sent, True))
        out = res["text_claims"][0]
        self.assertEqual(out["verdict"], "supported")


class TestGuardOnArbiterRescue(unittest.TestCase):

    def _fetched(self):
        c = _claim()
        c["verdict"] = "unsupported"
        c["evidences"] = [{"paper_id": "p1", "sentence": QF_SENT,
                           "supported": False, "source_title": "news"}]
        c["subject_guard"] = {"subject": "Majid", "missing_from": ["p1"]}
        c["arbiter"] = {"model": "fake/arbiter", "prompt_sha": "x",
                        "trigger": "unsupported",
                        "action": "wrong_or_insufficient_evidence",
                        "missing_subclaim": "", "rewrite_suggestion": "",
                        "proofs": [QF_SENT], "quotes_dropped": 0,
                        "conflict": None, "why": "w"}
        return c

    def test_rescue_never_rebuys_a_guarded_source(self):
        sources = {"p1": {"title": "news",
                          "sentences": [{"text": QF_SENT}]}}
        llm = MagicMock()
        llm.model = "fake/judge"
        llm.call.return_value = json.dumps({"supported": True, "reason": "r"})
        s = arbiter.rescue([self._fetched()], sources, llm, workers=1)
        self.assertEqual(s["flipped"], [])
        self.assertEqual(s["held"], ["t26"])
        llm.call.assert_not_called()   # no window survives — no judge spend

    def test_rescue_still_works_from_an_unguarded_source(self):
        c = self._fetched()
        c["paper_ids"] = ["p1", "p2"]
        c["subject_guard"] = {"subject": "Majid", "missing_from": ["p1"]}
        c["arbiter"]["proofs"] = ["Waleed Majid won his singles match."]
        sources = {"p1": {"title": "news", "sentences": [{"text": QF_SENT}]},
                   "p2": {"title": "bio", "sentences":
                          [{"text": "Waleed Majid won his singles match."}]}}
        llm = MagicMock()
        llm.model = "fake/judge"
        llm.call.return_value = json.dumps({"supported": True, "reason": "r"})
        s = arbiter.rescue([c], sources, llm, workers=1)
        self.assertEqual(s["flipped"], ["t26"])
        self.assertEqual(c["verdict"], "supported")


if __name__ == "__main__":
    unittest.main()
