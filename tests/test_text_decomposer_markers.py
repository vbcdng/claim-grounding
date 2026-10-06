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


class LongOpenerStubs(unittest.TestCase):
    """Card #25: the 6-word cap split openers that describe the study before
    naming its authors (pilot100:cidev0023, 9 words), leaving the real
    assertion uncited. A longer opener now merges when it ends in an
    author-naming tail and asserts nothing of its own; one that asserts
    something still splits."""

    def _one(self, body, marker="a"):
        claims = td.extract_claims(body)
        self.assertEqual(len(claims), 1, claims)
        self.assertEqual(claims[0]["markers"], [marker])
        return claims[0]["text"]

    def _split(self, body):
        claims = td.extract_claims(body)
        self.assertEqual(len(claims), 2, claims)
        self.assertEqual(claims[1]["markers"], [])
        return claims

    def test_cidev0023_long_frame_opener_merges(self):
        text = self._one(
            "Using preliminary US county level analysis, Abedi et al. ([[cidev0023]]) "
            "document that existing rates of poverty, disease and the presence of "
            "ethnic minorities were all associated with higher infection.", "cidev0023")
        self.assertEqual(text,
                         "Using preliminary US county level analysis, Abedi et al. document "
                         "that existing rates of poverty, disease and the presence of ethnic "
                         "minorities were all associated with higher infection.")

    def test_cidev0038_paragraph_end_claim_unchanged(self):
        # The marker closes the paragraph and the segment asserts a similarity:
        # it is a real claim and stays one cited claim, exactly as before.
        body = ("We developed a mathematical model describing the distribution of "
                "observed SARS-CoV-2 viral loads over time after infection. This model "
                "is similar to that used by Larremore et al. ([[cidev0038]])")
        claims = td.extract_claims(body)
        self.assertEqual(len(claims), 1)
        self.assertEqual(claims[0]["markers"], ["cidev0038"])
        self.assertTrue(claims[0]["text"].endswith("similar to that used by Larremore et al."))

    def test_long_according_to_without_comma_merges(self):
        self.assertIn("rates rose", self._one(
            "According to a large national survey of older adults by Kim et al.[[a]] rates rose."))

    def test_long_preposition_opener_and_colleagues_merges(self):
        self.assertIn("found lower blood pressure", self._one(
            "In a 2020 cohort of older adults in rural areas, Smith and colleagues[[a]] "
            "found lower blood pressure."))

    def test_long_opener_author_list_with_year_merges(self):
        self.assertIn("reported fewer infections", self._one(
            "In a cohort study of three thousand adults, Kim, Lee and Park (2019)[[a]] "
            "reported fewer infections."))

    def test_long_opener_with_its_own_clause_still_splits(self):
        claims = self._split(
            "Using a large cohort we showed that smoking causes cancer, as did Kim et al.[[a]] "
            "in mice.")
        self.assertEqual(claims[0]["markers"], ["a"])

    def test_long_opener_with_auxiliary_verb_still_splits(self):
        # comma + preposition start are both present; only the verbs ("was
        # shown") refuse it
        self._split("In mice the drug was shown to lower blood pressure, Kim et al.[[a]] "
                    "in two trials.")

    def test_long_opener_not_starting_with_preposition_still_splits(self):
        self._split("This model is similar to the one used by Larremore et al.[[a]] "
                    "and it performs well.")

    def test_long_opener_yearless_name_list_still_splits(self):
        # a place list, not a byline: a yearless name list is refused on the long path
        self._split("In the three regions studied during the survey, China and Japan[[a]] "
                    "saw the largest rises.")

    def test_long_preposition_opener_without_comma_still_splits(self):
        self._split("In a study of mice fed a high fat diet Kim et al.[[a]] found weight gain.")

    def test_over_long_opener_still_splits(self):
        self._split("Using " + "very " * 25 + "old data, Kim et al.[[a]] found weight gain.")


class MarkerTypos(unittest.TestCase):
    """find_marker_typos (task #69 item 1): near-miss markers the parser
    rejects must produce a warning instead of vanishing silently."""

    def test_space_inside_key_is_flagged(self):
        warns = td.find_marker_typos("Print caused it [[my key]].")
        self.assertEqual(len(warns), 1)
        self.assertIn("[[my key]]", warns[0])

    def test_bad_characters_flagged(self):
        warns = td.find_marker_typos("Result [[smith.2020]] and [[a,b]].")
        self.assertEqual(len(warns), 2)

    def test_missing_closing_bracket_flagged(self):
        warns = td.find_marker_typos("Print caused it [[eisenstein1980]. More text.")
        self.assertEqual(len(warns), 1)
        self.assertIn("closing", warns[0])

    def test_missing_opening_bracket_flagged(self):
        warns = td.find_marker_typos("Print caused it [eisenstein1980]]. More text.")
        self.assertEqual(len(warns), 1)
        self.assertIn("opening", warns[0])

    def test_valid_markers_not_flagged(self):
        self.assertEqual(td.find_marker_typos(
            "One [[a]], grouped ([[b]]; [[c-d_2020]]), adjacent [[e]][[f]]."), [])

    def test_plain_single_brackets_not_flagged(self):
        self.assertEqual(td.find_marker_typos(
            "Numeric cites [1] and [12, 13] and editorial [sic] stay quiet."), [])

    def test_duplicate_typo_warns_once(self):
        warns = td.find_marker_typos("A [[my key]] and again [[my key]].")
        self.assertEqual(len(warns), 1)
