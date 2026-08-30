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


class NarrativeCitationStubs(unittest.TestCase):
    """Narrative citations put the marker mid-sentence, right after an
    attribution phrase ("Kim et al.[[a]] found X."), unlike the parenthetical
    case ("...found X ([[a]])."). Splitting before the marker there produces a
    name-only claim (unsupported — a name isn't a claim) plus an orphaned,
    uncited "own" claim for the real assertion. Fix: the attribution stub is
    carried forward and merged into the following segment instead of being
    emitted on its own."""

    def test_et_al_stub_merges_forward(self):
        body = "Kim et al.[[cidev0078]] demonstrated airborne transmission in ferrets."
        claims = td.extract_claims(body)
        self.assertEqual(len(claims), 1)
        self.assertEqual(claims[0]["markers"], ["cidev0078"])
        self.assertEqual(claims[0]["text"],
                          "Kim et al. demonstrated airborne transmission in ferrets.")

    def test_et_al_with_year_stub_merges_forward(self):
        body = "Kim et al. (2020)[[a]] showed the same result in mice."
        claims = td.extract_claims(body)
        self.assertEqual(len(claims), 1)
        self.assertEqual(claims[0]["markers"], ["a"])
        self.assertTrue(claims[0]["text"].startswith("Kim et al. (2020) showed"))

    def test_bare_author_list_stub_merges_forward(self):
        body = "Kim, Lee & Park (2019)[[x]] showed similar effects in mice."
        claims = td.extract_claims(body)
        self.assertEqual(len(claims), 1)
        self.assertEqual(claims[0]["markers"], ["x"])
        self.assertTrue(claims[0]["text"].startswith("Kim, Lee & Park (2019) showed"))

    def test_frame_opener_stub_merges_forward(self):
        body = "In contrast to other reports[[k]] we found that infection rates dropped."
        claims = td.extract_claims(body)
        self.assertEqual(len(claims), 1)
        self.assertEqual(claims[0]["markers"], ["k"])
        self.assertEqual(claims[0]["text"],
                          "In contrast to other reports we found that infection rates dropped.")

    def test_second_frame_opener_according_to(self):
        claims = td.extract_claims("According to[[k]] the manual, this should work.")
        self.assertEqual(len(claims), 1)
        self.assertEqual(claims[0]["markers"], ["k"])
        self.assertEqual(claims[0]["text"], "According to the manual, this should work.")

    def test_list_case_unchanged(self):
        # MUST NOT CHANGE: each list item is a real claim with its own marker.
        claims = td.extract_claims("Item A [[a]], item B [[b]], item C [[c]].")
        self.assertEqual([c["text"] for c in claims], ["Item A", "item B", "item C"])
        self.assertEqual([c["markers"] for c in claims], [["a"], ["b"], ["c"]])

    def test_stub_at_end_of_paragraph_not_merged(self):
        # Nothing to merge into in this block -> emit the stub as-is (today's
        # behaviour), never dropped, never merged into the next paragraph.
        body = "Kim et al.[[a]]\n\nSome other paragraph text with no marker."
        claims = td.extract_claims(body)
        self.assertEqual(len(claims), 2)
        self.assertEqual(claims[0]["text"], "Kim et al.")
        self.assertEqual(claims[0]["markers"], ["a"])
        self.assertEqual(claims[1]["text"], "Some other paragraph text with no marker.")
        self.assertEqual(claims[1]["markers"], [])

    def test_stub_at_end_of_text_not_merged(self):
        body = "The study concluded with strong evidence. Kim et al.[[a]]"
        claims = td.extract_claims(body)
        self.assertEqual(len(claims), 1)
        self.assertEqual(claims[0]["text"], "The study concluded with strong evidence. Kim et al.")
        self.assertEqual(claims[0]["markers"], ["a"])

    def test_tail_of_prior_sentence_not_treated_as_stub(self):
        # The segment before the marker is NOT a bare attribution stub -- it's
        # a completed sentence plus an opener, so today's split-before-marker
        # behaviour is kept (conservative: false on anything ambiguous).
        body = "This ends a sentence. Kim et al.[[a]] found nothing new."
        claims = td.extract_claims(body)
        self.assertEqual(len(claims), 2)
        self.assertEqual(claims[0]["text"], "This ends a sentence. Kim et al.")
        self.assertEqual(claims[0]["markers"], ["a"])
        self.assertEqual(claims[1]["text"], "found nothing new.")
        self.assertEqual(claims[1]["markers"], [])

    def test_marker_dedup_preserved_through_merge(self):
        # A duplicate marker group attached to a carried-forward stub must
        # still dedupe once merged into the following segment.
        body = "Kim et al.[[a]] [[a]] found significant results."
        claims = td.extract_claims(body)
        self.assertEqual(len(claims), 1)
        self.assertEqual(claims[0]["text"], "Kim et al. found significant results.")
        self.assertEqual(claims[0]["markers"], ["a"])

    def test_normal_end_of_sentence_marker_unchanged(self):
        claims = td.extract_claims("A grounded statement worth citing [[a]].")
        self.assertEqual(len(claims), 1)
        self.assertEqual(claims[0]["text"], "A grounded statement worth citing")
        self.assertEqual(claims[0]["markers"], ["a"])


if __name__ == "__main__":
    unittest.main()


class ProperNounListNotAStub(unittest.TestCase):
    """An enumeration of proper nouns must keep one claim (and one marker) per
    item. A lone capitalised word is therefore NOT an attribution stub: reading
    "China[[b]]" as one would merge the whole list into a single claim and lose
    the per-item citations (found while reviewing the 2026-08-01 narrative-
    citation fix)."""

    def test_country_list_keeps_one_marker_per_item(self):
        claims = td.extract_claims(
            "Surveys ran in the United States[[a]], China[[b]], "
            "and Japan[[c]] during 2020.")
        markers = [c["markers"] for c in claims if c["markers"]]
        self.assertEqual(markers, [["a"], ["b"], ["c"]])

    def test_single_capitalised_word_is_not_a_stub(self):
        claims = td.extract_claims("China[[a]] reported a sharp fall in trade.")
        self.assertEqual(claims[0]["text"], "China")
        self.assertEqual(claims[0]["markers"], ["a"])

    def test_two_names_still_merge_forward(self):
        claims = td.extract_claims("Kim and Lee[[a]] found the opposite.")
        self.assertEqual(len(claims), 1)
        self.assertEqual(claims[0]["markers"], ["a"])
        self.assertIn("found the opposite", claims[0]["text"])

    def test_one_name_with_a_year_still_merges_forward(self):
        claims = td.extract_claims("Kim (2019)[[a]] found the opposite.")
        self.assertEqual(len(claims), 1)
        self.assertEqual(claims[0]["markers"], ["a"])
        self.assertIn("found the opposite", claims[0]["text"])
