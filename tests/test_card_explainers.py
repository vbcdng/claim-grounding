"""Card explainers from the owner walkthrough (2026-07-07, todo items 10-14):
multi-source OR semantics stated on the card, null-sentence wording, context
expander on every evidence row, evidence-length clamp, secondhand-citation
chip, cited-sources-disagreement chip, component-check note, and the
missing-component hunt note. Rendering only — no API calls.

Run:  venv/bin/python3 -m unittest tests.test_card_explainers -v
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.papertrail import viewer

CLAIM = "Europe lags in compute and the gap keeps growing."


def _e(pid, title, supported, sentence, reason="", window="", via=None):
    return {"paper_id": pid, "source_title": title, "supported": supported,
            "sentence": sentence, "page": 1, "snippet": (sentence or "")[:20],
            "reason": reason, "window": window, "via": via}


def _analysis(claim):
    pids = claim.get("paper_ids", [])
    return {"text_claims": [claim],
            "sources": [{"paper_id": p, "key": p, "filename": f"{p}.txt",
                         "title": f"Source {p}"} for p in pids],
            "coverage": {"totals": {"claims": 1, "supported": 0, "unsupported": 0,
                                    "own": 0, "omitted": 0}},
            "metadata": {}, "omitted": []}


def _render(claim):
    out = os.path.join(tempfile.mkdtemp(), "v.html")
    viewer.generate(_analysis(claim), out)
    with open(out, encoding="utf-8") as f:
        page = f.read()
    # Drop the static legend so chip-label assertions match only real card chips.
    pre, _, rest = page.partition('<details class="legend">')
    _, _, post = rest.partition('</details>')
    return pre + post


def _base(verdict="supported", method="llm", **kw):
    c = {"id": "t1", "text": CLAIM, "markers": ["a", "b"], "paper_ids": ["p1", "p2"],
         "verdict": verdict, "method": method, "reason": "r",
         "evidences": [_e("p1", "Alpha", True, "Europe lags in compute today."),
                       _e("p2", "Beta", False, "Effect of Proxy Model Alignment.",
                          reason="not about compute")]}
    c["evidence"] = c["evidences"][0]
    c.update(kw)
    return c


class TestMultiSourceExplainer(unittest.TestCase):

    def test_or_semantics_note_when_one_of_n_supports(self):
        page = _render(_base())
        self.assertIn("multisource-note", page)
        self.assertIn("at least ONE cited source", page)
        self.assertIn("Supported via <b>Alpha</b>", page)

    def test_no_note_when_all_sources_support(self):
        c = _base()
        c["evidences"][1]["supported"] = True
        self.assertNotIn("at least ONE cited source", _render(c))

    def test_null_sentence_row_says_no_relevant_passage(self):
        c = _base()
        c["evidences"][1]["sentence"] = None
        page = _render(c)
        self.assertIn("no relevant passage found", page)       # the row chip
        self.assertIn("No relevant passage found in this source", page)


class TestEvidenceRows(unittest.TestCase):

    def test_context_expander_on_supported_rows_too(self):
        c = _base()
        c["evidences"][0]["window"] = ("Before sentence. Europe lags in compute today. "
                                       "After sentence.")
        page = _render(c)
        self.assertIn("Context — what the judge read", page)
        self.assertIn("After sentence.", page)

    def test_giant_sentence_is_clamped(self):
        c = _base()
        c["evidences"][1]["sentence"] = "word " * 400   # ~2000 chars of nav dump
        page = _render(c)
        self.assertIn("show the full passage", page)


class TestSecondhandChip(unittest.TestCase):

    def test_parenthetical_citation_in_supporting_sentence_flags(self):
        c = _base()
        c["evidences"][0]["sentence"] = ("Growth still hits the Baumol bottleneck "
                                         "(Baumol, 1967) in every simulation.")
        page = _render(c)
        self.assertIn("secondhand evidence?", page)
        self.assertIn("citing the original", page)

    def test_bracket_citation_flags_and_clean_sentence_does_not(self):
        c = _base()
        c["evidences"][0]["sentence"] = "Prior work established the limit [12]."
        self.assertIn("secondhand evidence?", _render(c))
        self.assertNotIn("secondhand evidence?", _render(_base()))

    def test_nonsupporting_rows_never_flag(self):
        c = _base()
        c["evidences"][1]["sentence"] = "As shown before (Smith, 2019)."
        self.assertNotIn("secondhand evidence?", _render(c))


class TestDisagreementChip(unittest.TestCase):

    def test_contradicting_cocited_reason_flags(self):
        c = _base()
        c["evidences"][1]["reason"] = "the passage contradicts the claim that the gap grows"
        page = _render(c)
        self.assertIn("sources may disagree?", page)
        self.assertIn("disagree-note", page)

    def test_merely_absent_evidence_does_not_flag(self):
        self.assertNotIn("sources may disagree?", _render(_base()))


class TestComponentNotes(unittest.TestCase):

    def test_component_check_note_on_unsupported(self):
        c = _base(verdict="unsupported", method="llm_fulltext")
        c["evidences"][0]["supported"] = False
        c["component_check"] = {
            "found": ["Europe lags in compute"], "missing": ["the gap keeps growing"],
            "rescued": False,
            "evidence": [{"component": "Europe lags in compute", "paper_id": "p1",
                          "source_title": "Alpha",
                          "sentence": "Europe lags in compute today.", "page": 1}]}
        page = _render(c)
        # P2 symmetric display (owner ruling 2026-07-11): proven parts visible
        # WITH their sentences, all unproven parts listed
        self.assertIn("compcheck-note", page)
        self.assertIn("WERE found in the cited", page)
        self.assertIn("Europe lags in compute today.", page)     # proof sentence shown
        self.assertIn("compcheck-missing", page)
        self.assertIn("the gap keeps growing", page)             # unproven part listed
        self.assertIn("support these parts elsewhere", page)

    def test_component_check_missing_only(self):
        # nothing provable: no green block, the missing line still renders
        c = _base(verdict="unsupported", method="llm_fulltext")
        c["evidences"][0]["supported"] = False
        c["component_check"] = {"found": [], "missing": ["the gap keeps growing"],
                                "rescued": False, "evidence": []}
        page = _render(c)
        self.assertNotIn("WERE found in the cited", page)
        self.assertIn('class="compcheck-missing"', page)

    def test_component_rescue_method_note(self):
        c = _base(method="component_rescue")
        c["evidences"][1]["supported"] = True
        page = _render(c)
        self.assertIn("piece by piece", page)

    def test_hunt_note_found_and_not_found(self):
        c = _base()
        c["partial_support"] = {
            "reason": "x", "votes": "3-0", "escalated": True,
            "component_hunt": [
                {"component": "the gap keeps growing",
                 "found_in": [{"paper_id": "p9", "source_title": "Gamma", "key": "g"}],
                 "searched": 2},
                {"component": "compute doubles", "found_in": [], "searched": 2}]}
        page = _render(c)
        self.assertIn("may be covered by <b>Gamma</b>", page)
        self.assertIn("did not find", page)


class TestCoveringBlock(unittest.TestCase):
    """Covering-set evidence display (loop round-1 fix, 2026-07-10)."""

    def _cov_claim(self, covered=True, uncovered=True):
        cov = {"covered": [], "uncovered": []}
        if covered:
            cov["covered"] = [{"component": "Europe lags in compute",
                               "paper_id": "p1", "source_title": "Alpha",
                               "sentence": "Europe lags in compute today.",
                               "page": 2, "snippet": "Europe lags"}]
        if uncovered:
            cov["uncovered"] = ["the gap keeps growing"]
        return _base(covering=cov, covering_checked=True)

    def test_uncovered_parts_render_amber_and_always_visible(self):
        page = _render(self._cov_claim())
        self.assertIn('class="covset-miss"', page)
        self.assertIn("No evidence shown for: <b>the gap keeps growing</b>", page)

    def test_covered_parts_render_component_to_sentence_mapping(self):
        page = _render(self._cov_claim())
        self.assertIn("Evidence coverage", page)
        self.assertIn("✓ Europe lags in compute", page)
        self.assertIn("1 part with shown proof, 1 without", page)

    def test_full_coverage_shows_no_amber_line(self):
        page = _render(self._cov_claim(uncovered=False))
        self.assertNotIn('class="covset-miss"', page)
        self.assertIn("Evidence coverage", page)

    def test_unsupported_claim_ignores_covering(self):
        c = self._cov_claim()
        c["verdict"] = "unsupported"
        page = _render(c)
        self.assertNotIn("Evidence coverage", page)
        self.assertNotIn('class="covset-miss"', page)

    def test_claim_without_covering_renders_unchanged(self):
        page = _render(_base())
        self.assertNotIn("Evidence coverage", page)
        self.assertNotIn('class="covset-miss"', page)

    def test_repair_brief_includes_coverage_gap(self):
        page = _render(self._cov_claim())
        self.assertIn("Coverage gap: no displayed sentence proves:", page)

    def test_context_span_toggle_renders(self):
        c = self._cov_claim()
        c["covering"]["spans"] = [{"paper_id": "p1", "source_title": "Alpha",
                                   "n_used": 2,
                                   "text": "Europe lags in compute today. Filler. The gap grows."}]
        page = _render(c)
        self.assertIn("Read it in context", page)
        self.assertIn("The gap grows.", page)


if __name__ == "__main__":
    unittest.main()
