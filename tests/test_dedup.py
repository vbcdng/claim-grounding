"""Cross-source claim dedup (slot A4) — no API calls (fake LLM / fake embeddings).

Run:  venv/bin/python3 -m unittest tests.test_dedup -v
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.papertrail import dedup


class FakeLLM:
    def __init__(self, responses):
        self.responses = list(responses) if isinstance(responses, list) else [responses]
        self.calls = 0
        self.prompts = []

    def call(self, prompt, temperature=0.0, max_output_tokens=2048):
        self.prompts.append(prompt)
        resp = self.responses[min(self.calls, len(self.responses) - 1)]
        self.calls += 1
        return resp


# Vectors are looked up by text: identical vector => cosine 1.0, distinct basis
# vectors => cosine 0.0. Only cross-source pairs above the floor become candidates.
V = {
    # near-verbatim cross-source duplicate (lex 0.975, no guards trip)
    "The stock solution was prepared by dissolving cesium chloride in distilled water.": [1, 0, 0, 0, 0],
    "A stock solution was prepared by dissolving cesium chloride in distilled water.": [1, 0, 0, 0, 0],
    # paraphrase of the same (lex 0.935 vs the b-form) — transitive link
    "A stock solution was made by dissolving cesium chloride in distilled water.": [1, 0, 0, 0, 0],
    # semantically-hot but lexically-far pair (cosine 1.0, lex low)
    "Bentonite adsorption capacity reached its maximum at neutral pH.": [0, 1, 0, 0, 0],
    "Adsorption was described by the pseudo-first-order model.": [0, 1, 0, 0, 0],
    # year switch: lex 0.98 but a DIFFERENT claim — numeric guard territory
    "The committee approved the annual budget for 2024.": [0, 0, 1, 0, 0],
    "The committee approved the annual budget for 2025.": [0, 0, 1, 0, 0],
    # negation switch (lex 0.929)
    "The model applies to saline conditions.": [0, 0, 0, 1, 0],
    "The model never applies to saline conditions.": [0, 0, 0, 1, 0],
    # unrelated filler
    "Chimpanzees use tools to extract termites.": [0, 0, 0, 0, 1],
}


def fake_embed(key, texts):
    return [V[t] for t in texts]


def S(key, *texts):
    return key, [{"id": f"{key}_c{i}", "text": t} for i, t in enumerate(texts)]


def _pair(payload, a, b):
    for p in payload["pairs"]:
        if {p["a"], p["b"]} == {a, b}:
            return p
    return None


class TestBlocking(unittest.TestCase):
    def test_same_source_pairs_never_candidates(self):
        # Both budget variants live in ONE source; the other source is unrelated.
        sources = dict([
            S("src1", "The committee approved the annual budget for 2024.",
                      "The committee approved the annual budget for 2025."),
            S("src2", "Chimpanzees use tools to extract termites."),
        ])
        payload = dedup.find_duplicates(sources, embed_fn=fake_embed)
        self.assertEqual(payload["pairs"], [])
        self.assertEqual(payload["clusters"], [])

    def test_below_floor_not_candidates(self):
        sources = dict([
            S("src1", "The committee approved the annual budget for 2024."),
            S("src2", "Chimpanzees use tools to extract termites."),
        ])
        payload = dedup.find_duplicates(sources, embed_fn=fake_embed)
        self.assertEqual(payload["pairs"], [])

    def test_top_k_caps_candidates_per_claim(self):
        # One src1 claim vs 5 identical-vector src2 claims: keep only top_k.
        text = "Bentonite adsorption capacity reached its maximum at neutral pH."
        others = "Adsorption was described by the pseudo-first-order model."
        sources = dict([S("src1", text), S("src2", *[others] * 5)])

        def embed(key, texts):
            return [[0, 1, 0]] * len(texts)

        payload = dedup.find_duplicates(sources, embed_fn=embed, top_k=2)
        self.assertEqual(len(payload["pairs"]), 2)

    def test_requires_embed_fn(self):
        with self.assertRaises(ValueError):
            dedup.find_duplicates({})

    def test_fewer_than_two_sources_empty(self):
        payload = dedup.find_duplicates(
            dict([S("only", "The model applies to saline conditions.")]),
            embed_fn=fake_embed)
        self.assertEqual(payload["clusters"], [])
        self.assertEqual(payload["pairs"], [])

    def test_duplicate_claim_id_keeps_first(self):
        sources = {
            "src1": [{"id": "x_c0", "text": "The model applies to saline conditions."}],
            "src2": [{"id": "x_c0", "text": "The model never applies to saline conditions."}],
        }
        payload = dedup.find_duplicates(sources, embed_fn=fake_embed)
        self.assertEqual(payload["pairs"], [])   # second x_c0 dropped -> 1 source left


class TestGrading(unittest.TestCase):
    def test_near_verbatim_merges_with_a1_contract_members(self):
        sources = dict([
            S("src1", "The stock solution was prepared by dissolving cesium chloride in distilled water."),
            S("src2", "A stock solution was prepared by dissolving cesium chloride in distilled water."),
        ])
        payload = dedup.find_duplicates(sources, embed_fn=fake_embed)
        p = _pair(payload, "src1_c0", "src2_c0")
        self.assertEqual(p["strength"], "strong")
        self.assertEqual(len(payload["clusters"]), 1)
        for member in payload["clusters"][0]:
            self.assertEqual(sorted(member), ["id", "source", "text"])
        self.assertEqual([m["source"] for m in payload["clusters"][0]],
                         ["src1", "src2"])
        self.assertEqual(payload["n_claims"], {"src1": 1, "src2": 1})

    def test_cosine_is_retrieval_only_never_strong(self):
        # cosine 1.0 but lexically far: surfaced as a weak question, no cluster.
        sources = dict([
            S("src1", "Bentonite adsorption capacity reached its maximum at neutral pH."),
            S("src2", "Adsorption was described by the pseudo-first-order model."),
        ])
        payload = dedup.find_duplicates(sources, embed_fn=fake_embed)
        p = _pair(payload, "src1_c0", "src2_c0")
        self.assertIsNotNone(p)
        self.assertEqual(p["strength"], "weak")
        self.assertEqual(payload["clusters"], [])
        self.assertEqual(payload["n_weak_pairs"], 1)

    def test_numeric_switch_capped_at_weak(self):
        # lex 0.98 — way above LEX_STRONG; only the number/ordinal guard keeps
        # "budget for 2024" vs "budget for 2025" from a wrong merge.
        sources = dict([
            S("src1", "The committee approved the annual budget for 2024."),
            S("src2", "The committee approved the annual budget for 2025."),
        ])
        payload = dedup.find_duplicates(sources, embed_fn=fake_embed)
        p = _pair(payload, "src1_c0", "src2_c0")
        self.assertEqual(p["strength"], "weak")
        self.assertEqual(payload["clusters"], [])

    def test_negation_switch_capped_at_weak(self):
        sources = dict([
            S("src1", "The model applies to saline conditions."),
            S("src2", "The model never applies to saline conditions."),
        ])
        payload = dedup.find_duplicates(sources, embed_fn=fake_embed)
        p = _pair(payload, "src1_c0", "src2_c0")
        self.assertEqual(p["strength"], "weak")
        self.assertEqual(payload["clusters"], [])

    def test_transitive_strong_pairs_form_one_cluster(self):
        sources = dict([
            S("src1", "The stock solution was prepared by dissolving cesium chloride in distilled water."),
            S("src2", "A stock solution was prepared by dissolving cesium chloride in distilled water."),
            S("src3", "A stock solution was made by dissolving cesium chloride in distilled water."),
        ])
        payload = dedup.find_duplicates(sources, embed_fn=fake_embed)
        self.assertEqual(len(payload["clusters"]), 1)
        self.assertEqual(len(payload["clusters"][0]), 3)


class TestLLMConfirm(unittest.TestCase):
    WEAK_SOURCES = dict([
        S("src1", "Bentonite adsorption capacity reached its maximum at neutral pH."),
        S("src2", "Adsorption was described by the pseudo-first-order model."),
    ])

    def _resp(self, items):
        return json.dumps({"pairs": items})

    def test_different_claim_kills_pair(self):
        llm = FakeLLM(self._resp([{"n": 1, "relation": "different_claim", "why": "x"}]))
        payload = dedup.find_duplicates(self.WEAK_SOURCES, embed_fn=fake_embed, llm=llm)
        self.assertEqual(llm.calls, 1)
        self.assertEqual(payload["method"], "cosine+lexical+llm")
        self.assertEqual(payload["pairs"], [])
        self.assertEqual(payload["clusters"], [])

    def test_confirm_upgrades_weak_to_strong(self):
        llm = FakeLLM(self._resp([{"n": 1, "relation": "hedged_variant", "why": "same finding"}]))
        payload = dedup.find_duplicates(self.WEAK_SOURCES, embed_fn=fake_embed, llm=llm)
        p = payload["pairs"][0]
        self.assertEqual(p["strength"], "strong")
        self.assertEqual(p["llm"]["relation"], "hedged_variant")
        self.assertEqual(len(payload["clusters"]), 1)

    def test_garbage_fails_open(self):
        llm = FakeLLM("garbage")
        payload = dedup.find_duplicates(self.WEAK_SOURCES, embed_fn=fake_embed, llm=llm)
        self.assertEqual(payload["method"], "cosine+lexical")   # no chunk parsed
        p = payload["pairs"][0]
        self.assertEqual(p["strength"], "weak")                 # heuristic kept
        self.assertIsNone(p["llm"])

    def test_out_of_range_verdict_ignored(self):
        llm = FakeLLM(self._resp([{"n": 99, "relation": "restatement", "why": "x"}]))
        payload = dedup.find_duplicates(self.WEAK_SOURCES, embed_fn=fake_embed, llm=llm)
        self.assertEqual(payload["pairs"][0]["strength"], "weak")

    def test_no_candidates_no_llm_call(self):
        llm = FakeLLM(self._resp([]))
        sources = dict([
            S("src1", "The committee approved the annual budget for 2024."),
            S("src2", "Chimpanzees use tools to extract termites."),
        ])
        dedup.find_duplicates(sources, embed_fn=fake_embed, llm=llm)
        self.assertEqual(llm.calls, 0)

    def test_chunking_splits_large_batches(self):
        # CONFIRM_CHUNK + 1 candidate pairs -> exactly 2 LLM calls, and the
        # per-chunk pair numbering maps verdicts back to the right pairs.
        n = dedup.CONFIRM_CHUNK + 1
        src1 = [{"id": f"a_c{i}", "text": f"Claim variant number {i} about topic {i}."} for i in range(n)]
        src2 = [{"id": f"b_c{i}", "text": f"Claim variant number {i} on topic {i}."} for i in range(n)]

        def embed(key, texts):
            vecs = []
            for t in texts:
                i = int(t.split("number ")[1].split(" ")[0])
                v = [0.0] * n
                v[i] = 1.0
                vecs.append(v)
            return vecs

        # Kill every pair in both chunks -> everything dropped.
        kill_all = json.dumps({"pairs": [
            {"n": k, "relation": "different_claim", "why": "x"}
            for k in range(1, dedup.CONFIRM_CHUNK + 1)]})
        llm = FakeLLM([kill_all, kill_all])
        payload = dedup.find_duplicates({"src1": src1, "src2": src2},
                                        embed_fn=embed, llm=llm)
        self.assertEqual(llm.calls, 2)
        self.assertEqual(payload["pairs"], [])


class TestArtifact(unittest.TestCase):
    def test_write_dedup(self):
        sources = dict([
            S("src1", "The stock solution was prepared by dissolving cesium chloride in distilled water."),
            S("src2", "A stock solution was prepared by dissolving cesium chloride in distilled water."),
        ])
        payload = dedup.find_duplicates(sources, embed_fn=fake_embed)
        with tempfile.TemporaryDirectory() as d:
            path = dedup.write_dedup(payload, d)
            with open(path) as f:
                loaded = json.load(f)
        self.assertEqual(loaded["clusters"], payload["clusters"])
        self.assertIn("params", loaded)

    def test_clusters_sorted_by_size_desc(self):
        sources = dict([
            S("src1",
              "The stock solution was prepared by dissolving cesium chloride in distilled water.",
              "The model applies to saline conditions."),
            S("src2",
              "A stock solution was prepared by dissolving cesium chloride in distilled water.",
              "The model applies to saline conditions."),
            S("src3",
              "A stock solution was made by dissolving cesium chloride in distilled water."),
        ])
        payload = dedup.find_duplicates(sources, embed_fn=fake_embed)
        sizes = [len(c) for c in payload["clusters"]]
        self.assertEqual(sizes, sorted(sizes, reverse=True))
        self.assertEqual(sizes[0], 3)


if __name__ == "__main__":
    unittest.main()
