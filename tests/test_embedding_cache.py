"""Tests for the on-disk embedding cache (embeddings.embed_cached) and the matcher's
cached embedding path. No API, no network, no real SPECTER model — embeddings.embed
is stubbed with a deterministic hash-based encoder.

Run:  venv/bin/python3 -m unittest tests.test_embedding_cache -v
"""

import os
import sys
import shutil
import hashlib
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import torch

from modules.papertrail import embeddings, matcher


def fake_embed(texts, model_name=embeddings.DEFAULT_MODEL):
    """Deterministic per-text vectors: identical texts -> cosine 1.0, others ~0."""
    vecs = []
    for t in texts:
        h = hashlib.sha256(t.encode("utf-8")).digest()
        v = torch.tensor([b / 255.0 for b in h], dtype=torch.float32)
        vecs.append(v / (v.norm() + 1e-9))
    return torch.stack(vecs) if vecs else torch.zeros((0, 32))


class TestEmbedCached(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_second_call_hits_cache_without_recomputing(self):
        texts = ["alpha claim", "beta claim"]
        f = os.path.join(self.dir, "p1.sents.npz")
        with patch.object(embeddings, "embed", side_effect=fake_embed):
            first = embeddings.embed_cached(texts, f)
        self.assertTrue(os.path.exists(f))

        def boom(*a, **k):
            raise AssertionError("embed() must not be called on a cache hit")

        with patch.object(embeddings, "embed", side_effect=boom):
            second = embeddings.embed_cached(texts, f)
        # float16 storage round-trip: close, not bit-identical
        self.assertTrue(torch.allclose(first, second, atol=1e-3))

    def test_changed_texts_invalidate_cache(self):
        f = os.path.join(self.dir, "p1.sents.npz")
        with patch.object(embeddings, "embed", side_effect=fake_embed) as m:
            embeddings.embed_cached(["one"], f)
            embeddings.embed_cached(["one", "two"], f)  # different texts -> recompute
            self.assertEqual(m.call_count, 2)

    def test_corrupt_cache_recovers(self):
        f = os.path.join(self.dir, "p1.sents.npz")
        with open(f, "wb") as fh:
            fh.write(b"not an npz")
        with patch.object(embeddings, "embed", side_effect=fake_embed):
            out = embeddings.embed_cached(["one"], f)
        self.assertEqual(out.shape[0], 1)

    def test_none_cache_file_degrades_to_plain_embed(self):
        with patch.object(embeddings, "embed", side_effect=fake_embed):
            out = embeddings.embed_cached(["one"], None)
        self.assertEqual(out.shape[0], 1)


class StubLLM:
    def call(self, *a, **k):
        return '{"supported": true, "reason": "ok"}'


class TestMatcherCachedPath(unittest.TestCase):
    """The emb_cache_dir path must produce the same verdicts/relevances as the
    historical uncached path, and reuse cache files on a second run."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def _fixture(self):
        text = "The source document plainly states this exact fact."
        claims = [{"id": "t1", "text": text, "markers": ["p1"], "paper_ids": ["p1"]}]
        sources = {"p1": {"title": "S1", "key": "p1",
                          "sentences": [{"text": text, "page": 1}],
                          "claims": [{"id": "c1", "text": "An omitted source claim.",
                                      "evidence": ["An omitted evidence sentence."],
                                      "evidence_pages": [2]}]}}
        return claims, sources

    def test_cached_path_matches_uncached_and_reuses_cache(self):
        claims, sources = self._fixture()
        with patch.object(embeddings, "embed", side_effect=fake_embed):
            plain = matcher.run(claims, sources, llm=StubLLM(), workers=1)
            cached = matcher.run(claims, sources, llm=StubLLM(), workers=1,
                                 emb_cache_dir=self.dir)
        self.assertEqual(plain["text_claims"][0]["verdict"],
                         cached["text_claims"][0]["verdict"])
        self.assertEqual(cached["text_claims"][0]["verdict"], "supported")
        self.assertEqual(len(plain["omitted"]), len(cached["omitted"]))
        self.assertAlmostEqual(plain["omitted"][0]["relevance"],
                               cached["omitted"][0]["relevance"], places=2)
        self.assertNotIn("_j", cached["omitted"][0])
        files = sorted(os.listdir(self.dir))
        self.assertEqual(files, ["p1.claims.npz", "p1.sents.npz"])

        # Second cached run: source sentence/claim texts unchanged -> only the
        # user's claims get re-encoded; both source caches must hit.
        calls = []
        def counting_embed(texts, model_name=embeddings.DEFAULT_MODEL):
            calls.append(list(texts))
            return fake_embed(texts, model_name)
        with patch.object(embeddings, "embed", side_effect=counting_embed):
            again = matcher.run(claims, sources, llm=StubLLM(), workers=1,
                                emb_cache_dir=self.dir)
        self.assertEqual(again["text_claims"][0]["verdict"], "supported")
        self.assertEqual(len(calls), 1)          # user claims only
        self.assertEqual(calls[0], [claims[0]["text"]])


if __name__ == "__main__":
    unittest.main()
