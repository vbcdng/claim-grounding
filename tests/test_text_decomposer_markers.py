"""Marker-splitting in text_decomposer: grouped citations must NOT leave
punctuation-only "claims". The eggs case study surfaced 9/63 cards that were pure
punctuation (').' and ';') because the author grouped citations as
"([[a]]; [[b]])" and the ';' / trailing ')' fell out as their own segments.
Offline only — pure parsing, no LLM."""

import re
import unittest

from modules.papertrail import text_decomposer as td


def _alpha(s):
    return re.sub(r"[^A-Za-z0-9]", "", s)


class GroupedCitations(unittest.TestCase):
    def test_semicolon_group_is_one_claim_with_both_markers(self):
        body = ("added dietary cholesterol raises both LDL and HDL, with the "
                "magnitude depending on the individual ([[griffin2013]]; [[blesso2018]]).")
        claims = td.extract_claims(body)
        self.assertEqual(len(claims), 1)
        self.assertEqual(claims[0]["markers"], ["griffin2013", "blesso2018"])
        # no dangling '(' at the end, no ')'/';' fragments
        self.assertFalse(claims[0]["text"].rstrip().endswith("("))
        self.assertTrue(claims[0]["text"].endswith("individual"))

    def test_comma_separated_group(self):
        claims = td.extract_claims("The effect is real [[a]], [[b]], [[c]].")
        self.assertEqual(len(claims), 1)
        self.assertEqual(claims[0]["markers"], ["a", "b", "c"])

    def test_whitespace_group_unchanged(self):
        # the pre-existing whitespace-separated grouping still works
        claims = td.extract_claims("A grounded statement [[a]] [[b]].")
        self.assertEqual(len(claims), 1)
        self.assertEqual(claims[0]["markers"], ["a", "b"])


class NoPunctuationOnlyClaims(unittest.TestCase):
    def test_no_punctuation_only_claim_emitted(self):
        body = ("First point with support ([[a]]; [[b]]). Second point that "
                "continues the paragraph and cites one source [[c]].")
        claims = td.extract_claims(body)
        for c in claims:
            self.assertTrue(_alpha(c["text"]),
                            f"punctuation-only claim leaked: {c['text']!r}")

    def test_citation_only_paragraph_yields_no_claim(self):
        self.assertEqual(td.extract_claims("([[a]]; [[b]])."), [])

    def test_trailing_paren_and_semicolon_dropped(self):
        # the exact eggs failure region: text ( [[a]] ; [[b]] ) .
        claims = td.extract_claims("Reviews reach the same verdict ([[griffin2013]]; [[blesso2018]]).")
        self.assertEqual(len(claims), 1)
        self.assertNotIn(";", claims[0]["text"])

    def test_multi_paragraph_no_junk(self):
        body = ("Intro thesis with no citation.\n\n"
                "A claim ([[a]]).\n\n"
                "Another claim ([[b]]; [[c]]).")
        claims = td.extract_claims(body)
        self.assertEqual(len(claims), 3)
        self.assertTrue(all(_alpha(c["text"]) for c in claims))
        self.assertEqual(claims[1]["markers"], ["a"])
        self.assertEqual(claims[2]["markers"], ["b", "c"])


if __name__ == "__main__":
    unittest.main()
