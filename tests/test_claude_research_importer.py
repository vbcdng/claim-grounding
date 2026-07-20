"""Unit tests for the Claude Science importer — pure parsing, no API calls.

Run:  venv/bin/python3 -m unittest tests.test_claude_research_importer -v
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.papertrail.claude_research_importer import (
    PandocCitationRecognizer, convert_block, _parse_bib_fields, _extract_doi,
    split_frontmatter,
)

REC = PandocCitationRecognizer()


def conv(text):
    return convert_block(text, REC)


class TestCitationRecognition(unittest.TestCase):
    def test_single_key(self):
        cits = REC.find_citations("Bostrom [@bostrom2014] argues X.")
        self.assertEqual([c.keys for c in cits], [["bostrom2014"]])

    def test_multi_key_semicolon(self):
        cits = REC.find_citations("AGI reshapes labor. [@jones; @lee]")
        self.assertEqual(cits[0].keys, ["jones", "lee"])

    def test_locator_text_ignored(self):
        cits = REC.find_citations("As shown [@smith2020, p. 12], it works.")
        self.assertEqual(cits[0].keys, ["smith2020"])

    def test_bare_at_not_matched(self):
        self.assertEqual(REC.find_citations("mail me @ example@foo.org today"), [])


class TestMarkerRelocation(unittest.TestCase):
    def test_mid_sentence_moves_to_sentence_end(self):
        out, keys = conv("In his book, Bostrom [@b2014] argues that X follows. Next sentence.")
        self.assertEqual(out, "In his book, Bostrom argues that X follows. [[b2014]] Next sentence.")
        self.assertEqual(keys, ["b2014"])

    def test_citation_just_before_period(self):
        out, _ = conv("Three from Europe [@x]. Those counts measure Y.")
        self.assertEqual(out, "Three from Europe. [[x]] Those counts measure Y.")

    def test_two_citations_same_sentence_group(self):
        out, keys = conv("X falls [@a], and Y outgrows the rest [@b]. After.")
        self.assertEqual(out, "X falls, and Y outgrows the rest. [[a]] [[b]] After.")
        self.assertEqual(keys, ["a", "b"])

    def test_abbreviation_not_a_boundary(self):
        out, _ = conv("Smith [@s] cites e.g. apples and oranges. Next.")
        self.assertEqual(out, "Smith cites e.g. apples and oranges. [[s]] Next.")

    def test_decimal_not_a_boundary(self):
        out, _ = conv("Growth [@k] was 3.5 percent overall. Next.")
        self.assertEqual(out, "Growth was 3.5 percent overall. [[k]] Next.")

    def test_initial_not_a_boundary(self):
        out, _ = conv("Jones [@j] follows B. F. Skinner closely. Next.")
        self.assertEqual(out, "Jones follows B. F. Skinner closely. [[j]] Next.")

    def test_no_trailing_period_inserts_at_end(self):
        out, _ = conv("A last line with no period [@k]")
        self.assertEqual(out, "A last line with no period [[k]]")

    def test_boundary_search_skips_other_citation_spans(self):
        # the second bracket contains "p. 3." — must not terminate the first
        # citation's sentence inside it
        out, _ = conv("First point [@a] and detail [@b, p. 3.] combined here. Next.")
        self.assertEqual(out, "First point and detail combined here. [[a]] [[b]] Next.")

    def test_quote_after_period_included(self):
        out, _ = conv('He called it "a divergence [@u]." Next.')
        self.assertEqual(out, 'He called it "a divergence." [[u]] Next.')

    def test_no_citations_block_unchanged(self):
        block = "## A heading with no citations"
        self.assertEqual(conv(block), (block, []))


class TestBibtex(unittest.TestCase):
    def test_latex_escapes_unescaped(self):
        from modules.papertrail.claude_research_importer import _strip_braces
        self.assertEqual(_strip_braces(r"Big Data \& Society"), "Big Data & Society")
        self.assertEqual(_strip_braces(r"100\% {Renewable}"), "100% Renewable")

    def test_fields_braced_and_bare(self):
        f = _parse_bib_fields('author = {Smith, J.},\n  title = {A {Nested} Title},\n  year = 2024')
        self.assertEqual(f["author"], "Smith, J.")
        self.assertEqual(f["title"], "A {Nested} Title")
        self.assertEqual(f["year"], "2024")

    def test_doi_from_url(self):
        self.assertEqual(_extract_doi("https://doi.org/10.1000/xyz123"), "10.1000/xyz123")
        self.assertIsNone(_extract_doi("https://example.org/paper.pdf"))


class TestFrontmatter(unittest.TestCase):
    def test_split(self):
        meta, body = split_frontmatter('---\ntitle: "T"\nbibliography: refs.bib\n---\nBody text.')
        self.assertEqual(meta["bibliography"], "refs.bib")
        self.assertEqual(body, "Body text.")

    def test_absent(self):
        meta, body = split_frontmatter("No frontmatter here.")
        self.assertEqual(meta, {})
        self.assertEqual(body, "No frontmatter here.")


if __name__ == "__main__":
    unittest.main()
