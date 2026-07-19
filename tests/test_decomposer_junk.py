"""Model-agnostic post-decomposition junk filter (source_decomposer._is_junk_claim
+ its use in _extract_claims_from_text). Even a good decomposer occasionally emits
a non-claim lifted from the source — a funding/COI line, a reference entry, a bare
stat fragment. These pollute the 'unused points' panel and round-2 escalation.
Validated to drop ~1.3% of 5077 real eggs claims, all genuine junk.
Offline only — no LLM, no network."""

import unittest
from unittest import mock

from modules.papertrail import source_decomposer as sd


class IsJunkClaim(unittest.TestCase):
    def test_drops_citation_and_doi_entries(self):
        self.assertTrue(sd._is_junk_claim(
            "Bioactive egg components and inflammation was published in Nutrients 2015;7:7889-913."))
        self.assertTrue(sd._is_junk_claim("Available at doi:10.1136/heartjnl-2017-312651"))

    def test_drops_funding_and_coi_boilerplate(self):
        self.assertTrue(sd._is_junk_claim(
            "This work was supported by grants (81390540) from the National Natural Science Foundation."))
        self.assertTrue(sd._is_junk_claim("The authors declare no conflicts of interest."))
        self.assertTrue(sd._is_junk_claim("Correspondence to Dr Smith; all rights reserved."))

    def test_drops_email_table_caption_and_bare_stat(self):
        self.assertTrue(sd._is_junk_claim("Contact: jane.doe@university.edu for data requests."))
        self.assertTrue(sd._is_junk_claim("Table 2 Associations of egg consumption with CVD outcomes"))
        self.assertTrue(sd._is_junk_claim("P < 0.001 comparing US with non-US studies."))

    def test_keeps_real_claims_including_short_ones(self):
        # length is NOT a junk signal — these are all valid claims
        for s in ["Eggs are affordable.",
                  "Eggs are nutrient-dense.",
                  "Dietary cholesterol raises both LDL and HDL cholesterol.",
                  "Higher egg intake was associated with incident CVD (HR 1.05, 95% CI 1.02-1.08).",
                  "The hazard ratio for stroke was reduced in the highest quintile."]:
            self.assertFalse(sd._is_junk_claim(s), s)

    def test_year_in_prose_is_not_a_citation(self):
        # "year;vol:page" is the citation shape; a plain year in prose must survive
        self.assertFalse(sd._is_junk_claim("In 2018, egg consumption in China rose sharply."))


class ExtractDropsJunk(unittest.TestCase):
    def test_extract_filters_junk_and_keeps_real(self):
        raw = ["Eggs are affordable.",
               "The authors declare no conflicts of interest.",
               "Dietary cholesterol raises LDL cholesterol.",
               "P < 0.05 was considered significant.",
               "doi:10.1136/heartjnl-2017-312651"]
        llm = mock.MagicMock()
        llm.call_json.return_value = raw
        with mock.patch.object(sd, "_chunk_paragraphs", return_value=["chunk"]):
            out = sd._extract_claims_from_text("some text", llm)
        self.assertIn("Eggs are affordable.", out)
        self.assertIn("Dietary cholesterol raises LDL cholesterol.", out)
        self.assertNotIn("The authors declare no conflicts of interest.", out)
        self.assertNotIn("P < 0.05 was considered significant.", out)
        self.assertNotIn("doi:10.1136/heartjnl-2017-312651", out)
        self.assertEqual(len(out), 2)


if __name__ == "__main__":
    unittest.main()
