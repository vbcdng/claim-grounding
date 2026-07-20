"""Evidence independence — fully offline (fake S2 + fake LLM, no network).

Run:  venv/bin/python3 -m unittest tests.test_independence -v
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.papertrail import evidence_independence as ei


class FakeLLM:
    def __init__(self, response, model="fake-model"):
        self.response = response
        self.model = model
        self.calls = 0

    def call(self, prompt, temperature=0.0, max_output_tokens=512):
        self.calls += 1
        self.last_prompt = prompt
        return self.response


def make_analysis():
    """Three sources: a1+b2 share an author (Smith), c3 is independent.
    t1 cites a1+b2 (the correlated pair), t2 cites c3 alone.

    Realistic shape guard: claims carry HASH paper_ids (sha1 of the filename in
    live runs) while sources map paper_id -> citation key — the per-claim
    arithmetic must translate, or it silently never fires (2026-07-06 review)."""
    return {
        "sources": [
            {"paper_id": "9f3a" * 10, "key": "a1",
             "title": "Alpha Study of Widgets — Smith, J. and Jones, K."},
            {"paper_id": "1c7b" * 10, "key": "b2",
             "title": "Beta Replication of Widgets — Smith, J."},
            {"paper_id": "55e0" * 10, "key": "c3",
             "title": "Gamma Independent Work — Brown, P."},
        ],
        "text_claims": [
            {"id": "t1", "text": "Widgets work.", "paper_ids": ["9f3a" * 10, "1c7b" * 10],
             "verdict": "supported"},
            {"id": "t2", "text": "Solo claim.", "paper_ids": ["55e0" * 10],
             "verdict": "supported"},
        ],
    }


def fake_s2(authors_by_prefix, refs_by_pid=None):
    """(s2_lookup, s2_refs) fakes. authors_by_prefix maps a title prefix to a
    list of (authorId, name); the paperId is 'pid_<prefix>'."""
    refs_by_pid = refs_by_pid or {}

    def lookup(title):
        for prefix, authors in authors_by_prefix.items():
            if title.startswith(prefix):
                return {"status": "matched", "paper": {
                    "paperId": "pid_" + prefix.split()[0].lower(),
                    "title": title, "year": 2024, "externalIds": {},
                    "authors": [{"authorId": i, "name": n} for i, n in authors]}}
        return {"status": "no_match", "paper": None}

    def refs(pid):
        if pid in refs_by_pid:
            return {"status": "ok", "refs": refs_by_pid[pid]}
        return {"status": "ok", "refs": []}

    return lookup, refs


class TestAuthorTail(unittest.TestCase):
    def test_paper1_style_tail(self):
        self.assertEqual(
            ei.parse_author_tail("Computing Power and Governance — Sastry, G. and others"),
            ["sastry"])

    def test_multi_author_tail(self):
        self.assertEqual(
            ei.parse_author_tail("Future Possibilities — Redshaw, J. and Suddendorf, T."),
            ["redshaw", "suddendorf"])

    def test_org_tail_yields_nothing(self):
        self.assertEqual(ei.parse_author_tail("AI Index Report — Stanford HAI"), [])

    def test_no_tail(self):
        self.assertEqual(ei.parse_author_tail("Sorption_Studies_on_Bentonite"), [])
        clean, tail = ei.split_title_tail("Sorption_Studies_on_Bentonite")
        self.assertEqual(tail, "")

    def test_hyphen_inside_title_is_not_a_separator(self):
        clean, tail = ei.split_title_tail("Cesium-134 retention in clay")
        self.assertEqual(clean, "Cesium-134 retention in clay")


class TestSignals(unittest.TestCase):
    def test_shared_authors_strong_via_s2_ids(self):
        a = {"authors_local": [], "s2": {"authors": [{"id": "77", "name": "J. Smith"}]}}
        b = {"authors_local": [], "s2": {"authors": [{"id": "77", "name": "J. Smith"},
                                                     {"id": "88", "name": "K. Jones"}]}}
        sig = ei._sig_shared_authors(a, b)
        self.assertEqual(sig["level"], "strong")
        self.assertEqual(sig["ids"], ["77"])

    def test_shared_surname_weak_without_s2(self):
        a = {"authors_local": ["smith"], "s2": None}
        b = {"authors_local": ["smith", "jones"], "s2": None}
        sig = ei._sig_shared_authors(a, b)
        self.assertEqual(sig["level"], "weak")
        self.assertEqual(sig["surnames"], ["smith"])

    def test_surname_match_suppressed_when_s2_says_disjoint(self):
        # Two different Smiths: both papers have S2 author lists, no shared id.
        a = {"authors_local": ["smith"], "s2": {"authors": [{"id": "1", "name": "A. Smith"}]}}
        b = {"authors_local": ["smith"], "s2": {"authors": [{"id": "2", "name": "B. Smith"}]}}
        self.assertIsNone(ei._sig_shared_authors(a, b))

    def test_direct_citation_by_paperid(self):
        a = {"title": "Alpha Study", "s2": {"paper_id": "pa", "doi": None,
                                            "refs": [{"paperId": "pb", "title": "x", "doi": None}]}}
        b = {"title": "Beta Study", "s2": {"paper_id": "pb", "doi": None, "refs": []}}
        sig = ei._sig_direct_citation(a, b)
        self.assertEqual(sig["level"], "strong")
        self.assertEqual(sig["directions"], ["a_cites_b"])

    def test_direct_citation_by_fuzzy_title(self):
        a = {"title": "Alpha Study of Widgets",
             "s2": {"paper_id": "pa", "doi": None,
                    "refs": [{"paperId": None, "doi": None,
                              "title": "Beta Replication of Widgets"}]}}
        b = {"title": "Beta Replication of Widgets — Smith, J.",
             "s2": {"paper_id": "pb", "doi": None, "refs": []}}
        self.assertEqual(ei._sig_direct_citation(a, b)["level"], "strong")

    def test_direct_citation_unknown_when_no_refs(self):
        a = {"title": "Alpha", "s2": None}
        b = {"title": "Beta", "s2": None}
        self.assertIsNone(ei._sig_direct_citation(a, b))

    def test_bib_coupling_levels(self):
        def src(n_shared, n_total, pid_prefix):
            refs = [{"paperId": f"shared{i}", "doi": None, "title": None}
                    for i in range(n_shared)]
            refs += [{"paperId": f"{pid_prefix}{i}", "doi": None, "title": None}
                     for i in range(n_total - n_shared)]
            return {"s2": {"refs": refs}}
        strong = ei._sig_bib_coupling(src(6, 10, "a"), src(6, 10, "b"))
        self.assertEqual(strong["level"], "strong")
        weak = ei._sig_bib_coupling(src(3, 10, "a"), src(3, 10, "b"))
        self.assertEqual(weak["level"], "weak")
        none = ei._sig_bib_coupling(src(1, 10, "a"), src(1, 10, "b"))
        self.assertEqual(none["level"], "none")
        self.assertIsNone(ei._sig_bib_coupling({"s2": None}, src(1, 10, "b")))

    def test_content_overlap_from_dedup_clusters(self):
        # a has 4 clustered claims, 2 sharing clusters with b (smaller side = b: 2/2).
        dedup = {"clusters": [
            [{"source": "a", "claim": 1}, {"source": "b", "claim": 9}],
            [{"source": "a", "claim": 2}, {"source": "b", "claim": 8}],
            [{"source": "a", "claim": 3}, {"source": "c", "claim": 7}],
            [{"source": "a", "claim": 4}],
        ]}
        sig = ei._sig_content_overlap(dedup, "a", "b")
        self.assertEqual(sig["level"], "strong")
        self.assertIsNone(ei._sig_content_overlap(None, "a", "b"))
        self.assertIsNone(ei._sig_content_overlap({"clusters": []}, "a", "b"))

    def test_content_overlap_normalizes_by_total_claims_not_clustered(self):
        # Same 2 shared clusters as above, but b actually has 20 total claims.
        # Old code divided by clustered claims (2/2 -> 1.0 "strong", saturated);
        # the merge fix divides by n_claims (2/20 = 0.10 -> "weak").
        dedup = {"clusters": [
            [{"source": "a", "claim": 1}, {"source": "b", "claim": 9}],
            [{"source": "a", "claim": 2}, {"source": "b", "claim": 8}],
        ], "n_claims": {"a": 4, "b": 20}}
        sig = ei._sig_content_overlap(dedup, "a", "b")
        # smaller by total claims = a (4): 2 of a's 4 claims overlap -> 0.5 strong;
        # verify it used a's total (4), not clustered (2 -> would be 1.0).
        self.assertEqual(sig["ratio"], 0.5)
        self.assertEqual(sig["level"], "strong")
        # Now make a large too: 2/40 = 0.05 -> below weak floor -> "none".
        dedup["n_claims"] = {"a": 40, "b": 20}
        sig2 = ei._sig_content_overlap(dedup, "a", "b")
        self.assertEqual(sig2["ratio"], 0.1)   # smaller = b (20): 2/20
        self.assertEqual(sig2["level"], "weak")

    def test_weak_only_signals_never_count_as_strong(self):
        relations, strength = ei._pair_verdict(
            {"shared_authors": {"level": "weak", "surnames": ["smith"]},
             "direct_citation": None,
             "bib_coupling": {"level": "none", "ratio": 0.05, "shared": 1},
             "content_overlap": None})
        self.assertEqual(relations, ["shared_authors"])
        self.assertEqual(strength, "weak")


class TestAssess(unittest.TestCase):
    def test_local_only_weak_pair_does_not_merge_clusters(self):
        # Surname-only match (no S2): flagged weak, but effective count stays 2.
        payload = ei.assess_independence(make_analysis(), s2_enrich=False)
        self.assertEqual(payload["method"], "local")
        self.assertEqual(len(payload["pairs"]), 1)
        self.assertEqual(payload["pairs"][0]["strength"], "weak")
        self.assertEqual(payload["summary"]["n_clusters"], 3)   # nothing merged
        t1 = payload["per_claim"]["t1"]
        self.assertEqual(t1["effective"], 2)                    # weak ≠ arithmetic
        self.assertEqual(t1["flagged_pairs"], [["a1", "b2"]])   # but surfaced

    def test_s2_confirmed_pair_merges_and_reduces_effective(self):
        lookup, refs = fake_s2({
            "Alpha Study": [("77", "J. Smith"), ("88", "K. Jones")],
            "Beta Replication": [("77", "J. Smith")],
            "Gamma Independent": [("99", "P. Brown")]})
        payload = ei.assess_independence(make_analysis(), s2_lookup=lookup,
                                         s2_refs=refs)
        self.assertEqual(payload["method"], "local+s2")
        pair = payload["pairs"][0]
        self.assertEqual(pair["strength"], "strong")
        self.assertIn("shared_authors", pair["relations"])
        self.assertIn(["a1", "b2"], payload["clusters"])
        self.assertEqual(payload["summary"]["n_clusters"], 2)
        self.assertEqual(payload["per_claim"]["t1"]["effective"], 1)
        # Single-citation claims are not annotated.
        self.assertNotIn("t2", payload["per_claim"])

    def test_absent_from_s2_is_unknown_not_a_flag(self):
        lookup, refs = fake_s2({})           # nothing matches
        analysis = make_analysis()
        # Remove the surname overlap so no local signal fires either.
        analysis["sources"][1]["title"] = "Beta Replication of Widgets — Doe, X."
        payload = ei.assess_independence(analysis, s2_lookup=lookup, s2_refs=refs)
        self.assertEqual(payload["pairs"], [])
        self.assertEqual(payload["summary"]["n_clusters"], 3)
        self.assertIsNone(payload["sources"][0]["s2"])

    def test_refs_stripped_from_payload_sources(self):
        lookup, refs = fake_s2(
            {"Alpha Study": [("77", "J. Smith")]},
            refs_by_pid={"pid_alpha": [{"paperId": "x", "title": "t", "doi": None}]})
        payload = ei.assess_independence(make_analysis(), s2_lookup=lookup,
                                         s2_refs=refs)
        for s in payload["sources"]:
            if s["s2"]:
                self.assertNotIn("refs", s["s2"])
                self.assertIn("n_refs", s["s2"])


class TestCaching(unittest.TestCase):
    def test_matched_cached_but_failure_retried(self):
        calls = {"n": 0}

        def flaky_lookup(title):
            calls["n"] += 1
            if title.startswith("Alpha") and calls["n"] >= 2:
                return {"status": "matched",
                        "paper": {"paperId": "pa", "title": title, "year": 2024,
                                  "externalIds": {}, "authors": []}}
            return {"status": "search_failed", "paper": None}

        def refs(pid):
            return {"status": "ok", "refs": []}

        analysis = {"sources": [{"key": "a1", "title": "Alpha Study — Smith, J."}],
                    "text_claims": []}
        with tempfile.TemporaryDirectory() as cache:
            ei.assess_independence(analysis, cache_dir=cache,
                                   s2_lookup=flaky_lookup, s2_refs=refs)
            n1 = calls["n"]
            ei.assess_independence(analysis, cache_dir=cache,
                                   s2_lookup=flaky_lookup, s2_refs=refs)
            # A failure is retried on the next run (not cached)...
            self.assertGreater(calls["n"], n1)
            n2 = calls["n"]  # this retry matched (n > 2) and was cached
            ei.assess_independence(analysis, cache_dir=cache,
                                   s2_lookup=flaky_lookup, s2_refs=refs)
            # ...but a determinate answer is served from disk.
            self.assertEqual(calls["n"], n2)


class TestLLMConfirm(unittest.TestCase):
    def _strong_setup(self):
        lookup, refs = fake_s2({
            "Alpha Study": [("77", "J. Smith")],
            "Beta Replication": [("77", "J. Smith")],
            "Gamma Independent": [("99", "P. Brown")]})
        return make_analysis(), lookup, refs

    def test_independent_verdict_downgrades_strong_to_weak(self):
        analysis, lookup, refs = self._strong_setup()
        llm = FakeLLM(json.dumps({"relation": "independent", "independent": True,
                                  "why": "different Smiths"}))
        payload = ei.assess_independence(analysis, llm=llm, s2_lookup=lookup,
                                         s2_refs=refs)
        self.assertEqual(llm.calls, 1)
        pair = payload["pairs"][0]
        self.assertEqual(pair["strength"], "weak")
        self.assertTrue(pair["llm"]["independent"])
        # Downgraded → clusters no longer merge, effective count restored.
        self.assertEqual(payload["per_claim"]["t1"]["effective"], 2)
        self.assertEqual(payload["method"], "local+s2+llm")
        self.assertEqual(payload["model"], "fake-model")

    def test_failopen_keeps_heuristic_flag(self):
        analysis, lookup, refs = self._strong_setup()
        llm = FakeLLM("garbage not json")
        payload = ei.assess_independence(analysis, llm=llm, s2_lookup=lookup,
                                         s2_refs=refs)
        pair = payload["pairs"][0]
        self.assertEqual(pair["strength"], "strong")   # flag survives
        self.assertIsNone(pair["llm"])

    def test_only_co_cited_pairs_are_confirmed(self):
        # a1+c3 also share an author, but no claim cites them together.
        analysis, _, refs = self._strong_setup()
        lookup, _ = fake_s2({
            "Alpha Study": [("77", "J. Smith"), ("99", "P. Brown")],
            "Beta Replication": [("77", "J. Smith")],
            "Gamma Independent": [("99", "P. Brown")]})
        llm = FakeLLM(json.dumps({"relation": "same_team", "independent": False,
                                  "why": "same author"}))
        payload = ei.assess_independence(analysis, llm=llm, s2_lookup=lookup,
                                         s2_refs=refs)
        self.assertEqual(len(payload["pairs"]), 2)
        self.assertEqual(llm.calls, 1)                 # only the co-cited pair


class TestWriteAndCLI(unittest.TestCase):
    def test_write_and_cli_no_s2(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "analysis.json"), "w", encoding="utf-8") as f:
                json.dump(make_analysis(), f)
            rc = ei.main([d, "--no-s2"])
            self.assertEqual(rc, 0)
            with open(os.path.join(d, "independence.json"), encoding="utf-8") as f:
                payload = json.load(f)
            self.assertEqual(payload["method"], "local")
            self.assertIn("t1", payload["per_claim"])

    def test_cli_missing_analysis(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(ei.main([d]), 1)


if __name__ == "__main__":
    unittest.main()
