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


T10 = ("Tellingly, individual forecasters' near-term performance was "
       "statistically indistinguishable from simple algorithms.")
AGTA = ("Consistent with this, a study of the Agta of the Philippines found "
        "individuals in camps more engaged in agricultural work.")


class TestOrdinaryOpener(unittest.TestCase):
    """Card 45 (gate row essay/t10): a single sentence-initial ordinary word is
    not a name, however rare it is in the source; real names still arm."""

    def test_t10_tellingly_disarms(self):
        self.assertEqual(matcher._subject_tokens(T10), [])
        self.assertEqual(matcher._claim_entity_sets(T10), [])

    def test_class_not_just_adverbs(self):
        # the card's point: the defect is 'first word absent from the source',
        # not 'framing adverb' — plain openers disarm too
        for t in ("Individual forecasters were indistinguishable from simple rules.",
                  "Among fields, the life sciences showed the highest rate.",
                  "Previous studies have investigated the factors.",
                  "Crucially, once the analysis adjusted for cholesterol, it vanished.",
                  "Real-world energy use was 20.8% greater than test values.",
                  "Faculty with foreign degrees make up one-tenth of the total."):
            self.assertEqual(matcher._subject_tokens(t), [], t)

    def test_single_token_names_still_arm(self):
        self.assertEqual(matcher._subject_tokens(
            "Finland has the highest rate of the tradition."), ["finland"])
        self.assertEqual(matcher._subject_tokens(
            "Leicht argues middle powers should skip strategy-writing."), ["leicht"])
        self.assertEqual(matcher._subject_tokens(
            "Engelmann et al. report evidence that chimpanzees prepare."),
            ["engelmann"])
        self.assertEqual(matcher._subject_tokens(
            "Agta foragers spent more time in camp."), ["agta"])

    def test_inner_capital_marks_a_name(self):
        self.assertEqual(matcher._subject_tokens(
            "ReviewGuard was evaluated on 20,861 papers."), ["reviewguard"])
        self.assertEqual(matcher._subject_tokens(
            "InSight traveled 483 million km."), ["insight"])

    def test_after_an_article_capitals_are_evidence(self):
        # "The Court ..." is capitalized mid-sentence: old rules stand
        self.assertEqual(matcher._subject_tokens(
            "The Court announced judgment in favor of the bank."), ["court"])

    def test_agta_still_guarded_by_its_midsentence_name(self):
        # before card 45 this claim's 'subject' was the word 'consistent';
        # now the guard rests on the real name, as it should
        self.assertEqual(matcher._subject_tokens(AGTA), [])
        self.assertEqual(matcher._claim_entity_sets(AGTA),
                         [("Agta of the Philippines", ["agta", "philippines"])])

    def test_is_ordinary_word(self):
        for w in ("Tellingly", "Crucially", "Similarly", "Individual",
                  "Adjustments", "Real-world", "Previous"):
            self.assertTrue(matcher._is_ordinary_word(w), w)
        for w in ("Majid", "Finland", "Agta", "Leicht", "Engelmann", "Marjot",
                  "Tarnitz", "ReviewGuard", "Kim"):
            self.assertFalse(matcher._is_ordinary_word(w), w)

    def test_frozen_list_is_loaded(self):
        self.assertGreater(len(matcher._ORDINARY_WORDS), 10000)
        for name in ("majid", "finland", "agta", "leicht", "engelmann"):
            self.assertNotIn(name, matcher._ORDINARY_WORDS)


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

    def test_t10_positive_survives_when_opener_absent_from_source(self):
        # card 45: the source never prints 'tellingly'; the judge's unanimous
        # positive must no longer be thrown out for it
        c = {"id": "t10", "text": T10, "markers": ["tetlock"], "paper_ids": ["p1"]}
        proof = ("Individual forecasters performed no better than simple "
                 "statistical baselines over short horizons.")
        srcs = {"p1": {"title": "forecasting study", "key": "tetlock",
                       "sentences": [{"text": "We ran a tournament."},
                                     {"text": proof}], "claims": []}}
        res = _run([c], srcs, _llm(proof, True))
        out = res["text_claims"][0]
        self.assertEqual(out["verdict"], "supported")
        self.assertNotIn("subject_guard", out)

    def test_agta_claim_still_rejected_when_source_never_names_agta(self):
        c = {"id": "t3", "text": AGTA, "markers": ["forager"], "paper_ids": ["p1"]}
        proof = ("Foragers in settled camps did more agricultural work than "
                 "those in forest camps.")
        srcs = {"p1": {"title": "forager study", "key": "forager",
                       "sentences": [{"text": proof}], "claims": []}}
        res = _run([c], srcs, _llm(proof, True))
        out = res["text_claims"][0]
        self.assertEqual(out["verdict"], "unsupported")
        self.assertIn("Agta of the Philippines", out["reason"])

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
