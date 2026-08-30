#!/usr/bin/env python3
"""Guard the settlement-row reading page (task #30, step 4).

The page is a reading aid, so most of it cannot be tested. What these checks
pin is that it cannot quietly show the wrong thing:

  * a row id that is not in the frozen settlement list is refused outright,
    rather than producing an empty page the author would read as "no problem";
  * how many articles the original paper cited is read off the recorded
    citation markers, not guessed — that number is the whole reason
    `pilot100:cidev0017` is the row being read;
  * every answer the second checker can give is translated into "dropped the
    warning" or "kept the warning", so an unmapped answer cannot silently print
    as neutral text;
  * the sentence under test is marked inside its paragraph even though the
    stored version of it carries citation markers the paragraph does not;
  * the built page really contains the answer key, the tool, all six checkers,
    both blind readers and the full source, since a missing column changes what
    the author concludes;
  * both papers are linked by their recorded PubMed Central number and never by
    a guessed one — the author asked for the originals so they can check a row
    before reading anyone's opinion of it (2026-08-06);
  * the reading order holds: sentence, papers, source, ruling, and only then
    everyone else's answers, folded shut. Putting the answers before the ruling
    box would defeat the point of reading it yourself.

Offline: no API, no network. The build test needs the frozen list and the
benchmark data, so it skips when `data/` is absent.
"""

import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "benchmarks"))

import ci_settlement_row_page as P  # noqa: E402

ROW = "pilot100:cidev0017"
HAVE_DATA = (os.path.exists(P.SETTLEMENT_JSON) and
             os.path.exists(P.BLIND_READERS) and
             os.path.exists(P.GROUND_TRUTH["pilot100"]))


class TestCitationCount(unittest.TestCase):

    def test_a_span_with_one_marker_is_one_article(self):
        n, note = P.citation_count("the impact decreased [<|cit|>]")
        self.assertEqual(n, 1)
        self.assertIn("one article", note)

    def test_markers_in_the_same_bracket_are_counted(self):
        n, note = P.citation_count("x [<|cit|>,<|multi_cit|>]")
        self.assertEqual(n, 2)
        self.assertIn("only one of them", note)

    def test_markers_elsewhere_in_the_sentence_are_counted(self):
        n, _ = P.citation_count("a [<|other_cit|>] and b [<|cit|>,<|multi_cit|>]")
        self.assertEqual(n, 3)


class TestPaperLinks(unittest.TestCase):

    def test_a_bare_pmc_id_becomes_an_address(self):
        self.assertEqual(P.pmc_link("PMC9224599"),
                         "https://pmc.ncbi.nlm.nih.gov/articles/PMC9224599/")

    def test_the_datasets_own_numbering_is_stripped(self):
        """Cited papers are recorded as `024_PMC7588823`, citing papers bare."""
        self.assertEqual(P.pmc_link("024_PMC7588823"),
                         "https://pmc.ncbi.nlm.nih.gov/articles/PMC7588823/")

    def test_an_id_with_no_pmc_number_gets_no_link(self):
        """Better a stated gap than an address that goes to the wrong paper."""
        self.assertIsNone(P.pmc_link("some_other_ref"))
        self.assertIsNone(P.pmc_link(None))
        self.assertIn("no address recorded", P.link_html(None, "some_other_ref"))

    def test_the_cited_papers_title_is_its_first_line(self):
        self.assertEqual(P.first_line("\n\nA Title\n\n## Abstract\n"), "A Title")
        self.assertEqual(P.first_line(""), "")


class TestActionWording(unittest.TestCase):

    def test_every_action_says_dropped_or_kept(self):
        for action, (verb, _) in P.ACTIONS.items():
            self.assertIn(verb, ("dropped the warning", "kept the warning"),
                          action)

    def test_the_two_settling_actions_are_the_ones_that_drop(self):
        """These two are exactly what `_adjudicated_bucket` treats as a
        settlement; if that ever changes, this page would mislabel a row."""
        dropped = {a for a, (verb, _) in P.ACTIONS.items()
                   if verb == "dropped the warning"}
        self.assertEqual(dropped,
                         {"supported", "wrong_or_insufficient_evidence"})


class TestParagraphMarking(unittest.TestCase):

    def test_the_span_is_marked_even_though_it_carries_a_marker(self):
        para = ("Before it. The psychological impact of the exposure "
                "decreased in T2 compared with that in T1 [<|cit|>]. After it.")
        span = ("The psychological impact of the exposure decreased in T2 "
                "compared with that in T1 [<|cit|>]")
        out = P.paragraph_html(para, span)
        self.assertIn("<mark>", out)
        self.assertIn("psychological impact", out.split("<mark>")[1])
        self.assertIn("After it.", out.split("</mark>")[1])

    def test_an_unfindable_span_still_prints_the_paragraph(self):
        out = P.paragraph_html("A paragraph that shares nothing.", "x" * 80)
        self.assertNotIn("<mark>", out)
        self.assertIn("A paragraph that shares nothing.", out)


@unittest.skipUnless(HAVE_DATA, "benchmark data not present")
class TestBuild(unittest.TestCase):

    def _build(self, rows, out):
        return subprocess.run(
            [sys.executable,
             os.path.join(ROOT, "benchmarks", "ci_settlement_row_page.py"),
             "--rows", rows, "--out", out],
            capture_output=True, text=True)

    def test_a_row_outside_the_frozen_list_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "x.html")
            r = self._build("pilot100:cidev0001", out)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("not a settlement row", r.stderr + r.stdout)
            self.assertFalse(os.path.exists(out))

    def test_the_page_carries_every_column_the_author_needs(self):
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "x.html")
            r = self._build(ROW, out)
            self.assertEqual(r.returncode, 0, r.stderr)
            with open(out, encoding="utf-8") as f:
                page = f.read()
            for needed in ("NOT_SUBSTANTIATE",          # the answer key
                           "What the tool said",
                           "What the built-in second checker said",
                           "DeepSeek", "OpenAI", "Alibaba", "Moonshot",
                           "Anthropic",                 # all five companies
                           "Sonnet", "Opus",            # both blind readers
                           "The cited paper, as the tool read it",
                           "Words used on this page",
                           # both papers, by their recorded PMC numbers
                           "https://pmc.ncbi.nlm.nih.gov/articles/PMC7588823/",
                           "https://pmc.ncbi.nlm.nih.gov/articles/PMC9224599/"):
                self.assertIn(needed, page, needed)

    def test_the_reading_order_puts_the_source_and_the_ruling_first(self):
        """The author asked to check a row themselves before seeing anyone's
        judgement of it, so the paper and the ruling box come before the
        answers, and the answers stay folded shut."""
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "x.html")
            self._build(ROW, out)
            with open(out, encoding="utf-8") as f:
                page = f.read()
            order = [page.index(s) for s in (
                "1. The sentence being checked",
                "2. Both papers, as published",
                "3. The cited paper, as the tool read it",
                "4. Your ruling",
                "5. What everyone else said")]
            self.assertEqual(order, sorted(order))
            self.assertLess(page.index("4. Your ruling"),
                            page.index("NOT_SUBSTANTIATE</b>"))
            # folded, and not forced open
            self.assertIn("<details class=others>", page)
            self.assertNotIn("<details class=others open", page)

    def test_the_row_is_shown_as_asking_a_fair_question(self):
        """`pilot100:cidev0017` is on the reading list precisely because it
        cites one article. If the page ever says otherwise, the reason for
        reading it has gone."""
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "x.html")
            self._build(ROW, out)
            with open(out, encoding="utf-8") as f:
                page = f.read()
            self.assertIn("The original paper cited</b> one article", page)


if __name__ == "__main__":
    unittest.main()
