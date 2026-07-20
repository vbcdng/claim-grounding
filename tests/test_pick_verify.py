"""Round-5 fix: batched pick-verify audit of the covering block
(matcher._verify_covering) — verify picks, dedup duplicates, add missed
named-specific components, tag common-knowledge gaps grey; proof_state counts
only real gaps. Offline — LLM + probe stubbed.

Run:  venv/bin/python3 -m unittest tests.test_pick_verify -v
"""
import os
import sys
import json
import tempfile
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.papertrail import matcher, viewer

S1 = "The tradition is documented across seventeenth-century England."
S1_DUP = "Seventeenth-century English records document the tradition widely."
S2 = "Bottles were buried beneath hearths and thresholds."
PROMPT = open(os.path.join(os.path.dirname(__file__), "..",
                           "config/prompts/pt_pick_verify_prompt.txt"),
              encoding="utf-8").read()


def _cov(covered, uncovered=None):
    return {"covered": covered, "uncovered": uncovered or []}


def _ce(part, sentence):
    return {"component": part, "paper_id": "p1", "source_title": "Src",
            "sentence": sentence, "page": 1, "snippet": sentence[:20]}


def _llm(payload):
    llm = MagicMock()
    llm.call.return_value = payload if isinstance(payload, str) else json.dumps(payload)
    return llm


class TestVerifyCovering(unittest.TestCase):

    def test_duplicate_picks_collapse_to_best(self):
        cov = _cov([_ce("the dating", S1), _ce("the dating", S1_DUP)])
        matcher._verify_covering("claim", cov, _llm(
            {"verified": [{"part": "the dating", "keep": [2]}],
             "new_components": [], "common_knowledge": []}), PROMPT)
        self.assertEqual([c["sentence"] for c in cov["covered"]], [S1_DUP])
        self.assertEqual(cov["uncovered"], [])
        self.assertTrue(cov["pick_verified"])

    def test_failed_pick_reprobes_then_amber(self):
        cov = _cov([_ce("hearth burial", S1)])         # S1 doesn't prove it
        resp = {"verified": [{"part": "hearth burial", "keep": []}],
                "new_components": [], "common_knowledge": []}
        # probe finds the real sentence -> moves to covered via escalation
        probe = MagicMock(return_value={"component": "hearth burial",
                                        "paper_id": "p1", "sentence": S2,
                                        "snippet": S2[:20], "via": "escalation"})
        cov1 = _cov([_ce("hearth burial", S1)])
        matcher._verify_covering("claim", cov1, _llm(resp), PROMPT, probe=probe)
        self.assertEqual([c["sentence"] for c in cov1["covered"]], [S2])
        self.assertEqual(cov1["uncovered"], [])
        # probe finds nothing -> honest amber
        matcher._verify_covering("claim", cov, _llm(resp), PROMPT,
                                 probe=MagicMock(return_value=None))
        self.assertEqual(cov["covered"], [])
        self.assertEqual(cov["uncovered"], ["hearth burial"])

    def test_paraphrase_of_existing_part_not_added_twice(self):
        cov = _cov([], uncovered=["end grain atop a post is where rainwater collects longest"])
        resp = {"verified": [{"part": "end grain atop a post is where rainwater collects longest",
                              "keep": [], "no_proof_kind": "needs_source"}],
                "new_components": ["exposed end grain atop a post collecting rainwater"]}
        matcher._verify_covering("claim", cov, _llm(resp), PROMPT,
                                 probe=MagicMock(return_value=None))
        self.assertEqual(len(cov["uncovered"]), 1)   # near-dup filtered

    def test_missed_named_specific_added_and_probed(self):
        cov = _cov([_ce("the tradition", S1)])
        resp = {"verified": [{"part": "the tradition", "keep": [1]}],
                "new_components": ["the Finnish setting"], "common_knowledge": []}
        matcher._verify_covering("claim", cov, _llm(resp), PROMPT,
                                 probe=MagicMock(return_value=None))
        self.assertIn("the Finnish setting", cov["uncovered"])
        self.assertEqual(len(cov["covered"]), 1)       # verified pick kept

    def test_common_knowledge_tagged_from_uncovered(self):
        cov = _cov([_ce("the finding", S1)], uncovered=["rainwater pools on flat tops"])
        resp = {"verified": [{"part": "the finding", "keep": [1]},
                             {"part": "rainwater pools on flat tops", "keep": [],
                              "no_proof_kind": "everyday_commonplace"}],
                "new_components": []}
        matcher._verify_covering("claim", cov, _llm(resp), PROMPT,
                                 probe=MagicMock(return_value=None))
        self.assertEqual(cov["common_knowledge"], ["rainwater pools on flat tops"])
        self.assertIn("rainwater pools on flat tops", cov["uncovered"])

    def test_needs_source_and_missing_kind_stay_amber(self):
        cov = _cov([], uncovered=["a thesis", "a superlative"])
        resp = {"verified": [{"part": "a thesis", "keep": [],
                              "no_proof_kind": "needs_source"},
                             {"part": "a superlative", "keep": []}],  # kind omitted
                "new_components": []}
        matcher._verify_covering("claim", cov, _llm(resp), PROMPT,
                                 probe=MagicMock(return_value=None))
        self.assertEqual(cov["common_knowledge"], [])   # default is needs_source

    def test_unparseable_response_fails_open(self):
        cov = _cov([_ce("a", S1), _ce("b", S2)], uncovered=["c"])
        matcher._verify_covering("claim", cov, _llm("not json at all"), PROMPT)
        self.assertEqual(len(cov["covered"]), 2)       # untouched
        self.assertEqual(cov["uncovered"], ["c"])
        self.assertTrue(cov["pick_verified"])          # checked, nothing usable

    def test_unreviewed_part_keeps_its_picks(self):
        cov = _cov([_ce("a", S1), _ce("b", S2)])
        resp = {"verified": [{"part": "a", "keep": [1]}],  # "b" omitted
                "new_components": [], "common_knowledge": []}
        matcher._verify_covering("claim", cov, _llm(resp), PROMPT,
                                 probe=MagicMock(return_value=None))
        self.assertEqual([c["component"] for c in cov["covered"]], ["a", "b"])


class TestRound7NamedSpecifics(unittest.TestCase):
    """Round-7 fix A: deterministic entity check + majority-of-3 pick
    verification for named-specific components (r4 t6 Finland, r6 t3 Agta)."""

    def test_named_specifics_extraction(self):
        specs = matcher._named_specifics(
            "Data on the Agta of the Philippines show a 1985 sample and 24% less hunting.")
        self.assertIn("Agta of the Philippines", specs)
        self.assertIn("1985", specs)
        self.assertIn("24%", specs)
        self.assertNotIn("Data", specs)          # sentence-initial word
        self.assertEqual(matcher._named_specifics("no capitals here at all"), [])

    def test_possessive_matches_bare_form(self):
        # "The GMB's sections" must not amber when picks say "GMB" (train-b2)
        claim = "The GMB's sections were rationalised in 2006."
        s = "GMB restructured its sections during the 2006 congress."
        cov = _cov([_ce("sections rationalised in 2006", s)])
        resp = {"verified": [{"part": "sections rationalised in 2006", "keep": [1]}],
                "new_components": [], "common_knowledge": []}
        matcher._verify_covering(claim, cov, _llm(resp), PROMPT,
                                 probe=MagicMock(return_value=None))
        self.assertEqual(cov["uncovered"], [])

    def test_punctuation_ends_a_run(self):
        # '…Hot Mum", Alex Massie wrote…' is two entities, not one
        specs = matcher._named_specifics(
            'Concerning "It Ain\'t Half Hot Mum", Alex Massie wrote in January 2019.')
        self.assertIn("Alex Massie", specs)
        self.assertNotIn("Ain't Half Hot Mum Alex Massie", specs)

    def test_entity_check_adds_missing_specific_as_amber(self):
        # the pick proves the generic part but never names Agta/Philippines;
        # the LLM pass returns no new components (the r6 t3 failure) — the
        # regex entity check must still surface it
        claim = "Hunting declined among the Agta of the Philippines."
        cov = _cov([_ce("hunting declined", S1)])
        resp = {"verified": [{"part": "hunting declined", "keep": [1]}],
                "new_components": [], "common_knowledge": []}
        matcher._verify_covering(claim, cov, _llm(resp), PROMPT,
                                 probe=MagicMock(return_value=None))
        self.assertIn("Agta of the Philippines", cov["uncovered"])

    def test_entity_check_probe_can_cover(self):
        claim = "Hunting declined among the Agta of the Philippines."
        cov = _cov([_ce("hunting declined", S1)])
        resp = {"verified": [{"part": "hunting declined", "keep": [1]}],
                "new_components": [], "common_knowledge": []}
        hit = {"component": "Agta of the Philippines", "paper_id": "p1",
               "source_title": "Src", "sentence": "Among the Agta of the "
               "Philippines, camp records show less hunting.", "via": "probe"}
        matcher._verify_covering(claim, cov, _llm(resp), PROMPT,
                                 probe=MagicMock(return_value=hit))
        self.assertEqual(cov["uncovered"], [])
        self.assertIn("Agta of the Philippines",
                      [c["component"] for c in cov["covered"]])

    def test_entity_check_skips_specific_already_in_picks(self):
        claim = "Hunting declined among the Agta of the Philippines."
        s = "Camp data from the Agta of the Philippines show hunting fell."
        cov = _cov([_ce("hunting declined", s)])
        resp = {"verified": [{"part": "hunting declined", "keep": [1]}],
                "new_components": [], "common_knowledge": []}
        matcher._verify_covering(claim, cov, _llm(resp), PROMPT,
                                 probe=MagicMock(return_value=None))
        self.assertEqual(cov["uncovered"], [])

    def _vote_llm(self, *keeps):
        llm = MagicMock()
        llm.call.side_effect = [json.dumps(
            {"verified": [{"part": "capped by Wales in 1988", "keep": k}],
             "new_components": [], "common_knowledge": []}) for k in keeps]
        return llm

    def test_majority_two_drops_beat_one_keep(self):
        s = "She joined the club at a young age."      # proves nothing specific
        cov = _cov([_ce("capped by Wales in 1988", s)])
        llm = self._vote_llm([1], [], [])              # keep, drop, drop
        matcher._verify_covering("She was capped by Wales in 1988.", cov, llm,
                                 PROMPT, probe=MagicMock(return_value=None))
        self.assertEqual(llm.call.call_count, 3)       # majority pass ran
        self.assertIn("capped by Wales in 1988", cov["uncovered"])

    def test_majority_keeps_against_one_flaky_drop(self):
        s = "Wales capped her in 1988, a first for the club."
        cov = _cov([_ce("capped by Wales in 1988", s)])
        llm = self._vote_llm([], [1], [1])             # drop, keep, keep
        matcher._verify_covering("She was capped by Wales in 1988.", cov, llm,
                                 PROMPT, probe=MagicMock(return_value=None))
        self.assertEqual(cov["uncovered"], [])
        self.assertEqual([c["component"] for c in cov["covered"]],
                         ["capped by Wales in 1988"])

    def test_no_specifics_stays_single_call(self):
        cov = _cov([_ce("the dating", S1)])
        llm = _llm({"verified": [{"part": "the dating", "keep": [1]}],
                    "new_components": [], "common_knowledge": []})
        matcher._verify_covering("claim about dating", cov, llm, PROMPT,
                                 probe=MagicMock(return_value=None))
        self.assertEqual(llm.call.call_count, 1)


class TestProofStateWithCommonKnowledge(unittest.TestCase):

    def _out(self, uncovered, common):
        return {"verdict": "supported", "paper_ids": ["p1"],
                "covering": {"covered": [], "uncovered": uncovered,
                             "common_knowledge": common, "pick_verified": True},
                "covering_checked": True}

    def test_all_gaps_common_knowledge_is_full(self):
        # proof_state derivation lives in a closure; exercise via viewer-level
        # contract instead: matcher.run path is covered in test_proof_state —
        # here assert the SAME rule through _confidence + badge rendering.
        c = {"id": "t1", "text": "x", "markers": ["k"], "paper_ids": ["p1"],
             "verdict": "supported", "method": "llm", "proof_state": "full",
             "covering_checked": True, "evidences": [],
             "covering": {"covered": [], "uncovered": ["obvious bit"],
                          "common_knowledge": ["obvious bit"], "pick_verified": True}}
        out = os.path.join(tempfile.mkdtemp(), "v.html")
        viewer.generate({"text_claims": [c],
                         "sources": [{"paper_id": "p1", "key": "p1",
                                      "filename": "p1.txt", "title": "S"}],
                         "coverage": {"totals": {"claims": 1, "supported": 1,
                                                 "unsupported": 0, "own": 0,
                                                 "omitted": 0}},
                         "metadata": {}, "omitted": []}, out)
        page = open(out, encoding="utf-8").read()
        pre, _, rest = page.partition('<details class="legend">')
        page = pre + rest.partition('</details>')[2]   # drop the static legend
        self.assertIn("commonly known", page)
        self.assertIn("obvious bit", page)
        self.assertNotIn("No evidence shown for", page)   # grey, not amber
        self.assertNotIn("NOT PROVEN AS WRITTEN", page)   # real gaps only


if __name__ == "__main__":
    unittest.main()
