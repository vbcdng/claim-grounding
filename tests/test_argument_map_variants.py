"""Claim variants + map diff (slot A2) — no API calls (fake LLM / fake embeddings).

Run:  venv/bin/python3 -m unittest tests.test_argument_map_variants -v
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.papertrail import argument_map


class FakeLLM:
    def __init__(self, response, model="fake-model"):
        self.response = response
        self.model = model
        self.calls = 0

    def call(self, prompt, temperature=0.0, max_output_tokens=2048):
        self.calls += 1
        self.last_prompt = prompt
        return self.response


# t1 and t4 are near-identical restatements (lexically strong); t5 is a hedged
# paraphrase of t1 (lexically weak); t2/t3 are unrelated to everything.
NODES = [
    {"id": "t1", "text": "Cities should ban private cars from downtown areas."},
    {"id": "t2", "text": "Car bans cut street-level pollution by 30 percent."},
    {"id": "t3", "text": "But car bans hurt downtown retail revenue."},
    {"id": "t4", "text": "Cities should ban private cars from downtown areas entirely."},
    {"id": "t5", "text": "Urban centers may benefit from restricting private vehicles."},
]


def _pair(payload, a, b):
    for p in payload["pairs"]:
        if {p["a"], p["b"]} == {a, b}:
            return p
    return None


class TestLexicalTier(unittest.TestCase):
    def test_strong_restatement_grouped_canonical_is_earliest(self):
        payload = argument_map.find_variants(NODES)
        self.assertEqual(payload["method"], "lexical")
        p = _pair(payload, "t1", "t4")
        self.assertIsNotNone(p)
        self.assertEqual(p["strength"], "strong")
        self.assertEqual(len(payload["groups"]), 1)
        g = payload["groups"][0]
        self.assertEqual(g["canonical"], "t1")     # document order wins
        self.assertEqual(g["members"], ["t1", "t4"])

    def test_unrelated_claims_not_paired(self):
        payload = argument_map.find_variants(NODES)
        self.assertIsNone(_pair(payload, "t2", "t3"))

    def test_weak_pair_does_not_merge(self):
        # A lexically-weak pair (ratio ~0.85) must appear in pairs but form no group.
        nodes = [
            {"id": "a", "text": "The committee approved the budget for 2024."},
            {"id": "b", "text": "The committee approved a version of the budget for 2025."},
        ]
        payload = argument_map.find_variants(nodes)
        p = _pair(payload, "a", "b")
        self.assertIsNotNone(p)
        self.assertEqual(p["strength"], "weak")
        self.assertEqual(payload["groups"], [])
        self.assertEqual(payload["n_weak_pairs"], 1)

    def test_idless_or_empty_nodes_skipped(self):
        nodes = NODES + [{"id": "", "text": "no id"}, {"id": "t9", "text": "  "}]
        payload = argument_map.find_variants(nodes)
        for p in payload["pairs"]:
            self.assertNotIn("t9", (p["a"], p["b"]))


def _fake_embed(basis, order):
    def embed(texts):
        return [basis[i] for i in order[:len(texts)]]
    return embed


class TestEmbeddingsTier(unittest.TestCase):
    # t1/t5 share a direction (paraphrase); everything else near-orthogonal.
    BASIS = {"t1": [1.0, 0.0, 0.0], "t5": [0.99, 0.14, 0.0],
             "t2": [0.0, 1.0, 0.0], "t3": [0.0, 0.0, 1.0],
             "t4": [0.5, 0.5, 0.5]}

    def test_cosine_is_retrieval_only_never_strong(self):
        # Real-data rule (paper1): even cos ~0.99 only proposes a WEAK pair —
        # a negated near-twin scored 0.949 there, so cosine must never merge.
        embed = _fake_embed(self.BASIS, [n["id"] for n in NODES])
        payload = argument_map.find_variants(NODES, embed_fn=embed)
        self.assertEqual(payload["method"], "lexical+embeddings")
        p = _pair(payload, "t1", "t5")
        self.assertIsNotNone(p)
        self.assertEqual(p["strength"], "weak")
        self.assertIsNotNone(p["cos"])
        # Only the lexical-strong t1+t4 pair forms a group.
        self.assertEqual(len(payload["groups"]), 1)
        self.assertNotIn("t5", payload["groups"][0]["members"])

    def test_llm_confirm_promotes_cosine_candidate_into_group(self):
        embed = _fake_embed(self.BASIS, [n["id"] for n in NODES])
        # Confirm both candidate pairs: t1~t4 (lexical) and t1~t5 (cosine).
        resp = json.dumps({"pairs": [
            {"n": 1, "relation": "restatement", "why": "same thesis"},
            {"n": 2, "relation": "hedged_variant", "why": "weaker form"},
        ]})
        payload = argument_map.find_variants(NODES, embed_fn=embed,
                                             llm=FakeLLM(resp))
        self.assertEqual(payload["method"], "lexical+embeddings+llm")
        g = payload["groups"][0]
        self.assertEqual(g["canonical"], "t1")
        self.assertIn("t4", g["members"])
        self.assertIn("t5", g["members"])


class TestNegationGuard(unittest.TestCase):
    def test_negated_near_twin_capped_at_weak(self):
        # lex ratio 0.94 — above the strong threshold, so only the negation
        # guard keeps this opposite-meaning pair out of a merge.
        nodes = [
            {"id": "a", "text": "The report applies to the following scenario."},
            {"id": "b", "text": "The report never applies to the following scenario."},
        ]
        payload = argument_map.find_variants(nodes)
        p = _pair(payload, "a", "b")
        self.assertIsNotNone(p)                     # still surfaced as a question
        self.assertEqual(p["strength"], "weak")     # but never auto-merged
        self.assertEqual(payload["groups"], [])


class TestLLMConfirm(unittest.TestCase):
    def _resp(self, items):
        return json.dumps({"pairs": items})

    def test_different_claim_kills_pair(self):
        llm = FakeLLM(self._resp([{"n": 1, "relation": "different_claim",
                                   "why": "scope differs"}]))
        payload = argument_map.find_variants(NODES, llm=llm)
        self.assertEqual(llm.calls, 1)
        self.assertEqual(payload["method"], "lexical+llm")
        self.assertIsNone(_pair(payload, "t1", "t4"))
        self.assertEqual(payload["groups"], [])

    def test_confirm_upgrades_weak_to_strong(self):
        nodes = [
            {"id": "a", "text": "The committee approved the budget for 2024."},
            {"id": "b", "text": "The committee approved a version of the budget for 2025."},
        ]
        llm = FakeLLM(self._resp([{"n": 1, "relation": "hedged_variant",
                                   "why": "same claim, weaker strength"}]))
        payload = argument_map.find_variants(nodes, llm=llm)
        p = _pair(payload, "a", "b")
        self.assertEqual(p["strength"], "strong")
        self.assertEqual(p["llm"]["relation"], "hedged_variant")
        self.assertEqual(len(payload["groups"]), 1)
        self.assertEqual(payload["groups"][0]["relations"].get("b"), "hedged_variant")

    def test_failopen_on_garbage(self):
        before = argument_map.find_variants(NODES)
        llm = FakeLLM("garbage")
        payload = argument_map.find_variants(NODES, llm=llm)
        # Unparseable → heuristic strengths kept, method stays lexical.
        self.assertEqual(payload["method"], "lexical")
        self.assertEqual(len(payload["pairs"]), len(before["pairs"]))
        self.assertEqual(len(payload["groups"]), len(before["groups"]))

    def test_no_verdict_for_pair_fails_open(self):
        llm = FakeLLM(self._resp([{"n": 99, "relation": "restatement", "why": "x"}]))
        payload = argument_map.find_variants(NODES, llm=llm)
        p = _pair(payload, "t1", "t4")
        self.assertEqual(p["strength"], "strong")   # heuristic kept
        self.assertIsNone(p["llm"])

    def test_no_candidates_no_llm_call(self):
        llm = FakeLLM(self._resp([]))
        argument_map.find_variants([NODES[1], NODES[2]], llm=llm)
        self.assertEqual(llm.calls, 0)


OLD_MAP = {
    "nodes": [{"id": "t1", "text": "Thesis."}, {"id": "t2", "text": "Old premise."},
              {"id": "t3", "text": "Dropped claim."}],
    "edges": [{"from": "t2", "to": "t1", "type": "support", "confidence": 0.9},
              {"from": "t3", "to": "t1", "type": "attack", "confidence": 0.8}],
    "thesis_ids": ["t1"],
}
NEW_MAP = {
    "nodes": [{"id": "t1", "text": "Thesis."}, {"id": "t2", "text": "Edited premise."},
              {"id": "t9", "text": "New claim."}],
    "edges": [{"from": "t2", "to": "t1", "type": "support", "confidence": 0.9},
              {"from": "t9", "to": "t1", "type": "support", "confidence": 0.7}],
    "thesis_ids": ["t1"],
}


class TestDiffMaps(unittest.TestCase):
    def test_diff_fields(self):
        d = argument_map.diff_maps(OLD_MAP, NEW_MAP)
        self.assertEqual(d["nodes_added"], ["t9"])
        self.assertEqual(d["nodes_removed"], ["t3"])
        self.assertEqual(d["nodes_retexted"],
                         [{"id": "t2", "old": "Old premise.", "new": "Edited premise."}])
        self.assertEqual([e["from"] for e in d["edges_added"]], ["t9"])
        self.assertEqual([e["from"] for e in d["edges_removed"]], ["t3"])
        self.assertIsNone(d["thesis_changed"])
        self.assertEqual(d["summary"]["edges_added"], 1)

    def test_identical_maps_diff_empty(self):
        d = argument_map.diff_maps(NEW_MAP, NEW_MAP)
        self.assertEqual(sum(d["summary"].values()), 0)
        self.assertIsNone(d["thesis_changed"])

    def test_thesis_change_reported(self):
        moved = dict(NEW_MAP, thesis_ids=["t9"])
        d = argument_map.diff_maps(OLD_MAP, moved)
        self.assertEqual(d["thesis_changed"], {"old": ["t1"], "new": ["t9"]})

    def test_write_diff(self):
        with tempfile.TemporaryDirectory() as d:
            argument_map.write_diff(argument_map.diff_maps(OLD_MAP, NEW_MAP), d)
            self.assertTrue(os.path.exists(os.path.join(d, "argmap_diff.json")))


if __name__ == "__main__":
    unittest.main()
