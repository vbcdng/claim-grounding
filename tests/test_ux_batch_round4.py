"""Round-4 UX batch (owner asks from rounds 2-3): covset same-sentence
grouping (r2 t5), review filename from the text's frontmatter title (r3),
needs_citation triage mark + louder own+fact chip (r3 t6). Rendering and
parsing only — no API calls.

Run:  venv/bin/python3 -m unittest tests.test_ux_batch_round4 -v
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.papertrail import text_decomposer, viewer
from modules.papertrail.claude_research_importer import write_artifacts

S1 = "The tournament ran from June to October 2022 and experts gave higher numbers."
S2 = "Superforecasters updated little after exchanging arguments."


def _analysis(claim, meta=None):
    pids = claim.get("paper_ids", [])
    return {"text_claims": [claim],
            "sources": [{"paper_id": p, "key": p, "filename": f"{p}.txt",
                         "title": f"Source {p}"} for p in pids],
            "coverage": {"totals": {"claims": 1, "supported": 1, "unsupported": 0,
                                    "own": 0, "omitted": 0}},
            "metadata": meta or {}, "omitted": []}


def _render(claim):
    out = os.path.join(tempfile.mkdtemp(), "v.html")
    viewer.generate(_analysis(claim), out)
    with open(out, encoding="utf-8") as f:
        return f.read()


def _cov_claim(covered):
    return {"id": "t1", "text": "Claim.", "markers": ["k"], "paper_ids": ["p1"],
            "verdict": "supported", "method": "llm", "reason": "r",
            "covering_checked": True, "proof_state": "full",
            "covering": {"covered": covered, "uncovered": []},
            "evidences": [{"paper_id": "p1", "source_title": "Source p1",
                           "supported": True, "sentence": S1, "page": 1,
                           "snippet": S1[:20]}]}


def _ce(part, sentence):
    return {"component": part, "paper_id": "p1", "source_title": "Source p1",
            "sentence": sentence, "page": 1, "snippet": sentence[:20]}


class TestCovsetGrouping(unittest.TestCase):

    def test_adjacent_same_sentence_parts_group_and_quote_once(self):
        page = _render(_cov_claim([_ce("the 2022 dates", S1),
                                   _ce("experts higher", S1)]))
        self.assertIn("✓ the 2022 dates · ✓ experts higher", page)
        # ONE covset row total: the parts grouped, the sentence beneath once.
        self.assertEqual(page.count('class="covset-row"'), 1)

    def test_different_sentences_stay_separate_rows(self):
        page = _render(_cov_claim([_ce("the dates", S1), _ce("no updating", S2)]))
        self.assertNotIn("· ✓", page)
        self.assertIn(S2, page)

    def test_part_with_two_proof_sentences_shows_both(self):
        page = _render(_cov_claim([_ce("the finding", S1), _ce("the finding", S2)]))
        self.assertIn(S1, page)
        self.assertIn(S2, page)


class TestStripFrontmatter(unittest.TestCase):

    def test_title_and_body(self):
        title, body = text_decomposer.strip_frontmatter(
            '---\ntitle: "Why Pots?"\nbibliography: x.bib\n---\n\nBody. [[k]]')
        self.assertEqual(title, "Why Pots?")
        self.assertTrue(body.lstrip().startswith("Body."))

    def test_no_frontmatter_untouched(self):
        title, body = text_decomposer.strip_frontmatter("Plain text. [[k]]")
        self.assertEqual(title, "")
        self.assertEqual(body, "Plain text. [[k]]")

    def test_mid_document_rule_not_stripped(self):
        raw = "Intro.\n\n---\n\nMore."
        self.assertEqual(text_decomposer.strip_frontmatter(raw), ("", raw))

    def test_parse_references_skips_frontmatter(self):
        refs, body = text_decomposer.parse_references(
            "---\ntitle: T\n---\nA claim. [[k]]")
        self.assertNotIn("title", body)
        claims = text_decomposer.extract_claims(body)
        self.assertEqual(len(claims), 1)
        self.assertEqual(claims[0]["markers"], ["k"])


class TestRunNameFromTitle(unittest.TestCase):

    def _run_name(self, text_content, fname="my_text.md"):
        d = tempfile.mkdtemp()
        tf = os.path.join(d, fname)
        with open(tf, "w", encoding="utf-8") as f:
            f.write(text_content)
        claim = {"id": "t1", "text": "C.", "markers": [], "paper_ids": [],
                 "verdict": "own", "method": "none", "evidences": []}
        data = viewer._review_data(_analysis(claim, meta={"text_file": tf}),
                                   [claim], d)
        return data["run"]["run_name"]

    def test_frontmatter_title_names_the_run(self):
        name = self._run_name('---\ntitle: "Why Do Old Pots End Up on Fence Posts?"\n---\n\nBody.')
        self.assertTrue(name.startswith("Why-Do-Old-Pots"))
        self.assertNotIn("?", name)

    def test_no_title_falls_back_to_text_stem(self):
        self.assertEqual(self._run_name("Body.", fname="bentonite_essay.md"),
                         "bentonite_essay")

    def test_no_text_file_falls_back_to_output_dir(self):
        claim = {"id": "t1", "text": "C.", "markers": [], "paper_ids": [],
                 "verdict": "own", "method": "none", "evidences": []}
        d = tempfile.mkdtemp()
        data = viewer._review_data(_analysis(claim), [claim], d)
        self.assertEqual(data["run"]["run_name"], os.path.basename(d))


class TestNeedsCitationMark(unittest.TestCase):

    def test_triage_row_has_needs_citation_button(self):
        page = _render(_cov_claim([]))
        self.assertIn('data-mark="needs_citation"', page)
        self.assertIn("needs_citation: 'needs a citation'", page)

    def test_own_fact_card_gets_loud_chip(self):
        claim = {"id": "t1", "text": "Uncited fact.", "markers": [], "paper_ids": [],
                 "verdict": "own", "method": "none", "evidences": [],
                 "own_kind": {"kind": "fact", "reason": "checkable"}}
        page = _render(claim)
        self.assertIn('citechip loud', page)
        self.assertIn('📎 citation needed?', page)


class TestImporterTitlePreserved(unittest.TestCase):

    def test_write_artifacts_prepends_title_frontmatter(self):
        d = tempfile.mkdtemp()
        write_artifacts(d, "A claim. [[k]]", ["k"], {}, title="Why Pots?")
        with open(os.path.join(d, "my_text.md"), encoding="utf-8") as f:
            raw = f.read()
        title, body = text_decomposer.strip_frontmatter(raw)
        self.assertEqual(title, "Why Pots?")
        self.assertTrue(body.lstrip().startswith("A claim."))

    def test_no_title_writes_plain_text(self):
        d = tempfile.mkdtemp()
        write_artifacts(d, "A claim. [[k]]", ["k"], {})
        with open(os.path.join(d, "my_text.md"), encoding="utf-8") as f:
            self.assertFalse(f.read().startswith("---"))


if __name__ == "__main__":
    unittest.main()
