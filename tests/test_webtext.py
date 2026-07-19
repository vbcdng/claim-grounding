"""Web-boilerplate filters (owner walkthrough 2026-07-07, todo item 8): bylines,
publish stamps, photo credits, and related-articles headline dumps must not
enter the sentence index — while real prose survives untouched. Shapes below
are taken verbatim from the paper1 sources that leaked (epochai2025,
aljazeera2026, disruptionbanking2026, agenceeurope2026). No network, no API.

Run:  venv/bin/python3 -m unittest tests.test_webtext -v
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.papertrail.webtext import drop_boilerplate_lines
from modules.papertrail import source_decomposer

PROSE = ("Artificial intelligence has become the latest issue to drive a wedge "
         "between the United States and its allies, citing national security concerns.")


class TestLineFilters(unittest.TestCase):

    def test_byline_is_dropped(self):
        text = ("By Konstantin F. Pilz, Robi Rahman, James Sanders, Luke Emberson, "
                "and Lennart Heim\n" + PROSE)
        out = drop_boilerplate_lines(text)
        self.assertNotIn("Pilz", out)
        self.assertIn(PROSE, out)

    def test_prose_starting_with_by_survives(self):
        line = "By 2030, electricity demand more than doubles in the reference scenario."
        self.assertIn(line, drop_boilerplate_lines(line))

    def test_publish_stamp_and_bare_dates_dropped(self):
        text = "Published On 19 Jun 2026\n19 Jun 2026\nMay 1, 2026\nJun. 5, 2025\n" + PROSE
        out = drop_boilerplate_lines(text)
        self.assertEqual(out.strip(), PROSE)

    def test_photo_credit_caption_dropped(self):
        cap = ("Pages from the Anthropic website are displayed on a computer screen "
               "in New York on February 26, 2026 [Patrick Sison/AP Photo]")
        out = drop_boilerplate_lines(cap + "\n" + PROSE)
        self.assertNotIn("Patrick Sison", out)
        self.assertIn(PROSE, out)

    def test_site_chrome_dropped_but_long_subscribe_sentence_kept(self):
        keep = ("Subscribe revenue at the paper grew twelve percent last year, "
                "the annual report says, outpacing advertising for the first time.")
        text = "Skip to content\nPlease log in\n" + keep
        out = drop_boilerplate_lines(text)
        self.assertNotIn("Skip to content", out)
        self.assertNotIn("Please log in", out)
        self.assertIn(keep, out)

    def test_allcaps_section_header_dropped(self):
        out = drop_boilerplate_lines("SECTORAL POLICIES /\n" + PROSE)
        self.assertEqual(out.strip(), PROSE)

    def test_numeric_table_rows_survive(self):
        # audit t6: "EU, 4.8%"-style rows ARE evidence; "1. USA, 74.5%" is
        # uppercase+digits and must not be eaten by the section-header rule
        rows = "1. USA, 74.5%\n2. China, 15.1%\n3. EU, 4.8%"
        self.assertEqual(drop_boilerplate_lines(rows), rows)

    def test_headline_dump_run_dropped_but_short_run_kept(self):
        dump = "\n".join([
            "European Parliament committee approves provisional agreement on regulation",
            "Commission launches public consultation on police biometric data sharing",
            "EU Member States fail to reach agreement on simplifying pesticide rules",
            "European Commission preparing stricter framework for forever chemicals",
            "MEPs to vote in plenary on amendments tabled by left-wing groups",
        ])
        out = drop_boilerplate_lines(dump + "\n" + PROSE)
        self.assertNotIn("pesticide", out)
        self.assertIn(PROSE, out)
        # two consecutive unpunctuated lines (real headings) survive
        short = "A Bold New Strategy for Europe\nWhy the Stakes Are Higher Now\n" + PROSE
        out2 = drop_boilerplate_lines(short)
        self.assertIn("Bold New Strategy", out2)

    def test_prose_paragraphs_untouched(self):
        text = PROSE + "\n\n" + ("Euro-area finance ministers will meet with banking "
                                 "supervisors on Monday, according to a senior EU official.")
        self.assertEqual(drop_boilerplate_lines(text), text)


class TestTxtReadIntegration(unittest.TestCase):

    def _read(self, content):
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False,
                                         encoding="utf-8") as f:
            f.write(content)
            path = f.name
        try:
            return source_decomposer.read_source_pages(path)[0]
        finally:
            os.unlink(path)

    def test_downloader_saved_txt_is_filtered(self):
        content = ("Source URL: https://example.com/a\n\n---\n\n"
                   "Published On 19 Jun 2026\n" + PROSE)
        out = self._read(content)
        self.assertNotIn("Published On", out)
        self.assertIn(PROSE, out)

    def test_hand_supplied_txt_untouched(self):
        content = "Published On 19 Jun 2026\n" + PROSE
        self.assertEqual(self._read(content), content)


class TestExtractPageText(unittest.TestCase):

    def test_link_dense_block_and_chrome_classes_stripped(self):
        from bs4 import BeautifulSoup
        from modules.papertrail.direct_downloader import extract_page_text
        headlines = "".join(f'<li><a href="/{i}">Some unrelated headline number '
                            f'{i} about another topic entirely</a></li>'
                            for i in range(8))
        html = f"""
        <html><body><div id="content">
          <p>{PROSE} The ministers agreed to meet again in September to review
          progress on the framework, officials said on Monday afternoon.</p>
          <div class="related-posts"><ul>{headlines}</ul></div>
          <ul>{headlines}</ul>
        </div></body></html>"""
        out = extract_page_text(BeautifulSoup(html, "html.parser"))
        self.assertIn("drive a wedge", out)
        self.assertNotIn("unrelated headline", out)


if __name__ == "__main__":
    unittest.main()
