"""Finding (i): download_sources.py prepends a 'Source URL: <url>\\n\\n---\\n\\n'
header to fetched .txt sources; that metadata block must never reach the sentence
index / evidence path. Stripping happens at the single read boundary
(source_decomposer.read_source_pages) so all disk caches (keyed on raw file BYTES)
stay valid."""

import os
import tempfile
import unittest

from modules.papertrail import source_decomposer as sd


class StripTxtPreamble(unittest.TestCase):
    def test_strips_exact_downloader_format(self):
        # matches direct_downloader.py: f"Source URL: {url}\n\n---\n\n" + text
        raw = "Source URL: https://example.org/paper\n\n---\n\nThe real first sentence."
        self.assertEqual(sd._strip_txt_preamble(raw), "The real first sentence.")

    def test_only_leading_block_stripped(self):
        # a later '---' or a mid-document 'Source URL:' mention must survive
        raw = ("Source URL: https://a.b/c\n\n---\n\nBody one.\n"
               "See also Source URL: https://x.y/z in the discussion.\n---\nBody two.")
        out = sd._strip_txt_preamble(raw)
        self.assertTrue(out.startswith("Body one."))
        self.assertIn("Source URL: https://x.y/z", out)
        self.assertIn("Body two.", out)

    def test_no_preamble_is_untouched(self):
        raw = "A plain source with no header at all.\nSecond line."
        self.assertEqual(sd._strip_txt_preamble(raw), raw)

    def test_read_source_pages_strips_txt(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "src.txt")
            with open(p, "w", encoding="utf-8") as f:
                f.write("Source URL: https://example.org/x\n\n---\n\nActual content here.")
            pages = sd.read_source_pages(p)
            self.assertEqual(pages, ["Actual content here."])

    def test_read_source_pages_pdf_path_untouched(self):
        # non-.txt files never go through the strip; a missing pdf just returns []
        self.assertEqual(sd.read_source_pages("/no/such/file.pdf"), [])


if __name__ == "__main__":
    unittest.main()
