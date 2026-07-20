"""Sentence-segmentation guards (blob split + fragment merge) — no API calls.

These encode the paper1 audit's two segmentation failure families:
- t31: anthropic2025 (policy page, unpunctuated bullet lines) collapsed into one
  4,000+ char "sentence" once whitespace was flattened;
- t6: epochai2025's ranked table split into 2-3 word rows the judge can't use.

Run:  venv/bin/python3 -m unittest tests.test_segmentation -v
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.papertrail.source_decomposer import (
    sentence_split, _hard_wrap, _MAX_SENT_CHARS)


class TestNormalProse(unittest.TestCase):

    def test_plain_sentences_unchanged(self):
        text = "First sentence. Second sentence! Third one?"
        self.assertEqual(sentence_split(text),
                         ["First sentence.", "Second sentence!", "Third one?"])

    def test_wrapped_prose_stays_joined(self):
        # PDF-style soft wraps must NOT become sentence boundaries.
        text = "This sentence is wrapped\nacross three physical\nlines in the file."
        self.assertEqual(sentence_split(text),
                         ["This sentence is wrapped across three physical lines in the file."])

    def test_empty(self):
        self.assertEqual(sentence_split(""), [])
        self.assertEqual(sentence_split("  \n \n "), [])


class TestBlobGuard(unittest.TestCase):
    """Unpunctuated bullet lines (the t31 family)."""

    def test_bullet_lines_split_at_newlines(self):
        lines = [f"Do not use the service to perform prohibited activity number "
                 f"{i} of the policy list" for i in range(40)]
        sents = sentence_split("\n".join(lines))
        self.assertTrue(all(len(s) <= _MAX_SENT_CHARS for s in sents))
        # each policy line survives as its own retrievable sentence
        self.assertIn("Do not use the service to perform prohibited activity "
                      "number 7 of the policy list", sents)

    def test_giant_single_line_hard_wrapped(self):
        # one line, no punctuation, no newlines -> fixed-size wrap, never a blob
        text = "word " * 500
        sents = sentence_split(text)
        self.assertTrue(all(len(s) <= _MAX_SENT_CHARS for s in sents))
        self.assertGreater(len(sents), 1)

    def test_spaceless_pdf_text_never_exceeds_cap(self):
        # broken PDF text layers concatenate words; a single "word" can be huge
        text = ("Wewillsoonliveintheintelligenceage" * 40 + "\n") * 3
        sents = sentence_split(text)
        self.assertTrue(all(len(s) <= _MAX_SENT_CHARS for s in sents))

    def test_hard_wrap_giant_word(self):
        pieces = _hard_wrap("x" * 1000, target=300)
        self.assertTrue(all(len(p) <= 300 for p in pieces))
        self.assertEqual("".join(pieces), "x" * 1000)


class TestFragmentMerge(unittest.TestCase):
    """Tiny numbered table rows (the t6 family)."""

    TABLE = ("The chart shows the relative compute share over time of the top "
             "five countries. These top five are currently: 1. USA, 74.5% 2. "
             "China, 14.1% 3. EU, 4.8% 4. Norway, 1.8% 5. Japan holds the rest "
             "of the measured share according to the dataset.")

    def test_table_rows_merge_into_one_block(self):
        sents = sentence_split(self.TABLE)
        table = [s for s in sents if "74.5" in s]
        self.assertEqual(len(table), 1)
        # the whole comparison lands in ONE sentence the judge can read
        for needle in ("USA, 74.5%", "China, 14.1%", "EU, 4.8%"):
            self.assertIn(needle, table[0])

    def test_lone_fragment_kept_as_is(self):
        text = ("A normal opening sentence with plenty of words in it. Fig. "
                "Another normal closing sentence with plenty of words in it.")
        sents = sentence_split(text)
        self.assertIn("Fig.", sents)   # isolated fragment untouched (±1 window handles it)

    def test_spaceless_chunks_are_not_fragments(self):
        # few spaces => low word count, but long chars: must NOT be glued together
        chunks = ["Averyverylongspacelesschunkofpdftext" + str(i) * 30 for i in range(5)]
        sents = sentence_split(". ".join(chunks) + ".")
        self.assertTrue(all(len(s) <= _MAX_SENT_CHARS for s in sents))

    def test_short_numeric_sentences_are_not_fragments(self):
        # Complete short sentences ending in a YEAR (or a decimal) are genuine
        # sentences, not ranked-list artifacts — merging them would blur exactly
        # the numeric claims the pipeline cares most about (2026-07-06 review).
        from modules.papertrail.source_decomposer import _is_fragment
        self.assertFalse(_is_fragment("Sales fell in 2020."))
        self.assertFalse(_is_fragment("The rate fell by 4.5."))
        # ...while a true ranked-row artifact (trailing standalone list number)
        # is still caught:
        self.assertTrue(_is_fragment("USA, 74.5% 2."))
        sents = sentence_split("Sales fell in 2020. Profits rose in 2021.")
        self.assertIn("Sales fell in 2020.", sents)
        self.assertIn("Profits rose in 2021.", sents)


if __name__ == "__main__":
    unittest.main()
