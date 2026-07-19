"""Argument-map engine — no API calls (fake LLM).

Run:  venv/bin/python3 -m unittest tests.test_argument_map -v
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.papertrail import argument_map


class FakeLLM:
    """Returns a canned response; counts calls so cache reuse is testable."""
    def __init__(self, response, model="fake-model"):
        self.response = response
        self.model = model
        self.calls = 0

    def call(self, prompt, temperature=0.0, max_output_tokens=2048):
        self.calls += 1
        return self.response


# t2 supports t1; t3 attacks t1; t4 is an isolated aside.
ANALYSIS = {"text_claims": [
    {"id": "t1", "text": "Cities should ban cars downtown.", "verdict": "own"},
    {"id": "t2", "text": "Car bans cut street-level pollution by 30%.", "verdict": "supported"},
    {"id": "t3", "text": "But car bans hurt downtown retail revenue.", "verdict": "supported"},
    {"id": "t4", "text": "As an aside, parking is expensive.", "verdict": "own"},
    {"id": "", "text": "structural placeholder with no id"},
]}

EDGES_JSON = json.dumps({"edges": [
    {"from": 2, "to": 1, "type": "support", "confidence": 0.9, "reason": "pollution benefit"},
    {"from": 3, "to": 1, "type": "attack", "confidence": 0.8, "reason": "retail harm"},
    {"from": 9, "to": 1, "type": "support", "confidence": 0.5, "reason": "out of range"},
    {"from": 1, "to": 1, "type": "support", "confidence": 0.5, "reason": "self loop"},
    {"from": 2, "to": 1, "type": "support", "confidence": 0.7, "reason": "duplicate"},
]})


class TestNodesAndEdges(unittest.TestCase):
    def test_nodes_skip_idless_or_empty(self):
        nodes = argument_map._nodes_from(ANALYSIS)
        self.assertEqual([n["id"] for n in nodes], ["t1", "t2", "t3", "t4"])

    def test_infer_edges_maps_numbers_and_filters(self):
        llm = FakeLLM(EDGES_JSON)
        nodes = argument_map._nodes_from(ANALYSIS)
        edges = argument_map.infer_edges(nodes, llm)
        # out-of-range, self-loop, and duplicate are dropped → 2 survive.
        self.assertEqual(len(edges), 2)
        e = {(x["from"], x["to"], x["type"]) for x in edges}
        self.assertIn(("t2", "t1", "support"), e)
        self.assertIn(("t3", "t1", "attack"), e)

    def test_bad_json_yields_none_not_empty(self):
        # None = "call failed", [] = "model found no relationships" — the
        # distinction keeps failures out of the edge cache.
        edges = argument_map.infer_edges(argument_map._nodes_from(ANALYSIS),
                                         FakeLLM("not json at all"))
        self.assertIsNone(edges)

    def test_failed_inference_is_not_cached(self):
        llm = FakeLLM("not json at all")
        with tempfile.TemporaryDirectory() as d:
            payload = argument_map.build_map(ANALYSIS, llm, cache_dir=d)
            self.assertEqual(payload["edges"], [])          # build still usable
            self.assertEqual(os.listdir(d), [])             # but nothing cached
            # next build retries the LLM instead of inheriting the failure
            argument_map.build_map(ANALYSIS, llm, cache_dir=d)
            self.assertEqual(llm.calls, 2)


class TestRolesAndArgdown(unittest.TestCase):
    def setUp(self):
        self.nodes = argument_map._nodes_from(ANALYSIS)
        self.edges = argument_map.infer_edges(self.nodes, FakeLLM(EDGES_JSON))

    def test_roles(self):
        roles = argument_map.classify_roles(self.nodes, self.edges)
        self.assertEqual(roles["t1"], "thesis")   # influenced, influences nothing
        self.assertEqual(roles["t2"], "premise")  # influences t1, nothing influences it
        self.assertEqual(roles["t3"], "premise")  # attacks t1 (influence), nothing in
        self.assertEqual(roles["t4"], "aside")    # unconnected

    def test_argdown_has_relations(self):
        payload = {"nodes": [{"id": n["id"], "text": n["text"],
                              "verdict": n.get("verdict"), "role": "x"}
                             for n in self.nodes],
                   "edges": self.edges, "thesis_ids": ["t1"]}
        ad = argument_map.to_argdown(payload)
        self.assertIn("[t1]:", ad)
        self.assertIn("<+ [t2]:", ad)   # support marker
        self.assertIn("<- [t3]:", ad)   # attack marker
        self.assertIn("[t4]:", ad)      # isolated claim still listed


class TestBuildMapAndCache(unittest.TestCase):
    def test_build_map_and_cache_reuse(self):
        llm = FakeLLM(EDGES_JSON)
        with tempfile.TemporaryDirectory() as d:
            p1 = argument_map.build_map(ANALYSIS, llm, cache_dir=d)
            self.assertEqual(llm.calls, 1)
            self.assertEqual(p1["thesis_ids"], ["t1"])
            self.assertEqual(p1["model"], "fake-model")
            self.assertTrue(any(f.startswith("argmap_") for f in os.listdir(d)))
            # Second build, same inputs → cache hit, no new LLM call.
            p2 = argument_map.build_map(ANALYSIS, llm, cache_dir=d)
            self.assertEqual(llm.calls, 1)
            self.assertEqual(p2["edges"], p1["edges"])

    def test_write_map_emits_both_files(self):
        llm = FakeLLM(EDGES_JSON)
        with tempfile.TemporaryDirectory() as d:
            payload = argument_map.build_map(ANALYSIS, llm)
            argument_map.write_map(payload, d)
            self.assertTrue(os.path.exists(os.path.join(d, "argument_map.json")))
            self.assertTrue(os.path.exists(os.path.join(d, "argument_map.argdown")))


if __name__ == "__main__":
    unittest.main()
