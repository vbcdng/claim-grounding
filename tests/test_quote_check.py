import unittest
from modules.papertrail import quote_check as qc

SRC = ("The committee concluded that available evidence shows no appreciable "
       "relationship between dietary cholesterol and serum cholesterol. "
       "Intake should be as low as practical.")


class TestQuoteCheck(unittest.TestCase):
    def test_extract_ignores_scare_quotes(self):
        qs = qc.extract_quotes('The so-called "hyper-responder" effect is "real".')
        self.assertEqual(qs, [])                      # both under the word gate

    def test_extract_keeps_long_quote(self):
        t = 'The report said "available evidence shows no appreciable relationship here".'
        self.assertEqual(len(qc.extract_quotes(t)), 1)

    def test_quote_present_exact(self):
        found, score = qc.quote_in_text(
            "available evidence shows no appreciable relationship between dietary cholesterol and serum cholesterol", SRC)
        self.assertTrue(found)
        self.assertEqual(score, 1.0)

    def test_quote_present_fuzzy_whitespace_case(self):
        found, _ = qc.quote_in_text(
            "Available   Evidence shows NO appreciable relationship between dietary cholesterol and serum cholesterol", SRC)
        self.assertTrue(found)                        # normalization handles case/space

    def test_quote_absent(self):
        found, score = qc.quote_in_text(
            "cholesterol is not a nutrient of concern for overconsumption", SRC)
        self.assertFalse(found)
        self.assertLess(score, qc.FUZZY_THRESHOLD)

    def test_check_claim_flags_missing_only(self):
        claim = {
            "id": "t1", "verdict": "supported", "markers": ["src"],
            "text": ('The report said "available evidence shows no appreciable '
                     'relationship between dietary cholesterol and serum cholesterol" '
                     'and that "cholesterol is not a nutrient of concern for overconsumption".'),
        }
        fs = qc.check_claim(claim, {"src": SRC})
        self.assertEqual(len(fs), 1)                  # only the second quote is absent
        self.assertIn("nutrient of concern", fs[0]["quote"])

    def test_uncited_claim_no_findings(self):
        claim = {"id": "t2", "verdict": "own", "markers": [],
                 "text": 'A quote "available evidence shows no appreciable relationship at all here".'}
        self.assertEqual(qc.check_claim(claim, {"src": SRC}), [])


if __name__ == "__main__":
    unittest.main()
