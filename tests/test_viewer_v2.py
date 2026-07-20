"""Viewer v2 (docs/VIEWER_V2_DESIGN.md): display states, shared RUN_ID with v1,
always-visible triage, and the verdict field never changing. Offline."""

import os
import re
import unittest
import tempfile

from modules.papertrail import viewer, viewer_v2


def _analysis():
    return {
        "text_claims": [
            {"id": "t0", "verdict": "own", "method": "none", "text": "My own idea.",
             "markers": [], "paper_ids": [], "evidences": [],
             "own_kind": {"kind": "fact", "reason": "checkable stat"}},
            {"id": "t1", "verdict": "supported", "method": "llm", "cosine": 0.9,
             "text": "Fully proven claim.", "markers": ["a"], "paper_ids": ["p1"],
             "evidences": [{"paper_id": "p1", "source_title": "Src A", "supported": True,
                            "sentence": "Proof one.", "page": 1, "snippet": "Proof one."}],
             "proof_state": "full",
             "covering": {"covered": [
                 {"component": "part one", "paper_id": "p1", "source_title": "Src A",
                  "sentence": "Proof one.", "page": 1, "snippet": "Proof one."},
                 {"component": "part two", "paper_id": "p1", "source_title": "Src A",
                  "sentence": "Proof two.", "page": 2, "snippet": "Proof two."}],
                 "uncovered": [], "common_knowledge": []}},
            {"id": "t2", "verdict": "supported", "method": "llm", "cosine": 0.9,
             "text": "Amber claim with a surviving gap.", "markers": ["a"],
             "paper_ids": ["p1"], "proof_state": "partial",
             "evidences": [{"paper_id": "p1", "source_title": "Src A", "supported": True,
                            "sentence": "Proof one.", "page": 1, "snippet": "Proof one."}],
             "covering": {"covered": [
                 {"component": "the proven core", "paper_id": "p1", "source_title": "Src A",
                  "sentence": "Proof one.", "page": 1, "snippet": "Proof one."}],
                 "uncovered": ["the unproven flourish"], "common_knowledge": []},
             "arbiter": {"model": "m", "action": "add_citation_or_rewrite",
                         "missing_subclaim": "the unproven flourish",
                         "rewrite_suggestion": "A humbler sentence.",
                         "proofs": ["Proof one."], "why": "over-claims."}},
            {"id": "t3", "verdict": "unsupported", "method": "llm_fulltext", "cosine": 0.8,
             "text": "Failed claim.", "markers": ["a"], "paper_ids": ["p1"],
             "reason": "the source says otherwise",
             "evidences": [{"paper_id": "p1", "source_title": "Src A", "supported": False,
                            "via": "llm_fulltext", "sentence": "Nearby but not it.",
                            "page": 3, "snippet": "Nearby", "reason": "close only"}],
             "component_check": {"found": ["the true part"], "missing": ["the false part"],
                                 "evidence": [{"component": "the true part", "paper_id": "p1",
                                               "source_title": "Src A", "page": 4,
                                               "sentence": "The true part is stated."}]}},
        ],
        "omitted": [],
        "coverage": {"totals": {"claims": 4, "supported": 2, "unsupported": 1,
                                "own": 1, "omitted": 0},
                     "per_source": {"p1": {"title": "Src A", "used": 1,
                                           "total_source_claims": 2}}},
        "metadata": {"text_file": "/tmp/x.md", "timestamp": "2026-07-15", "model": "m"},
        "sources": [{"paper_id": "p1", "filename": "a.pdf", "title": "Src A"}],
    }


class TestViewerV2(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        a = _analysis()
        self.v2 = viewer_v2.generate(a, os.path.join(self.tmp.name, "viewer_v2.html"))
        self.v1 = viewer.generate(_analysis(), os.path.join(self.tmp.name, "viewer.html"))
        with open(self.v2, encoding="utf-8") as f:
            self.html = f.read()

    def test_display_states(self):
        self.assertIn('class="card supported', self.html)          # t1 green
        self.assertIn('class="card amber', self.html)              # t2 amber card
        self.assertIn("NOT PROVEN AS WRITTEN", self.html)
        self.assertIn('class="card unsupported', self.html)        # t3 red
        self.assertIn('claim amber', self.html)                    # amber text highlight

    def test_amber_always_visible_gap_and_rewrite(self):
        self.assertIn("No proof found for", self.html)
        self.assertIn("the unproven flourish", self.html)
        self.assertIn("A humbler sentence.", self.html)            # rewrite box
        # the verdict field itself is untouched (display-only invariant)
        self.assertIn("verdict field: supported", self.html)

    def test_load_review_file_import(self):
        # the viewer can load a saved review*.json back in (button + importer)
        self.assertIn("Load review file", self.html)
        self.assertIn("function importReview", self.html)
        self.assertIn('id="revfile"', self.html)
        self.assertIn("function refreshTriageDOM", self.html)

    def test_supported_shows_all_proof_rows(self):
        self.assertIn("Proof one.", self.html)
        self.assertIn("Proof two.", self.html)
        self.assertIn("part one", self.html)
        self.assertIn("part two", self.html)

    def test_unsupported_reason_and_partly_proven(self):
        self.assertIn("the source says otherwise", self.html)
        self.assertIn("Partly proven despite the verdict", self.html)
        self.assertIn("The true part is stated.", self.html)
        self.assertIn("the false part", self.html)

    def test_triage_on_every_card(self):
        self.assertEqual(self.html.count('class="triage"'), 4)

    def test_run_id_shared_with_v1(self):
        with open(self.v1, encoding="utf-8") as f:
            v1_html = f.read()
        rid = lambda h: re.search(r"const RUN_ID = '(\w+)'", h).group(1)
        self.assertEqual(rid(self.html), rid(v1_html))

    def test_own_fact_nudge_visible(self):
        self.assertIn("citation needed?", self.html)
        self.assertIn("checkable stat", self.html)

    def test_no_cli_fix_command(self):
        # Owner 7/15 #11: reader-facing cards never show a terminal command.
        self.assertNotIn("--fix-claim", self.html)
        self.assertNotIn("fixcmd", self.html)

    def test_mode_segment_shows_state(self):
        # Owner 7/15 #7: the control displays the CURRENT view, not the target.
        self.assertIn('id="modeSimple"', self.html)
        self.assertIn('id="modeExpert"', self.html)

    def test_proofs_behind_one_click(self):
        # Owner 7/15 #5: proof rows live in their own expander.
        self.assertIn("show proof sentences", self.html)
        self.assertIn("proof for the proven parts", self.html)   # amber variant

    def test_what_was_checked_expander(self):
        # Owner 7/15 #10: non-supporting passages under their own expander.
        self.assertIn("what was checked", self.html)

    def test_legend_has_run_models(self):
        # Owner 7/15 #3: models used must be on record in the page.
        self.assertIn("primary judge:", self.html)
        self.assertIn("How it works:", self.html)


if __name__ == "__main__":
    unittest.main()
