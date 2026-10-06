"""Task #113 — the three remaining false-alarm causes of the figure-in-proof
check (--figure-in-proof), each found when card #99's 21 new warnings were
read one by one, each fixed behind that same switch.

1. A figure inside a capitalised name ("4 Year Liberal Studies Degree") is not
   a measurement.
2. The same amount in another unit ("10 kg" against the source's "23-pound").
3. A weak quoted proof sentence: the paper backs the figure a few sentences
   away, in a sentence about the same thing.

Offline: no model calls. Each test asserts an outcome the 2026-09-10 code gave
differently (wice_heldout/test_b11_run t2, wice_rerun74/test_b07_run t2 and
eggs_run t29 are the real rows each one stands for).
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.papertrail import number_direction as nd


def claim(text, key="src"):
    return {"id": "t1", "text": text, "verdict": "supported",
            "markers": [key], "paper_ids": [key]}


def scoped(text, source, proof, key="src", in_proof=True):
    c = claim(text, key)
    c["evidences"] = [{"paper_id": key, "sentence": proof, "window": proof,
                       "supported": True}]
    return nd.check_claim_numbers(c, {key: source}, in_proof=in_proof)


# ---------------------------------------------------------------- fix 1: names

DEGREES = ("St. Mary's University is accredited to offer a 3 Year General Studies "
           "Degree, a 4 Year Liberal Studies Degree, and 3 and 4 year History "
           "Degrees.")
ST_MARYS = ("St. Mary's University is accredited to offer eight Bachelor of Arts "
            "degrees, a Bachelor of Science and a Bachelor of Education after "
            "degree. Applicants must complete all degree requirements within 5 "
            "years.")


class TestNameIsNotAFigure(unittest.TestCase):
    def test_figures_inside_programme_names_are_skipped(self):
        figs = nd.extract_figures(DEGREES, skip_names=True)
        self.assertEqual(figs, [])

    def test_without_the_switch_they_are_still_extracted(self):
        raws = [f["raw"] for f in nd.extract_figures(DEGREES)]
        self.assertIn("4 Year", raws)
        self.assertIn("3 Year", raws)

    def test_the_real_row_raises_no_warning(self):
        p = scoped(DEGREES, ST_MARYS, ST_MARYS.split(". ")[0] + ".")
        self.assertIsNone(p)                      # nothing left to check

    def test_a_measurement_followed_by_lowercase_words_is_kept(self):
        figs = nd.extract_figures("Participants were followed for 12 years after "
                                  "enrolment.", skip_names=True)
        self.assertEqual([f["raw"] for f in figs], ["12 years"])

    def test_a_single_capitalised_word_is_not_a_name(self):
        """'5 years Before' needs two capitalised words to be read as a name."""
        figs = nd.extract_figures("They waited 5 years Before the review.",
                                  skip_names=True)
        self.assertEqual([f["raw"] for f in figs], ["5 years"])

    def test_a_percentage_is_never_skipped(self):
        figs = nd.extract_figures("A 42% Higher Risk Of Diabetes", skip_names=True)
        self.assertEqual([f["raw"] for f in figs], ["42%"])


# ---------------------------------------------------------- fix 2: other units

RAILGUN = ("It uses a form of electromagnetic energy known as the Lorentz force to "
           "hurl a 23-pound projectile at speeds exceeding Mach 7. According to "
           "the Navy, each 18-inch projectile costs about $25,000.")


class TestUnitConversion(unittest.TestCase):
    def test_kilograms_match_the_sources_pounds(self):
        fig = nd.extract_figures("The rounds weigh 10 kg.")[0]
        self.assertTrue(nd.figure_present(fig, RAILGUN, convert=True)[0])
        self.assertFalse(nd.figure_present(fig, RAILGUN)[0])      # old behaviour

    def test_millimetres_match_the_sources_inches(self):
        fig = nd.extract_figures("The rounds are 460 mm long.")[0]
        ok, how = nd.figure_present(fig, RAILGUN, convert=True)
        self.assertTrue(ok)
        self.assertEqual(how, "the same amount in another unit")

    def test_kilometres_match_miles(self):
        fig = nd.extract_figures("The loop is 230 km long.")[0]
        self.assertTrue(nd.figure_present(
            fig, "The trail has lengthened to more than 140 miles.", convert=True)[0])

    def test_a_wrong_amount_is_not_rescued_by_conversion(self):
        fig = nd.extract_figures("The rounds weigh 30 kg.")[0]
        self.assertFalse(nd.figure_present(fig, RAILGUN, convert=True)[0])

    def test_length_matches_length_and_mass_never_matches_length(self):
        fig = nd.extract_figures("The rounds are 46 cm long.")[0]  # 18 in = 45.7 cm
        self.assertTrue(nd.figure_present(fig, RAILGUN, convert=True)[0])
        fig = nd.extract_figures("The rounds weigh 0.457 kg.")[0]   # 18 in = 0.457 m
        self.assertFalse(nd.figure_present(fig, RAILGUN, convert=True)[0])

    def test_bare_in_is_not_read_as_inches(self):
        fig = nd.extract_figures("The site is 51 cm deep.")[0]    # 20 in = 50.8 cm
        self.assertFalse(nd.figure_present(
            fig, "Work began 20 in 2019 and ended later.", convert=True)[0])

    def test_the_real_row_is_found_in_the_proof_sentence(self):
        p = scoped("The hyper-velocity rounds weigh 10 kg (23 lb), are 18 in "
                   "(460 mm), and are fired at Mach 7.", RAILGUN, RAILGUN)
        self.assertEqual([c["where"] for c in p["clusters"]], ["sentence", "sentence"])
        self.assertEqual(p["elsewhere"], [])
        self.assertEqual(p["missing"], [])

    def test_without_the_switch_the_full_paper_lookup_is_unchanged(self):
        p = scoped("The rounds are 460 mm long.", RAILGUN, RAILGUN, in_proof=False)
        self.assertEqual(p["missing"], ["460 mm"])


# ------------------------------------------------------ fix 3: nearby paragraph

WEAK = ("However, egg consumption may be associated with an increased incidence "
        "of type 2 diabetes among the general population.")
BACKING = ("Comparing the highest with the lowest intake gave a pooled relative "
           "risk of 1.42 (1.09, 1.86) for type 2 diabetes.")
UNRELATED = ("Around 70% of the gap in per capita GDP with the US is explained by "
             "lower productivity.")
FILLER = ("Cohort sizes ranged widely across the included studies and follow-up "
          "was long in most of them. ")


class TestNearbyParagraph(unittest.TestCase):
    def test_a_figure_a_sentence_away_is_not_chipped(self):
        p = scoped("Egg consumption carried a 42% higher risk of type 2 diabetes.",
                   FILLER + WEAK + " " + BACKING + " " + FILLER, WEAK)
        self.assertEqual(p["clusters"][0]["where"], "paragraph")
        self.assertEqual(p["elsewhere"], [])

    def test_a_figure_far_from_the_proof_is_still_chipped(self):
        p = scoped("Egg consumption carried a 42% higher risk of type 2 diabetes.",
                   WEAK + " " + FILLER * 8 + BACKING, WEAK)
        self.assertEqual(p["clusters"][0]["where"], "paper")
        self.assertEqual(p["elsewhere"], ["42%"])

    def test_a_nearby_figure_about_something_else_is_still_chipped(self):
        """paper1_ds_reasoner t17: the claim's 70% is about AI models, the
        paper's 70% next to the proof is about the GDP gap."""
        weak = "At the same time, Europe will need a fundamentally new approach to skills."
        p = scoped("Around 70% of foundational AI models since 2017 were built in "
                   "the United States.", weak + " " + UNRELATED, weak)
        self.assertEqual(p["clusters"][0]["where"], "paper")
        self.assertEqual(p["elsewhere"], ["70%"])

    def test_a_proof_that_prints_its_own_different_figure_is_still_chipped(self):
        """Card 99's first hole: the proof says 43% for reuse, the writer says
        23%, and 23% sits right next to it about the same articles."""
        proof = "Of the data-using articles we examined, 43% reused data."
        src = (proof + " Funding acknowledgements were present in 23% of the "
               "articles, a separate count that has nothing to do with reuse.")
        p = scoped("23% of data-using articles reused data collected by others.",
                   src, proof)
        self.assertEqual(p["clusters"][0]["where"], "paper")
        self.assertEqual(p["elsewhere"], ["23%"])

    def test_without_the_switch_no_scope_is_recorded(self):
        p = scoped("Egg consumption carried a 42% higher risk of type 2 diabetes.",
                   WEAK + " " + BACKING, WEAK, in_proof=False)
        self.assertNotIn("where", p["clusters"][0])

    def test_topic_words_compare_on_their_first_five_letters(self):
        self.assertTrue(nd.topic_stems("diabetes") & nd.topic_stems("diabetic"))
        self.assertIn("CVD", nd.topic_stems("overall CVD risk"))
        self.assertEqual(nd.topic_stems("about the with"), set())


if __name__ == "__main__":
    unittest.main()
