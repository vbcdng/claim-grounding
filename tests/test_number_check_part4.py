"""Task #146 — a decimal point printed as a middle dot ("1 ·37") is read as 1.37.

Real row: watchdog/rerun56_eggs2 claim t34, whose quoted proof prints the odds
ratios as "1 ·29 ..., 1 ·37 ... and 1 ·25". Offline, no model calls.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.papertrail import number_direction as nd


class TestMiddleDot(unittest.TestCase):
    def test_spaced_middle_dot_becomes_decimal_point(self):
        t = nd.normalize_source("odds ratios were 1 ·29 (95% CI), 1 ·37 and 1 ·25")
        self.assertIn("1.29", t)
        self.assertIn("1.37", t)
        self.assertIn("1.25", t)

    def test_figure_is_found_in_source(self):
        c = {"text": "Odds ratios of around 1.25 to 1.37 were reported.",
             "paper_ids": ["s"], "markers": ["s"], "verdict": "supported"}
        src = "The odds ratios were 1 ·29 (1 ·10 to 1 ·49), 1 ·37 (1 ·19 to 1 ·57) and 1 ·25."
        out = nd.check_claim_numbers(c, {"s": nd.normalize_source(src)})
        self.assertEqual(out["missing"], [])

    def test_wrong_figure_still_missing(self):
        c = {"text": "The odds ratio was 1.52.", "paper_ids": ["s"],
             "markers": ["s"], "verdict": "supported"}
        out = nd.check_claim_numbers(c, {"s": nd.normalize_source("The odds ratio was 1 ·37.")})
        self.assertTrue(out["missing"])

    def test_chemical_formula_left_alone(self):
        self.assertEqual(nd.normalize_source("Na2MoO4·2H2O and CaSO4·2H2O"),
                         "Na2MoO4·2H2O and CaSO4·2H2O")


if __name__ == "__main__":
    unittest.main()
