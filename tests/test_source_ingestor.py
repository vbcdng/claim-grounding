"""Unit tests for the inbox ingestor — pure local file handling, no network.

Run:  venv/bin/python3 -m unittest tests.test_source_ingestor -v
"""

import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.papertrail import source_ingestor as si


ENTRIES = [
    {"key": "bostrom2014", "title": "Superintelligence: Paths, Dangers, Strategies",
     "doi": None},
    {"key": "hackenburg2024", "title": "Evaluating the persuasive influence of political microtargeting with large language models",
     "doi": "10.1073/pnas.2403116121"},
    {"key": "sen1981", "title": "Poverty and Famines: An Essay on Entitlement and Deprivation",
     "doi": None},
]


def touch(dirpath, name, content="x" * 2000):
    path = os.path.join(dirpath, name)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path


class TestMatching(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def test_filename_is_key(self):
        p = touch(self.dir, "bostrom2014.pdf")
        entry, how = si.match_file(p, ENTRIES)
        self.assertEqual(entry["key"], "bostrom2014")
        self.assertIn("key", how)

    def test_key_inside_messy_filename(self):
        p = touch(self.dir, "sen1981 (1) copy.txt")
        entry, how = si.match_file(p, ENTRIES)
        self.assertEqual(entry["key"], "sen1981")

    def test_doi_inside_file(self):
        p = touch(self.dir, "pnas.2403116121.txt",
                  "Some paper text... doi: 10.1073/pnas.2403116121 more text")
        entry, how = si.match_file(p, ENTRIES)
        self.assertEqual(entry["key"], "hackenburg2024")
        self.assertIn("DOI", how)

    def test_title_matches_filename(self):
        p = touch(self.dir, "Poverty and Famines - An Essay on Entitlement and Deprivation.txt")
        entry, how = si.match_file(p, ENTRIES)
        self.assertEqual(entry["key"], "sen1981")

    def test_title_inside_file(self):
        p = touch(self.dir, "download (3).txt",
                  "SUPERINTELLIGENCE\nPaths, Dangers, Strategies\nNick Bostrom\n" + "x " * 500)
        entry, how = si.match_file(p, ENTRIES)
        self.assertEqual(entry["key"], "bostrom2014")

    def test_unknown_file_not_guessed(self):
        p = touch(self.dir, "grocery list.txt", "milk eggs bread " * 100)
        entry, note = si.match_file(p, ENTRIES)
        self.assertIsNone(entry)
        self.assertIn("no confident match", note)

    def test_generic_journal_title_never_matches_content(self):
        # the journal-name-as-title bib bug: "International Security" appears in
        # countless unrelated documents and must not match via content or filename
        entries = ENTRIES + [{"key": "farrell2019", "title": "International Security",
                              "doi": None}]
        p = touch(self.dir, "Assessing_emerging_technologies.txt",
                  "arms control and international security studies " * 50)
        entry, _ = si.match_file(p, entries)
        self.assertIsNone(entry)
        p2 = touch(self.dir, "international security.txt", "whatever " * 100)
        entry2, _ = si.match_file(p2, entries)
        self.assertIsNone(entry2)
        # but a key-named file still works for that entry
        p3 = touch(self.dir, "farrell2019.pdf")
        entry3, _ = si.match_file(p3, entries)
        self.assertEqual(entry3["key"], "farrell2019")


class TestPlanIngest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def test_two_files_matching_same_key_block_each_other(self):
        p1 = touch(self.dir, "bostrom2014.pdf")
        p2 = touch(self.dir, "bostrom2014 (1).pdf")
        to_ingest, blocked, unmatched = si.plan_ingest([p1, p2], ENTRIES,
                                                       has_file=lambda k: False)
        self.assertEqual(to_ingest, [])
        self.assertEqual(len(blocked), 2)
        self.assertIn("2 inbox files match this key", blocked[0][2])

    def test_existing_source_blocks_title_match_but_not_key_match(self):
        p_title = touch(self.dir, "Poverty and Famines - An Essay on Entitlement and Deprivation.txt")
        p_key = touch(self.dir, "bostrom2014.pdf")
        to_ingest, blocked, _ = si.plan_ingest([p_title, p_key], ENTRIES,
                                               has_file=lambda k: True)
        self.assertEqual([e["key"] for _, e, _ in to_ingest], ["bostrom2014"])
        self.assertEqual([b[1] for b in blocked], ["sen1981"])


class TestIngest(unittest.TestCase):
    def setUp(self):
        self.inbox = tempfile.mkdtemp()
        self.sources = tempfile.mkdtemp()

    def test_txt_moved_and_renamed(self):
        p = touch(self.inbox, "whatever.txt", "full source text " * 200)
        filename, warning = si.ingest_file(p, "sen1981", self.sources)
        self.assertEqual(filename, "sen1981.txt")
        self.assertTrue(os.path.exists(os.path.join(self.sources, "sen1981.txt")))
        self.assertFalse(os.path.exists(p))

    def test_html_converted_to_text(self):
        body = "<p>" + "real content words here. " * 300 + "</p>"
        p = touch(self.inbox, "page.html", f"<html><body><main>{body}</main></body></html>")
        filename, warning = si.ingest_file(p, "bostrom2014", self.sources)
        self.assertEqual(filename, "bostrom2014.txt")
        saved = open(os.path.join(self.sources, "bostrom2014.txt")).read()
        self.assertIn("real content words", saved)
        self.assertNotIn("<p>", saved)
        self.assertIsNone(warning)

    def test_pdf_replaces_stale_txt(self):
        touch(self.sources, "sen1981.txt", "old thin text")
        p = touch(self.inbox, "sen1981.pdf", "%PDF fake")
        with patch.object(si, "pdf_has_text", return_value=True):
            filename, _ = si.ingest_file(p, "sen1981", self.sources)
        self.assertEqual(filename, "sen1981.pdf")
        self.assertFalse(os.path.exists(os.path.join(self.sources, "sen1981.txt")))

    def test_copy_keeps_original(self):
        p = touch(self.inbox, "whatever.txt", "full source text " * 200)
        filename, _ = si.ingest_file(p, "sen1981", self.sources, copy=True)
        self.assertEqual(filename, "sen1981.txt")
        self.assertTrue(os.path.exists(os.path.join(self.sources, "sen1981.txt")))
        self.assertTrue(os.path.exists(p))   # original stays put

    def test_dry_run_touches_nothing(self):
        p = touch(self.inbox, "whatever.txt")
        filename, _ = si.ingest_file(p, "sen1981", self.sources, dry_run=True)
        self.assertEqual(filename, "sen1981.txt")
        self.assertTrue(os.path.exists(p))
        self.assertFalse(os.path.exists(os.path.join(self.sources, "sen1981.txt")))

    def test_scan_inbox_filters_extensions(self):
        touch(self.inbox, "a.pdf"); touch(self.inbox, "b.html")
        touch(self.inbox, "notes.docx"); touch(self.inbox, "c.txt")
        names = [os.path.basename(p) for p in si.scan_inbox(self.inbox)]
        self.assertEqual(names, ["a.pdf", "b.html", "c.txt"])


if __name__ == "__main__":
    unittest.main()
