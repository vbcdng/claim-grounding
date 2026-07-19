"""Slot B4: the nanopub-isomorphic provenance export. Offline, pure-function
tests — the exporter must reshape analysis.json without inventing or dropping
verdict-bearing data, and must keep the three-graph split + PROV-O/OA names
stable (they are the interop contract, docs/PROVENANCE_FORMAT.md)."""

import json
import os
import tempfile
import unittest

from modules.papertrail import provenance_export as pe

ANALYSIS = {
    "metadata": {"text_file": "/x/my_text.md", "model": "gemini/flash-lite",
                 "timestamp": "2026-07-05T12:00:00", "source_hashes": {"iea": "abc123"}},
    "sources": [{"paper_id": "p1", "key": "iea", "filename": "iea.pdf",
                 "title": "Energy and AI", "num_claims": 2}],
    "text_claims": [
        {"id": "t1", "text": "Use doubles by 2030.", "verdict": "supported",
         "method": "combined", "votes": "2-0", "reason": "stated", "paper_ids": ["p1"],
         "markers": ["iea"], "partial_support": {"reason": "x", "votes": "3-0"},
         "evidences": [{"paper_id": "p1", "supported": True, "sentence": "It doubles.",
                        "page": 3, "cosine": 0.91, "reason": "verbatim",
                        "snippet": "It dou", "window": "...", "source_title": "Energy and AI"}]},
        {"id": "t2", "text": "We believe X.", "verdict": "own", "own_kind": "opinion",
         "method": "no_citation_marker", "markers": [], "paper_ids": [], "evidences": []},
    ],
    "omitted": [
        {"paper_id": "p1", "source_claim_id": "p1_c9", "text": "Cooling is 40%.",
         "relevance": 0.8, "evidence": "Cooling accounts for 40%.", "page": 7,
         "snippet": "Cooling", "source_title": "Energy and AI"},
        {"paper_id": "p1", "source_claim_id": "p1_c3", "text": "Low relevance.",
         "relevance": 0.1, "evidence": None, "page": None, "snippet": "",
         "source_title": "Energy and AI"},
    ],
}


class ExportShape(unittest.TestCase):
    def setUp(self):
        self.out = pe.export(ANALYSIS)

    def test_three_graph_split_present_on_every_record(self):
        self.assertTrue(self.out["records"])
        for r in self.out["records"]:
            self.assertIn("assertion", r)
            self.assertIn("provenance", r)
        self.assertIn("publication_info", self.out)   # shared pubinfo graph

    def test_context_maps_our_keys_to_prov_and_oa(self):
        ctx = self.out["@context"]
        self.assertEqual(ctx["derived_from"], "prov:wasDerivedFrom")
        self.assertEqual(ctx["exact"], "oa:exact")
        self.assertEqual(ctx["assertion"], "np:assertion")

    def test_cited_claim_provenance(self):
        r = next(x for x in self.out["records"] if x["id"].endswith(":t1"))
        self.assertEqual(r["assertion"]["verdict"], "supported")
        self.assertEqual(r["assertion"]["partial_support"]["votes"], "3-0")
        self.assertEqual(r["provenance"]["derived_from"], ["source:p1"])
        self.assertEqual(r["provenance"]["generated_by"]["votes"], "2-0")
        use = r["provenance"]["used"][0]
        self.assertEqual(use["target"]["exact"], "It doubles.")
        self.assertEqual(use["target"]["page"], 3)
        self.assertEqual(use["quoted_from"], "source:p1")

    def test_own_claim_attributed_to_author_not_derived(self):
        r = next(x for x in self.out["records"] if x["id"].endswith(":t2"))
        self.assertEqual(r["provenance"]["attributed_to"], "author")
        self.assertNotIn("derived_from", r["provenance"])
        self.assertEqual(r["assertion"]["own_kind"], "opinion")

    def test_omitted_ranked_capped_and_attributed_to_source(self):
        out = pe.export(ANALYSIS, include_omitted=1)
        om = [r for r in out["records"] if r["assertion"]["verdict"] == "omitted"]
        self.assertEqual(len(om), 1)                       # capped
        self.assertIn("p1_c9", om[0]["id"])                # the higher-relevance one
        self.assertEqual(om[0]["provenance"]["attributed_to"], "source:p1")
        self.assertEqual(out["publication_info"]["omitted_total"], 2)

    def test_sources_carry_content_hash(self):
        s = self.out["sources"][0]
        self.assertEqual(s["content_sha1"], "abc123")
        self.assertEqual(s["citation_key"], "iea")

    def test_run_id_stable_and_in_record_ids(self):
        again = pe.export(ANALYSIS)
        self.assertEqual(self.out["publication_info"]["run_id"],
                         again["publication_info"]["run_id"])
        rid = self.out["publication_info"]["run_id"]
        self.assertTrue(all(r["id"].startswith(f"urn:pt:{rid}:") for r in self.out["records"]))

    def test_record_count_covers_all_text_claims(self):
        n_text = len(ANALYSIS["text_claims"])
        self.assertEqual(self.out["publication_info"]["n_records"],
                         len(self.out["records"]))
        self.assertGreaterEqual(len(self.out["records"]), n_text)


class ExportFile(unittest.TestCase):
    def test_writes_provenance_json_next_to_analysis(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "analysis.json"), "w", encoding="utf-8") as f:
                json.dump(ANALYSIS, f)
            path = pe.export_file(d)
            self.assertEqual(os.path.basename(path), "provenance.json")
            with open(path, encoding="utf-8") as f:
                out = json.load(f)
            self.assertEqual(out["format"], "papertrail-provenance")
