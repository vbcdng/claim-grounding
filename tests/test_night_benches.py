"""Offline tests for the night-loop benches (wice_bench + synth_docs)."""

import importlib.util
import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load(name):
    path = os.path.join(ROOT, "benchmarks", f"{name}.py")
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


wice_bench = _load("wice_bench")
synth_docs = _load("synth_docs")


class TestWiceSafetyFilter(unittest.TestCase):
    def test_blocks_health_bio_chem_it(self):
        for bad in ("a trial of the cancer drug", "the bacteria species",
                    "a chemical compound", "the malware exploit kit"):
            self.assertFalse(wice_bench._safe({"claim": bad, "evidence": [], "meta": {}}), bad)

    def test_allows_general_topics(self):
        ok = {"claim": "The bridge was completed in 1904 by the crane company.",
              "evidence": ["It spans the river."], "meta": {"claim_title": "Bridges"}}
        self.assertTrue(wice_bench._safe(ok))

    def test_checks_evidence_text_too(self):
        item = {"claim": "He wrote a famous book.",
                "evidence": ["He later died of a rare disease."], "meta": {}}
        self.assertFalse(wice_bench._safe(item))


class TestWiceToolBucket(unittest.TestCase):
    def test_unsupported_maps_to_not_supported(self):
        self.assertEqual(wice_bench._tool_bucket({"verdict": "unsupported"}), "not_supported")

    def test_full_proof_maps_to_supported(self):
        c = {"verdict": "supported", "covering": {"uncovered": []}}
        self.assertEqual(wice_bench._tool_bucket(c), "supported")

    def test_amber_or_flags_map_to_partial(self):
        for c in ({"verdict": "supported", "covering": {"uncovered": ["x"]}},
                  {"verdict": "supported", "proof_state": "partial"},
                  {"verdict": "supported", "partial_support": True}):
            self.assertEqual(wice_bench._tool_bucket(c), "partially_supported", c)


class TestWiceAdjudicatedBucket(unittest.TestCase):
    """The arbiter-adjudicated scoring mapping (NEWSYS_EVAL_PLAN §2) —
    scoring-time only, never a verdict change."""

    def _b(self, claim):
        return wice_bench._adjudicated_bucket(claim)

    def test_no_arbiter_field_keeps_base(self):
        b, why = self._b({"verdict": "unsupported"})
        self.assertEqual(b, "not_supported")
        self.assertIsNone(why)
        b, why = self._b({"verdict": "supported", "partial_support": True})
        self.assertEqual(b, "partially_supported")
        self.assertIsNone(why)

    def test_unsupported_arbiter_supported_flips_to_supported(self):
        c = {"verdict": "unsupported", "arbiter": {"action": "supported", "proofs": []}}
        b, why = self._b(c)
        self.assertEqual(b, "supported")
        self.assertIsNotNone(why)

    def test_unsupported_tool_fetch_with_proofs_flips_to_supported(self):
        c = {"verdict": "unsupported",
             "arbiter": {"action": "wrong_or_insufficient_evidence", "proofs": ["q1"]}}
        self.assertEqual(self._b(c)[0], "supported")

    def test_unsupported_tool_fetch_all_quotes_dropped_stays(self):
        c = {"verdict": "unsupported",
             "arbiter": {"action": "wrong_or_insufficient_evidence", "proofs": [],
                         "quotes_dropped": 3}}
        b, why = self._b(c)
        self.assertEqual(b, "not_supported")
        self.assertIsNone(why)

    def test_unsupported_author_fix_with_proofs_is_partial(self):
        c = {"verdict": "unsupported",
             "arbiter": {"action": "add_citation_or_rewrite", "proofs": ["q1"]}}
        self.assertEqual(self._b(c)[0], "partially_supported")

    def test_unsupported_author_fix_no_proofs_stays_not_supported(self):
        c = {"verdict": "unsupported",
             "arbiter": {"action": "add_citation_or_rewrite", "proofs": []}}
        b, why = self._b(c)
        self.assertEqual(b, "not_supported")
        self.assertIsNone(why)

    def test_supported_with_gaps_arbiter_supported_flips_to_supported(self):
        c = {"verdict": "supported", "covering": {"uncovered": ["x"]},
             "arbiter": {"action": "supported", "proofs": []}}
        b, why = self._b(c)
        self.assertEqual(b, "supported")
        self.assertIsNotNone(why)

    def test_supported_with_gaps_author_fix_stays_partial(self):
        c = {"verdict": "supported", "partial_support": True,
             "arbiter": {"action": "add_citation_or_rewrite", "proofs": []}}
        b, why = self._b(c)
        self.assertEqual(b, "partially_supported")
        self.assertIsNone(why)  # same bucket, no flip

    def test_supported_with_gaps_tool_fetch_with_proofs_is_supported(self):
        c = {"verdict": "supported", "covering": {"uncovered": ["x"]},
             "arbiter": {"action": "wrong_or_insufficient_evidence", "proofs": ["q"]}}
        self.assertEqual(self._b(c)[0], "supported")

    def test_supported_full_conflict_candidate_author_fix_downgrades(self):
        # conflict-candidate trigger on a supported-full claim
        c = {"verdict": "supported",
             "arbiter": {"action": "add_citation_or_rewrite", "proofs": []}}
        b, why = self._b(c)
        self.assertEqual(b, "partially_supported")
        self.assertIsNotNone(why)

    def test_supported_full_arbiter_supported_no_flip(self):
        c = {"verdict": "supported", "arbiter": {"action": "supported", "proofs": []}}
        b, why = self._b(c)
        self.assertEqual(b, "supported")
        self.assertIsNone(why)


class TestSynthBuildAndScore(unittest.TestCase):
    def _build(self, seed=3):
        d = tempfile.mkdtemp(prefix="synthtest_")
        synth_docs.build(seed, d)
        gt = json.load(open(os.path.join(d, "synth_ground_truth.json")))
        return d, gt

    def test_build_is_deterministic_and_complete(self):
        d1, gt1 = self._build()
        d2, gt2 = self._build()
        self.assertEqual(gt1, gt2)
        self.assertEqual(
            sorted(gt1["claims"][k]["kind"] for k in gt1["claims"]),
            sorted(["expect_full", "expect_partial", "expect_unsupported",
                    "expect_overcite", "watch_decontext"]))
        refs = open(os.path.join(d1, "my_text.md.refs.txt")).read()
        self.assertIn(" = ", refs)          # INPUT_FORMAT key = filename
        self.assertNotIn("[[", refs)

    def _analysis(self, rows):
        f = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        json.dump({"text_claims": rows}, f)
        f.close()
        return f.name

    def test_score_passes_on_designed_behavior(self):
        d, gt = self._build()
        g = gt["claims"]
        full_key = next(k for k in g if g[k]["kind"] == "expect_full")
        anchors = g[full_key]["anchors"]
        partial_key = next(k for k in g if g[k]["kind"] == "expect_partial")
        unsup_key = next(k for k in g if g[k]["kind"] == "expect_unsupported")
        over_key = next(k for k in g if g[k]["kind"] == "expect_overcite")
        watch_key = next(k for k in g if g[k]["kind"] == "watch_decontext")
        rows = [
            {"markers": [full_key], "verdict": "supported",
             "covering": {"covered": [{"sentence": a} for a in anchors], "uncovered": []}},
            {"markers": [partial_key], "verdict": "supported",
             "covering": {"uncovered": [g[partial_key]["missing_terms"][0]]}},
            {"markers": [unsup_key], "verdict": "unsupported"},
            {"markers": [over_key], "verdict": "supported", "over_citation": ["riverguide"],
             "covering": {}},
            {"markers": [watch_key], "verdict": "supported", "covering": {}},
        ]
        rc = synth_docs.score(self._analysis(rows),
                              os.path.join(d, "synth_ground_truth.json"))
        self.assertEqual(rc, 0)

    def test_score_fails_on_invisible_gap_and_false_support(self):
        d, gt = self._build()
        g = gt["claims"]
        partial_key = next(k for k in g if g[k]["kind"] == "expect_partial")
        unsup_key = next(k for k in g if g[k]["kind"] == "expect_unsupported")
        rows = [
            # gap invisible: supported, no amber
            {"markers": [partial_key], "verdict": "supported", "covering": {"uncovered": []}},
            # false support on the contradicted claim
            {"markers": [unsup_key], "verdict": "supported", "covering": {}},
        ]
        rc = synth_docs.score(self._analysis(rows),
                              os.path.join(d, "synth_ground_truth.json"))
        self.assertEqual(rc, 1)

    def test_strict_judge_naming_gap_is_accepted(self):
        d, gt = self._build()
        g = gt["claims"]
        partial_key = next(k for k in g if g[k]["kind"] == "expect_partial")
        term = g[partial_key]["missing_terms"][0]
        rows = [{"markers": [partial_key], "verdict": "unsupported",
                 "reason": f"The source never mentions the {term}."}]
        # other keys missing -> failures, but the partial row itself must not fail
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            synth_docs.score(self._analysis(rows),
                             os.path.join(d, "synth_ground_truth.json"))
        out = buf.getvalue()
        self.assertNotIn(f"{partial_key}:", " ".join(
            line for line in out.splitlines() if line.strip().startswith("FAIL")))
        self.assertIn("strict verdict", out)


if __name__ == "__main__":
    unittest.main()
