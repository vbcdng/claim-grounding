"""Offline tests for paper_search.neighbors() — the shared citation-traversal
primitive (Stream B). No network: real Semantic Scholar responses are replayed
from tests/fixtures/, and the OpenAlex fallback is driven with a hand-built work
dict. Capture script: scratchpad/capture_fixtures.py.

Run:  venv/bin/python3 -m unittest tests.test_paper_search -v
"""

import os
import sys
import json
import logging
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.papertrail import paper_search as ps

# The honest-failure tests deliberately trip S2/OpenAlex errors; quiet the logs.
logging.getLogger("modules.papertrail.paper_search").setLevel(logging.CRITICAL)

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


def _load_fixture(name):
    with open(os.path.join(FIX, name), "r", encoding="utf-8") as f:
        return json.load(f)


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def _s2_router(fixture_by_direction):
    """Return a fake requests.get that replays an S2 fixture for offset 0 and a
    terminal empty page afterwards (so pagination terminates deterministically).
    Routes references vs citations by URL suffix."""
    def fake_get(url, params=None, headers=None, timeout=None):
        params = params or {}
        if params.get("offset", 0) > 0:
            return _FakeResponse({"data": [], "next": None, "offset": params["offset"]})
        for direction, payload in fixture_by_direction.items():
            if url.rstrip("/").endswith(direction):
                return _FakeResponse(payload)
        raise AssertionError(f"unexpected URL in test: {url}")
    return fake_get


class TestNeighborsParsing(unittest.TestCase):
    def setUp(self):
        ps.s2._key_rejected = False
        self.refs = _load_fixture("s2_references_attention.json")
        self.cites = _load_fixture("s2_citations_attention.json")

    def test_references_parsed_into_normalized_shape(self):
        router = _s2_router({"references": self.refs})
        with patch.object(ps, "requests") as mock_requests:
            mock_requests.get.side_effect = router
            mock_requests.exceptions = ps.requests.exceptions  # keep real exc types
            out = ps.neighbors("ARXIV:1706.03762", "references")

        self.assertEqual(len(out), len(self.refs["data"]))
        n = out[0]
        # every documented key present
        for key in ("paper_id", "title", "year", "abstract", "authors", "doi",
                    "arxiv_id", "url", "citation_count", "relation",
                    "influential", "intents", "source"):
            self.assertIn(key, n)
        self.assertEqual(n["relation"], "references")
        self.assertEqual(n["source"], "s2")
        self.assertIsInstance(n["authors"], list)
        self.assertIsInstance(n["influential"], bool)
        # url derives from an id; at least one neighbor has a doi or arxiv url
        self.assertTrue(any(x["url"] and ("doi.org" in x["url"] or "arxiv.org" in x["url"]
                                          or "semanticscholar.org" in x["url"]) for x in out))

    def test_citations_relation_and_url(self):
        router = _s2_router({"citations": self.cites})
        with patch.object(ps, "requests") as mock_requests:
            mock_requests.get.side_effect = router
            mock_requests.exceptions = ps.requests.exceptions
            out = ps.neighbors("ARXIV:1706.03762", "citations")
        self.assertEqual(len(out), len(self.cites["data"]))
        self.assertTrue(all(x["relation"] == "citations" for x in out))
        # the first citation in the fixture has a DOI -> doi.org url
        doi_ones = [x for x in out if x["doi"]]
        self.assertTrue(doi_ones)
        self.assertTrue(doi_ones[0]["url"].startswith("https://doi.org/"))

    def test_both_composes_and_dedupes(self):
        router = _s2_router({"references": self.refs, "citations": self.cites})
        with patch.object(ps, "requests") as mock_requests:
            mock_requests.get.side_effect = router
            mock_requests.exceptions = ps.requests.exceptions
            out = ps.neighbors("ARXIV:1706.03762", "both")
        # attention refs and cites are disjoint papers -> union, no drops
        keys = {(x.get("paper_id") or x.get("doi") or x["title"].lower()) for x in out}
        self.assertEqual(len(out), len(keys))
        self.assertLessEqual(len(out),
                             len(self.refs["data"]) + len(self.cites["data"]))
        self.assertTrue(any(x["relation"] == "references" for x in out))
        self.assertTrue(any(x["relation"] == "citations" for x in out))

    def test_null_neighbors_skipped(self):
        # S2 returns null citedPaper for refs it can't resolve — must be skipped.
        payload = {"data": [{"intents": [], "isInfluential": False, "citedPaper": None},
                            self.refs["data"][0]], "next": None}
        router = _s2_router({"references": payload})
        with patch.object(ps, "requests") as mock_requests:
            mock_requests.get.side_effect = router
            mock_requests.exceptions = ps.requests.exceptions
            out = ps.neighbors("ARXIV:1706.03762", "references")
        self.assertEqual(len(out), 1)


class TestNeighborsCache(unittest.TestCase):
    def setUp(self):
        ps.s2._key_rejected = False
        self.refs = _load_fixture("s2_references_attention.json")

    def test_cache_written_then_reused_without_network(self):
        with tempfile.TemporaryDirectory() as d:
            router = _s2_router({"references": self.refs})
            with patch.object(ps, "requests") as mock_requests:
                mock_requests.get.side_effect = router
                mock_requests.exceptions = ps.requests.exceptions
                first = ps.neighbors("ARXIV:1706.03762", "references", cache_dir=d)
            # cache file exists
            files = os.listdir(d)
            self.assertTrue(any(f.startswith("neighbors__") for f in files))

            # second call: any network access raises -> must be served from cache
            def explode(*a, **k):
                raise AssertionError("network hit on a cached lookup")
            with patch.object(ps.requests, "get", side_effect=explode):
                second = ps.neighbors("ARXIV:1706.03762", "references", cache_dir=d)
            self.assertEqual(first, second)
            self.assertEqual(len(second), len(self.refs["data"]))

    def test_failure_is_not_cached(self):
        with tempfile.TemporaryDirectory() as d:
            def explode(*a, **k):
                raise ps.requests.exceptions.ConnectionError("down")
            with patch.object(ps.requests, "get", side_effect=explode):
                out = ps.neighbors("deadbeefdeadbeef", "references", cache_dir=d)
            self.assertEqual(out, [])
            # nothing cached -> a later (recovered) run will retry
            self.assertEqual([f for f in os.listdir(d) if f.startswith("neighbors__")], [])


class TestUnfetchableHonesty(unittest.TestCase):
    """Never guess: S2 down + OpenAlex can't resolve -> [] (caller stops)."""
    def setUp(self):
        ps.s2._key_rejected = False

    def test_s2_down_and_unresolvable_returns_empty(self):
        def explode(*a, **k):
            raise ps.requests.exceptions.ConnectionError("down")
        # a bare hex id matches no OpenAlex resolver pattern -> no network, no guess
        with patch.object(ps.requests, "get", side_effect=explode):
            out = ps.neighbors("0123abcd4567ef89", "references")
        self.assertEqual(out, [])

    def test_bad_direction_and_empty_id(self):
        self.assertEqual(ps.neighbors("", "both"), [])
        with self.assertRaises(ValueError):
            ps.neighbors("x", "sideways")


class TestOpenAlexFallback(unittest.TestCase):
    """Fallback normalization (offline, hand-built OpenAlex work)."""
    def test_reconstruct_abstract(self):
        inv = {"Large": [0], "language": [1], "models": [2], "scale": [3]}
        self.assertEqual(ps._reconstruct_abstract(inv),
                         "Large language models scale")
        self.assertIsNone(ps._reconstruct_abstract(None))

    def test_normalize_openalex_shape(self):
        work = {
            "id": "https://openalex.org/W2000000001",
            "title": "A Cited Work",
            "publication_year": 2020,
            "doi": "https://doi.org/10.1/xyz",
            "cited_by_count": 42,
            "abstract_inverted_index": {"Hello": [0], "there": [1]},
            "authorships": [{"author": {"display_name": "Ada L."}}],
        }
        n = ps._normalize_openalex(work, "references")
        self.assertEqual(n["paper_id"], "W2000000001")
        self.assertEqual(n["doi"], "10.1/xyz")
        self.assertEqual(n["url"], "https://doi.org/10.1/xyz")
        self.assertEqual(n["abstract"], "Hello there")
        self.assertEqual(n["authors"], ["Ada L."])
        self.assertEqual(n["relation"], "references")
        self.assertEqual(n["source"], "openalex")

    def test_openalex_citations_via_fallback(self):
        # S2 hard-fails; paper resolves by DOI; OpenAlex returns one citing work.
        resolved = {"id": "https://openalex.org/W111", "referenced_works": []}
        citing = {"results": [{
            "id": "https://openalex.org/W222", "title": "Citing Paper",
            "publication_year": 2021, "doi": "https://doi.org/10.2/abc",
            "cited_by_count": 3, "authorships": [],
            "abstract_inverted_index": None}],
            "meta": {"next_cursor": None}}

        def fake_get(url, params=None, headers=None, timeout=None):
            params = params or {}
            if "semanticscholar.org" in url:
                raise ps.requests.exceptions.ConnectionError("s2 down")
            if url.endswith("/works/https://doi.org/10.9/paper"):
                return _FakeResponse(resolved)
            if url.endswith("/works") and "cites:" in (params.get("filter") or ""):
                return _FakeResponse(citing)
            raise AssertionError(f"unexpected url {url} params {params}")

        with patch.object(ps.requests, "get", side_effect=fake_get):
            out = ps.neighbors("DOI:10.9/paper", "citations")
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["paper_id"], "W222")
        self.assertEqual(out[0]["source"], "openalex")
        self.assertEqual(out[0]["relation"], "citations")


# ---------------------------------------------------------------------------
# snowball / pick_relevant — offline (cosine + s2 search + neighbors all mocked)
# ---------------------------------------------------------------------------

# We encode a paper's intended similarity AS its abstract text ("0.9"), so the
# fake cosine can be trivially deterministic: score = float(text). Papers with an
# empty abstract fall back to title; papers with neither are unrankable.
def _fake_cosine(a_texts, b_texts, *args, **kwargs):
    return [[float(t) for t in b_texts]]


class _StubLLM:
    def __init__(self, resp=None, raises=False):
        self._resp = resp
        self._raises = raises
        self.calls = 0

    def call_json(self, prompt, **kwargs):
        self.calls += 1
        if self._raises:
            raise RuntimeError("boom")
        return self._resp


def _paper(pid, score, relation=None):
    p = {"paper_id": pid, "title": pid, "abstract": str(score),
         "year": 2020, "doi": None, "url": f"u/{pid}"}
    if relation:
        p["relation"] = relation
    return p


class TestPickRelevant(unittest.TestCase):
    def test_ranks_and_keeps_top_k(self):
        papers = [_paper("a", 0.1), _paper("b", 0.9), _paper("c", 0.5)]
        with patch.object(ps.embeddings, "cosine_matrix", _fake_cosine):
            out = ps.pick_relevant(papers, "target", 2)
        self.assertEqual([p["paper_id"] for p in out], ["b", "c"])
        self.assertEqual(out[0]["relevance"], 0.9)
        self.assertTrue(out[0]["reason"].startswith("cosine"))

    def test_drops_unrankable_papers(self):
        papers = [_paper("a", 0.5),
                  {"paper_id": "x", "title": "", "abstract": ""}]  # no text
        with patch.object(ps.embeddings, "cosine_matrix", _fake_cosine):
            out = ps.pick_relevant(papers, "target", 5)
        self.assertEqual([p["paper_id"] for p in out], ["a"])

    def test_empty_inputs(self):
        self.assertEqual(ps.pick_relevant([], "t", 3), [])
        self.assertEqual(ps.pick_relevant([_paper("a", 0.5)], "t", 0), [])
        self.assertEqual(ps.pick_relevant([_paper("a", 0.5)], "  ", 3), [])

    def test_llm_gate_drops_offtarget_and_sets_reason(self):
        papers = [_paper("a", 0.9), _paper("b", 0.8)]
        llm = _StubLLM(resp=[{"index": 0, "relevant": False},
                             {"index": 1, "relevant": True, "reason": "on point"}])
        with patch.object(ps.embeddings, "cosine_matrix", _fake_cosine):
            out = ps.pick_relevant(papers, "target", 5, llm=llm)
        self.assertEqual([p["paper_id"] for p in out], ["b"])
        self.assertEqual(out[0]["reason"], "on point")
        self.assertEqual(llm.calls, 1)   # one batched call, not one per paper

    def test_llm_gate_keeps_ranking_on_error(self):
        papers = [_paper("a", 0.9), _paper("b", 0.8)]
        llm = _StubLLM(raises=True)
        with patch.object(ps.embeddings, "cosine_matrix", _fake_cosine):
            out = ps.pick_relevant(papers, "target", 5, llm=llm)
        self.assertEqual([p["paper_id"] for p in out], ["a", "b"])  # unchanged

    def test_llm_gate_keeps_candidate_with_no_verdict(self):
        papers = [_paper("a", 0.9), _paper("b", 0.8)]
        llm = _StubLLM(resp=[{"index": 0, "relevant": True}])  # omits index 1
        with patch.object(ps.embeddings, "cosine_matrix", _fake_cosine):
            out = ps.pick_relevant(papers, "target", 5, llm=llm)
        self.assertEqual({p["paper_id"] for p in out}, {"a", "b"})


class TestSnowball(unittest.TestCase):
    def setUp(self):
        ps.s2._key_rejected = False

    def _raw_seed(self, pid, score):
        # shape of a Semantic Scholar /paper/search hit
        return {"paperId": pid, "title": pid, "abstract": str(score),
                "year": 2020, "authors": [], "externalIds": {},
                "citationCount": 1, "openAccessPdf": None}

    def test_end_to_end_graph_and_provenance(self):
        graph = {
            "S1": [_paper("N1", 0.8, "references"),
                   _paper("N2", 0.7, "citations")],
            "S2": [_paper("N1", 0.8, "references")],   # shared neighbor -> dedup
        }
        with patch.object(ps.embeddings, "cosine_matrix", _fake_cosine), \
             patch.object(ps.s2, "search_papers",
                          return_value=[self._raw_seed("S1", 0.95),
                                        self._raw_seed("S2", 0.9)]), \
             patch.object(ps, "neighbors",
                          side_effect=lambda pid, d="both", cache_dir=None: graph.get(pid, [])):
            res = ps.snowball("origins of X", ["x", "origins"],
                              max_depth=1, branching=5)

        ids = {c["paper_id"] for c in res["candidates"]}
        self.assertEqual(ids, {"S1", "S2", "N1", "N2"})
        # sorted by relevance descending
        rels = [c["relevance"] for c in res["candidates"]]
        self.assertEqual(rels, sorted(rels, reverse=True))
        # provenance: seeds are their own root; neighbors carry the seed path
        by_id = {c["paper_id"]: c for c in res["candidates"]}
        self.assertEqual(by_id["S1"]["found_via"], ["S1"])
        self.assertEqual(by_id["N1"]["found_via"][0], by_id["N1"]["found_via"][0])
        self.assertEqual(by_id["N1"]["found_via"][-1], "N1")
        self.assertEqual(len(by_id["N1"]["found_via"]), 2)
        # N1 discovered once despite two parents; edges record both parents
        n1_edges = [e for e in res["edges"] if e["to"] == "N1"]
        self.assertEqual({e["from"] for e in n1_edges}, {"S1", "S2"})
        self.assertTrue(all(e["kind"] == "cites" for e in n1_edges))
        cited_by = [e for e in res["edges"] if e["to"] == "N2"]
        self.assertEqual(cited_by[0]["kind"], "cited_by")
        self.assertEqual(len(res["seeds"]), 2)
        self.assertEqual(res["max_depth"], 1)
        self.assertEqual(res["status"], "ok")

    def test_depth_two_expands_further(self):
        graph = {
            "S1": [_paper("N1", 0.8, "references")],
            "N1": [_paper("D2", 0.6, "references")],
        }
        with patch.object(ps.embeddings, "cosine_matrix", _fake_cosine), \
             patch.object(ps.s2, "search_papers",
                          return_value=[self._raw_seed("S1", 0.95)]), \
             patch.object(ps, "neighbors",
                          side_effect=lambda pid, d="both", cache_dir=None: graph.get(pid, [])):
            res = ps.snowball("t", ["t"], max_depth=2, branching=5)
        self.assertIn("D2", {c["paper_id"] for c in res["candidates"]})
        d2 = next(c for c in res["candidates"] if c["paper_id"] == "D2")
        self.assertEqual(d2["found_via"], ["S1", "N1", "D2"])

    def test_cycle_terminates(self):
        graph = {
            "S1": [_paper("N1", 0.8, "references")],
            "N1": [_paper("S1", 0.9, "citations")],   # back-edge (cycle)
        }
        with patch.object(ps.embeddings, "cosine_matrix", _fake_cosine), \
             patch.object(ps.s2, "search_papers",
                          return_value=[self._raw_seed("S1", 0.95)]), \
             patch.object(ps, "neighbors",
                          side_effect=lambda pid, d="both", cache_dir=None: graph.get(pid, [])):
            res = ps.snowball("t", ["t"], max_depth=3, branching=5)
        # S1 recorded once (seen guards the cycle); N1 present
        self.assertEqual(sorted(c["paper_id"] for c in res["candidates"]), ["N1", "S1"])

    def test_empty_query_returns_empty(self):
        res = ps.snowball("t", [], max_depth=2)
        self.assertEqual(res["candidates"], [])
        self.assertEqual(res["edges"], [])
        self.assertEqual(res["target"], "t")
        self.assertEqual(res["status"], "empty_query")

    def test_no_relevant_seeds_returns_empty(self):
        with patch.object(ps.embeddings, "cosine_matrix", _fake_cosine), \
             patch.object(ps.s2, "search_papers", return_value=[]):
            res = ps.snowball("t", ["t"], max_depth=2)
        self.assertEqual(res["candidates"], [])
        self.assertEqual(res["seeds"], [])
        self.assertEqual(res["status"], "no_seeds")

    def test_search_failure_distinct_from_no_results(self):
        # search_papers returns None on hard error (429 exhaustion) — must be
        # reported as search_failed (retryable), NOT an empty field.
        with patch.object(ps.s2, "search_papers", return_value=None):
            res = ps.snowball("t", ["t"], max_depth=2)
        self.assertEqual(res["status"], "search_failed")
        self.assertEqual(res["candidates"], [])

    def test_string_keywords_accepted(self):
        with patch.object(ps.embeddings, "cosine_matrix", _fake_cosine), \
             patch.object(ps.s2, "search_papers",
                          return_value=[self._raw_seed("S1", 0.9)]) as mock_search, \
             patch.object(ps, "neighbors",
                          side_effect=lambda pid, d="both", cache_dir=None: []):
            ps.snowball("t", "one two three", max_depth=1)
        mock_search.assert_called_once()
        self.assertEqual(mock_search.call_args[0][0], "one two three")


if __name__ == "__main__":
    unittest.main()
