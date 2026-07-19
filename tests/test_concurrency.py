"""Concurrency tests — stub LLM with artificial latency, no API, no network.

Run:  venv/bin/python3 -m unittest tests.test_concurrency -v
"""

import os
import sys
import json
import time
import threading
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.papertrail.llm_client import parallel_map
from modules.papertrail import source_decomposer, matcher


class TestParallelMap(unittest.TestCase):
    def test_order_preserved_and_all_processed(self):
        items = list(range(20))
        out = parallel_map(lambda x: x * 2, items, workers=6)
        self.assertEqual(out, [x * 2 for x in items])

    def test_actually_concurrent(self):
        threads = set()

        def work(x):
            threads.add(threading.current_thread().name)
            time.sleep(0.05)
            return x

        t0 = time.time()
        parallel_map(work, range(8), workers=8)
        elapsed = time.time() - t0
        self.assertGreater(len(threads), 1)
        self.assertLess(elapsed, 8 * 0.05)   # faster than sequential

    def test_workers_1_is_plain_loop(self):
        threads = set()

        def work(x):
            threads.add(threading.current_thread().name)
            return x

        parallel_map(work, range(5), workers=1)
        self.assertEqual(threads, {threading.current_thread().name})


class StubLLM:
    """Returns one claim per chunk, tagged with the chunk's first word, after a delay."""

    def __init__(self, delay=0.05):
        self.delay = delay
        self.calls = 0
        self._lock = threading.Lock()

    def call_json(self, prompt, **kw):
        with self._lock:
            self.calls += 1
        time.sleep(self.delay)
        first_word = prompt.rsplit("{", 1)[-1] if False else prompt.split()[-1]
        return [f"claim about {first_word}"]

    def call(self, prompt, **kw):
        with self._lock:
            self.calls += 1
        time.sleep(self.delay)
        return json.dumps({"supported": True, "reason": "stub"})


class TestDecomposerConcurrency(unittest.TestCase):
    def test_chunk_order_deterministic_and_parallel(self):
        # 5 chunks of distinct content; the stub returns a claim naming each chunk's
        # last word, so claim order proves chunk order was preserved.
        text = "\n\n".join(" ".join(["word"] * 1199) + f" chunk{i}" for i in range(5))
        llm = StubLLM()
        with patch.object(source_decomposer, "_load_prompt", return_value="{TEXT}"):
            t0 = time.time()
            claims = source_decomposer._extract_claims_from_text(text, llm, workers=5)
            elapsed = time.time() - t0
        self.assertEqual(claims, [f"claim about chunk{i}" for i in range(5)])
        self.assertEqual(llm.calls, 5)
        self.assertLess(elapsed, 5 * llm.delay)   # ran concurrently

    def test_sequential_and_parallel_same_result(self):
        text = "\n\n".join(" ".join(["word"] * 1199) + f" chunk{i}" for i in range(4))
        with patch.object(source_decomposer, "_load_prompt", return_value="{TEXT}"):
            seq = source_decomposer._extract_claims_from_text(text, StubLLM(0), workers=1)
            par = source_decomposer._extract_claims_from_text(text, StubLLM(0), workers=4)
        self.assertEqual(seq, par)


class TestMatcherConcurrency(unittest.TestCase):
    def _claims_and_sources(self, n=6):
        # Each claim cites its own source; identical claim/sentence text drives
        # cosine to 1.0 (AUTO_SUPPORT) so no LLM call is even needed — we only
        # test the parallel plumbing and result assembly here.
        claims, sources = [], {}
        for i in range(n):
            pid = f"p{i}"
            text = f"The source document plainly states fact number {i} here."
            claims.append({"id": f"t{i}", "text": text,
                           "markers": [pid], "paper_ids": [pid]})
            sources[pid] = {"title": f"S{i}", "key": pid,
                            "sentences": [{"text": text, "page": 1}],
                            "claims": []}
        return claims, sources

    def _identity_matrix_run(self, claims, sources, workers):
        def fake_cosine(a, b):
            return [[1.0 if x == y else 0.0 for y in b] for x in a]
        with patch.object(matcher.embeddings, "cosine_matrix", side_effect=fake_cosine):
            return matcher.run(claims, sources, llm=StubLLM(0.03), workers=workers)

    def test_parallel_matches_sequential_and_orders_claims(self):
        claims, sources = self._claims_and_sources()
        seq = self._identity_matrix_run(claims, sources, workers=1)
        par = self._identity_matrix_run(claims, sources, workers=6)
        self.assertEqual([c["id"] for c in par["text_claims"]],
                         [f"t{i}" for i in range(6)])
        self.assertEqual(
            [(c["id"], c["verdict"]) for c in seq["text_claims"]],
            [(c["id"], c["verdict"]) for c in par["text_claims"]])
        self.assertEqual(par["coverage"]["totals"]["supported"], 6)


if __name__ == "__main__":
    unittest.main()
