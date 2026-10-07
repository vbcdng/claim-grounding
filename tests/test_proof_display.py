"""Task #4 — the card must show the right proof sentence, honestly.

Three display defects, all measured on real runs before the fix:
  * a stored "sentence" that glues two source sentences together because the
    reference numbers between them were printed as superscripts (419 of 9,084
    stored evidence rows; fresh50:cidev0028 turned it into a false support),
  * a quote that stops mid-sentence (529 rows; 151 of them can be finished
    from the judged window, which is source text),
  * a quote that is a reference-list line or a dumped table row, not prose.

Plus the v1 layout half: the sentences that prove each part of the claim are
now in the always-visible part of the card, not hidden behind the details
button in the default simple view.

Offline: no API calls, no network.

Run:  venv/bin/python3 -m unittest tests.test_proof_display -v
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.papertrail import proof_display as pd
from modules.papertrail import viewer, viewer_v2

# Modelled on the real fresh50:cidev0028 evidence row, shortened to two
# sentences: the superscript reference numbers "2 , 3" glue them together,
# and the stored row stops in the middle of the second one. WINDOW is the
# judged window, which carries the rest of that second sentence.
GLUED = ("Cirrhosis leads to 2 million deaths per year through hepatic "
         "decompensation and hepatocellular carcinoma (HCC).2 , 3 "
         "Cirrhosis is characterised by immune dysregulation, leading to concerns "
         "that these")
WINDOW = ("The paragraph opens here. " + GLUED + " patients may be at increased "
          "risk of complications following "
          "SARS-CoV-2 infection.")


class TestSplitGlued(unittest.TestCase):
    def test_real_case_splits_into_two_complete_sentences(self):
        parts = pd.split_glued(GLUED)
        self.assertEqual(len(parts), 2)
        self.assertTrue(parts[0].endswith("(HCC)."))
        self.assertTrue(parts[1].startswith("Cirrhosis is characterised"))

    def test_a_decimal_in_a_table_is_not_a_split_point(self):
        # "Diatomite 2.7 The relatively high sorption..." — the character before
        # the point is a digit, so this is a number, not a sentence end.
        s = "Glauconite 3.6 Bentonite 2.8 Diatomite 2.7 The relatively high sorption held."
        self.assertEqual(pd.split_glued(s), [s])

    def test_ordinary_sentence_is_returned_unchanged(self):
        s = "The bridge was rebuilt in 1892 after the flood."
        self.assertEqual(pd.split_glued(s), [s])

    def test_a_short_tail_is_not_promoted_to_its_own_sentence(self):
        s = "The study reported a clear effect.12 Yes it did."
        parts = pd.split_glued(s)
        self.assertEqual(len(parts), 1)

    def test_empty_input_is_safe(self):
        self.assertEqual(pd.split_glued(""), [])
        self.assertEqual(pd.split_glued(None), [])


class TestCompleteCutoff(unittest.TestCase):
    def test_quote_is_finished_from_the_judged_window(self):
        text, done = pd.complete_cutoff(
            "Cirrhosis is characterised by immune dysregulation, leading to "
            "concerns that these", WINDOW)
        self.assertTrue(done)
        self.assertTrue(text.endswith("SARS-CoV-2 infection."))

    def test_nothing_is_invented_without_a_window(self):
        frag = "leading to concerns that these"
        self.assertEqual(pd.complete_cutoff(frag, None), (frag, False))
        self.assertEqual(pd.complete_cutoff(frag, "unrelated text here."), (frag, False))

    def test_a_complete_sentence_is_left_alone(self):
        s = "The bridge was rebuilt."
        self.assertEqual(pd.complete_cutoff(s, "x " + s + " More text."), (s, False))


class TestFlaws(unittest.TestCase):
    def test_a_dumped_table_row_is_named(self):
        s = ("NAFLD| 322 (43.2%)| 274 (46.1%)| 48 (32.0%)| 0.55 (0.38-0.81)| 0.002| "
             "1.01 (0.57-1.79)| 0.965")
        self.assertIn("table", pd.flaws(s))

    def test_prose_quoting_statistics_is_not_called_a_table(self):
        s = ("Regarding aetiology, ALD showed a positive association with death "
             "(OR 3.11; 95% CI 2.12-4.55; p <0.001) in the adjusted model.")
        self.assertNotIn("table", pd.flaws(s))

    def test_the_matchers_own_reference_test_is_used_when_supplied(self):
        self.assertIn("reference", pd.flaws("Review of Economic Studies.",
                                            lambda s: True))
        self.assertEqual(pd.flaws("Review of Economic Studies."), [])


class TestPrepare(unittest.TestCase):
    def test_the_real_case_end_to_end(self):
        p = pd.prepare(GLUED, WINDOW)
        self.assertTrue(p["split"])
        self.assertTrue(p["completed"])
        self.assertFalse(p["cut_off"])
        self.assertEqual(len(p["parts"]), 2)

    def test_primary_text_is_the_first_real_sentence(self):
        self.assertTrue(pd.primary_text(GLUED, WINDOW).endswith("(HCC)."))


def _without_details_only(page):
    """The page with every <div class="adv"> block removed — what a reader sees
    in the viewer's default simple mode before opening a card."""
    out, i = [], 0
    open_tag = '<div class="adv">'
    while True:
        j = page.find(open_tag, i)
        if j < 0:
            out.append(page[i:])
            return "".join(out)
        out.append(page[i:j])
        depth, k = 1, j + len(open_tag)
        while depth and k < len(page):
            nd, cd = page.find("<div", k), page.find("</div>", k)
            if cd < 0:
                k = len(page)
                break
            if 0 <= nd < cd:
                depth += 1
                k = nd + 4
            else:
                depth -= 1
                k = cd + 6
        i = k


def _analysis(evidence, covering=None, verdict="supported"):
    c = {"id": "t1", "text": "Patients are vulnerable due to immune dysfunction.",
         "markers": ["a"], "paper_ids": ["p1"], "verdict": verdict, "method": "llm",
         "reason": "ok", "evidence": evidence, "evidences": [evidence]}
    if covering:
        c["covering"] = covering
        c["covering_checked"] = True
    return {"text_claims": [c],
            "sources": [{"paper_id": "p1", "key": "a", "filename": "a.txt",
                         "title": "Alpha"}],
            "coverage": {"totals": {"claims": 1, "supported": 1, "unsupported": 0,
                                    "own": 0, "omitted": 0}},
            "metadata": {"output_dir": "/tmp/runs/t4"}, "omitted": []}


def _page(analysis, v2=False):
    import tempfile
    mod = viewer_v2 if v2 else viewer
    with tempfile.TemporaryDirectory() as d:
        out = os.path.join(d, "v.html")
        mod.generate(analysis, out, title="t", source_texts={})
        with open(out, encoding="utf-8") as f:
            return f.read()


class TestViewerRendersHonestly(unittest.TestCase):
    def setUp(self):
        self.ev = {"paper_id": "p1", "source_title": "Alpha", "supported": True,
                   "sentence": GLUED, "page": 1, "snippet": "Cirrhosis leads",
                   "cosine": 0.89, "reason": "ok", "window": WINDOW}

    def test_v1_quotes_the_two_sentences_separately_and_says_so(self):
        page = _page(_analysis(self.ev))
        self.assertIn("2 separate sentences from different places", page)
        self.assertIn("hepatocellular carcinoma (HCC).</blockquote>", page)

    def test_v2_inherits_the_same_rendering(self):
        page = _page(_analysis(self.ev), v2=True)
        self.assertIn("2 separate sentences from different places", page)

    def test_the_copy_button_never_carries_the_glued_string(self):
        page = _page(_analysis(self.ev))
        self.assertNotIn('data-quote="Cirrhosis leads to 2 million deaths '
                         'per year through '
                         'hepatic decompensation '
                         'and hepatocellular carcinoma '
                         '(HCC).2 , 3', page)

    def test_a_clean_quote_gets_no_warning(self):
        ev = dict(self.ev, sentence="The bridge was rebuilt in 1892.", window=None)
        page = _page(_analysis(ev))
        self.assertNotIn("separate sentences from different places", page)
        self.assertNotIn("stops mid-sentence", page)

    def test_a_quote_still_cut_off_is_labelled(self):
        ev = dict(self.ev, sentence="leading to concerns that these", window=None)
        page = _page(_analysis(ev))
        self.assertIn("stops mid-sentence", page)

    def test_v1_shows_the_proving_sentences_outside_the_details_button(self):
        # Simple mode — the default view — hides everything inside a
        # <div class="adv"> behind the card's details button. The proving
        # sentences must sit outside those blocks.
        cov = {"covered": [{"component": "vulnerability is due to immune dysfunction",
                            "paper_id": "p1", "source_title": "Alpha",
                            "sentence": "Immune dysregulation makes these patients "
                                        "more vulnerable to severe infection.",
                            "page": 2, "snippet": "Immune"}],
               "uncovered": [], "common_knowledge": []}
        page = _page(_analysis(self.ev, cov))
        self.assertIn("Immune dysregulation makes these patients", page)
        self.assertIn("Immune dysregulation makes these patients",
                      _without_details_only(page),
                      "the proving sentence is hidden behind the details button")


if __name__ == "__main__":
    unittest.main()
