"""Review pages for every problem row of the 2026-08-02 run (task #29).

What is worth pinning:

1. The two normalisers must keep exactly the same characters. They did not on
   the first build — one kept Greek letters, the other dropped them — and 13 of
   323 quotes were reported as "not in the paper" when they were in it verbatim.
   That is the worst possible failure for this page: it accuses a model of
   inventing a quote it copied faithfully.
2. Finding a quote must survive the mess a PDF extractor leaves (hyphenation
   across a line break, curly apostrophes, ligatures, lost spacing) and must
   still refuse text that really is not there.
3. A quote stitched together from two far-apart passages must be reported as
   two passages, because that display defect is itself one of the findings the
   page exists to show (follow-up item 7).
4. The citing paragraph must never render a citation as empty brackets: the
   benchmark's tokens are spelled out, so "(  )" cannot be read as "the paper
   cited nothing here".
5. The page must refuse to build if the tool column disagrees with the run's
   own analysis.json — silently showing a verdict the run did not give would
   put a wrong number in front of the author.

Offline: no API calls, no network.
"""
import json
import os
import re
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "benchmarks"))
import ci_review_pages as rp  # noqa: E402


class TestNormalisersAgree(unittest.TestCase):
    """The offset-mapped normaliser and the plain one must keep the same chars."""

    SAMPLES = [
        "PGC1α stimulates Fndc5 expression",
        "TH17 cells and IL-17β in the synovial space",
        "ﬁbroblast growth factor 21 (FGF21)",
        "cells were treated with 5 µM MG132 for 6 h",
        "the protein’s N-terminal part — released into blood",
        "SARS-CoV-2 §3.1 [12,13]",
    ]

    def test_same_characters_survive_both_paths(self):
        for s in self.SAMPLES:
            mapped, offsets = rp._norm_map(s)
            self.assertEqual(mapped, rp._norm(s), "normalisers differ on %r" % s)
            self.assertEqual(len(mapped), len(offsets))

    def test_offsets_point_at_the_right_original_characters(self):
        s = "PGC1α might stimulate the secretion"
        mapped, offsets = rp._norm_map(s)
        for i, ch in enumerate(mapped):
            self.assertEqual(rp._fold(s)[offsets[i]].lower(), ch)


class TestLocate(unittest.TestCase):
    SOURCE = (
        "Introduction\n\nThis strongly suggests that PGC1α might stimu-\n"
        "late the secretion of factors from skeletal muscle that affects the "
        "function of other tissues. In this paper, we show that PGC1α "
        "stimulates the expression of several muscle gene-products.\n\n"
        "Later in the discussion, irisin is induced with exercise in mice and "
        "humans, and mildly increased irisin levels in blood cause an increase "
        "in energy expenditure.\n"
    )

    def setUp(self):
        self.norm, self.offsets = rp._norm_map(self.SOURCE)

    def find(self, quote):
        return rp.locate(quote, self.norm, self.offsets, self.SOURCE)

    def test_finds_a_quote_broken_by_hyphenation_and_a_line_break(self):
        spans = self.find("This strongly suggests that PGC1α might "
                          "stimulate the secretion of factors")
        self.assertEqual(len(spans), 1)
        start, end = spans[0]
        self.assertTrue(self.SOURCE[start:end].startswith("This strongly"))
        self.assertTrue(self.SOURCE[start:end].endswith("of factors"))

    def test_finds_a_quote_whose_greek_letter_the_extractor_dropped(self):
        # Both sides lose the Greek letter when normalised, so a quote that
        # copied it faithfully still matches source text where the extractor
        # lost it. (A quote that spells it out as "PGC1alpha" would NOT match —
        # nothing observed does that, and inventing a match rule for it would
        # let genuinely different text through.)
        source = "we show that PGC1 stimulates the expression of Fndc5."
        norm, offsets = rp._norm_map(source)
        self.assertTrue(rp.locate("we show that PGC1α stimulates the expression "
                                  "of Fndc5", norm, offsets, source))

    def test_refuses_text_that_is_not_in_the_paper(self):
        self.assertEqual(self.find("Irisin cures type 2 diabetes in humans "
                                   "within four weeks of treatment"), [])

    def test_refuses_a_scrap_too_short_to_mean_anything(self):
        self.assertEqual(self.find("irisin"), [])

    def test_reports_a_stitched_quote_as_two_separate_passages(self):
        spans = self.find("This strongly suggests that PGC1α might "
                          "stimulate the secretion of factors ... irisin is "
                          "induced with exercise in mice and humans")
        self.assertEqual(len(spans), 2)
        self.assertLess(spans[0][1], spans[1][0])

    def test_the_note_says_so_when_a_quote_is_two_passages(self):
        body, note = rp.in_context(
            "This strongly suggests that PGC1α might stimulate the "
            "secretion of factors ... irisin is induced with exercise in mice "
            "and humans", self.norm, self.offsets, self.SOURCE)
        self.assertIn("2 separate passages", note)
        self.assertEqual(body.count("<mark>"), 2)

    def test_a_missing_quote_says_it_could_not_be_found(self):
        body, note = rp.in_context("Irisin cures type 2 diabetes in humans "
                                   "within four weeks", self.norm, self.offsets,
                                   self.SOURCE)
        self.assertIsNone(body)
        self.assertIn("could not be found", note)

    def test_highlighting_the_whole_paper_marks_every_quote_once(self):
        marked = rp.highlight_all(self.SOURCE, [
            "In this paper, we show that PGC1α stimulates the expression",
            "we show that PGC1α stimulates the expression of several",
            "irisin is induced with exercise in mice and humans",
        ])
        # The first two overlap and must merge into one <mark>.
        self.assertEqual(marked.count("<mark>"), 2)


class TestCitingParagraph(unittest.TestCase):
    PAR = ("Muscles express myokines (<|multi_cit|>). Others have been "
           "proposed, including IL-6 (<|other_cit|>).")
    SPAN = "Muscles express myokines (<|multi_cit|>)."

    def test_the_checked_part_is_highlighted_inside_its_paragraph(self):
        out, located = rp.paragraph_html(self.PAR, self.SPAN)
        self.assertTrue(located)
        self.assertIn("<mark>", out)
        self.assertIn("Others have been proposed", out)

    def test_no_citation_is_rendered_as_empty_brackets(self):
        out, _ = rp.paragraph_html(self.PAR, self.SPAN)
        self.assertNotIn("( )", out)
        self.assertNotIn("()", out)
        self.assertIn("the cited paper", out)
        self.assertIn("other references", out)

    def test_a_span_that_is_not_in_the_paragraph_is_reported_not_faked(self):
        out, located = rp.paragraph_html(self.PAR, "A sentence from elsewhere.")
        self.assertFalse(located)
        self.assertNotIn("<mark>", out)

    def test_the_citation_note_never_uses_the_word_span(self):
        # "span" was ruled out for anything the author reads (2026-07-25).
        for s in (self.SPAN, "plain <|cit|> only",
                  "shared <|multi_cit|> and <|other_cit|> elsewhere",
                  "alone <|cit|> but <|other_cit|> later"):
            self.assertNotIn("span", rp.citation_note(s).lower())


class TestBuildRefusesDrift(unittest.TestCase):
    """A tool column that does not match the run must stop the build."""

    def _write_fixture(self, tmp, run_verdict):
        batch = os.path.join(tmp, "batch")
        run = os.path.join(tmp, "run")
        os.makedirs(os.path.join(batch, "sources"))
        os.makedirs(run)
        with open(os.path.join(batch, "sources", "cidev0001.txt"), "w") as fh:
            fh.write("The paper says muscles secrete irisin into the blood.\n")
        json.dump({"claims": {"cidev0001": {
            "claim_text": "Muscles secrete irisin [[cidev0001]].",
            "annotated_span": "Muscles secrete irisin (<|cit|>).",
            "citing_paragraph": "Muscles secrete irisin (<|cit|>).",
            "span_words": 3, "span_is_full_sentence": True,
            "label": "ACCURATE", "split": "dev", "ref": "000_PMC111",
            "citing_pmcid": "PMC222",
            "evidence_segments": ["The paper says muscles secrete irisin "
                                  "into the blood."]}}},
                  open(os.path.join(batch, "ci_ground_truth.json"), "w"))
        json.dump({"text_claims": [{"id": "t1", "markers": ["cidev0001"],
                                    "verdict": run_verdict, "method": "llm",
                                    "reason": "because"}]},
                  open(os.path.join(run, "analysis.json"), "w"))
        spec = os.path.join(tmp, "spec.json")
        json.dump({"sets": [{"batch": batch, "run": run,
                             "run_name": "fixture", "readers": {}}]},
                  open(spec, "w"))
        dis = os.path.join(tmp, "dis.json")
        json.dump({"spec": spec,
                   "sets": [{"tag": "fix", "n": 1, "run_name": "fixture"}],
                   "readers": ["sonnet", "opus"],
                   "piles": {"A": ["fix:cidev0001"], "B": [], "C": [], "D": []},
                   "rows": {"fix:cidev0001": {
                       "qid": "fix:cidev0001", "tag": "fix", "cid": "cidev0001",
                       "label": "ACCURATE", "key": "pass", "tool": "pass",
                       "claim": "Muscles secrete irisin [[cidev0001]].",
                       "fair": True,
                       "readers": {"sonnet": {"side": "flag", "confidence": "low",
                                              "reason": "r", "quote": None},
                                   "opus": {"side": "flag", "confidence": "low",
                                            "reason": "r", "quote": None}}}}},
                  open(dis, "w"))
        return dis

    class _Opts:
        full_text = True
        date = "2026-08-03"
        questions = None

    def test_a_matching_run_builds_one_card(self):
        with tempfile.TemporaryDirectory() as tmp:
            dis = self._write_fixture(tmp, "supported")
            out = os.path.join(tmp, "page.html")
            n, _ = rp.build(dis, out, self._Opts())
            self.assertEqual(n, 1)
            page = open(out).read()
            self.assertEqual(page.count("class='card'"), 1)
            self.assertIn("Reading it yourself", page)
            # both real papers are linked, not only our extracted copy: the
            # author asked for the whole texts on 2026-08-03
            self.assertIn("https://pmc.ncbi.nlm.nih.gov/articles/PMC111/", page)
            self.assertIn("https://pmc.ncbi.nlm.nih.gov/articles/PMC222/", page)
            self.assertIn("the published page is the truth", page)
            # no shortlist passed, so no "start here" anything
            self.assertNotIn("start here", page)

    def test_a_shortlisted_row_gets_its_question_and_the_filter(self):
        with tempfile.TemporaryDirectory() as tmp:
            dis = self._write_fixture(tmp, "supported")
            out = os.path.join(tmp, "page.html")

            class Opts(self._Opts):
                questions = {"fix:cidev0001": "Is 12-50% of one group the same "
                                              "as 50% of a subgroup?"}
            rp.build(dis, out, Opts())
            page = open(out).read()
            self.assertIn("start here (1)", page)
            self.assertEqual(page.count("class='ask-box'"), 1)
            self.assertIn("50% of a subgroup", page)
            self.assertIn("class='card starred'", page)

    def test_a_run_that_says_something_else_stops_the_build(self):
        with tempfile.TemporaryDirectory() as tmp:
            dis = self._write_fixture(tmp, "unsupported")   # list said pass
            out = os.path.join(tmp, "page.html")
            with self.assertRaises(SystemExit) as caught:
                rp.build(dis, out, self._Opts())
            self.assertIn("does not match", str(caught.exception))


class TestBuiltPageIsWellFormed(unittest.TestCase):
    """Every tag the builder opens must be closed, or the browser silently
    swallows the rest of a card."""

    PAGE = os.path.join(ROOT, "data", "citation_integrity", "review_2026-08-03",
                        "problem_rows.html")

    @unittest.skipUnless(os.path.exists(PAGE), "the built page is not on disk")
    def test_tags_balance(self):
        body = open(self.PAGE, encoding="utf-8").read()
        body = re.sub(r"<script>.*?</script>", "", body, flags=re.S)
        body = re.sub(r"<style>.*?</style>", "", body, flags=re.S)
        void = {"meta", "input", "br", "hr", "img", "link", "!doctype"}
        stack = []
        for m in re.finditer(r"<(/?)([a-zA-Z!][a-zA-Z0-9-]*)[^>]*?(/?)>", body):
            closing, name, self_closing = m.group(1), m.group(2).lower(), m.group(3)
            if name in void or self_closing:
                continue
            if closing:
                self.assertTrue(stack, "closing </%s> with nothing open" % name)
                self.assertEqual(stack[-1], name,
                                 "</%s> closes <%s>" % (name, stack[-1]))
                stack.pop()
            else:
                stack.append(name)
        self.assertEqual(stack, [], "never closed: %s" % stack)


if __name__ == "__main__":
    unittest.main()
