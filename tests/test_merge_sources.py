"""Merge mode of the Claude Science importer: a follow-up report's bibliography
is merged into an EXISTING project (dedupe by DOI/title, key-collision rename,
refs appended, manifest updated). Pure parsing, no API calls.

Run:  venv/bin/python3 -m unittest tests.test_merge_sources -v
"""
import os
import sys
import json
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.papertrail import claude_research_importer as imp

BIB = r"""
@article{smith2020,
  title = {Alpha Study of Bridges},
  author = {Smith, Jane},
  year = {2020},
  doi = {10.1000/alpha},
}
@report{jones2021,
  title = {The Beta Report on Global Diffusion},
  author = {Jones, Bob},
  year = {2021},
  url = {https://example.org/beta},
}
@article{brown2022,
  title = {A Genuinely New Gamma Paper},
  author = {Brown, Ann},
  year = {2022},
  doi = {10.1000/gamma},
}
@article{lee2023,
  title = {Delta Without Any Link},
  author = {Lee, Kim},
  year = {2023},
}
"""

REPORT = """---
bibliography: found.bib
---
New sources were found [@brown2022] and [@lee2023]; also [@smith2020; @jones2021].
"""


class TestMergeSources(unittest.TestCase):

    def setUp(self):
        self.proj = tempfile.mkdtemp()
        # existing project: smith2020 present under ANOTHER key (same DOI),
        # Beta Report present under another key (same title), and the key
        # "brown2022" already used by a DIFFERENT work.
        manifest = {"sources": [
            {"key": "smith_alpha", "title": "Alpha study of bridges!", "author": "Smith",
             "year": "2020", "url": None, "doi": "10.1000/alpha",
             "suggested_filename": "smith_alpha.pdf", "status": "has_link"},
            {"key": "beta_rep", "title": "The Beta Report on Global Diffusion", "author": "Jones",
             "year": "2021", "url": "https://example.org/beta", "doi": None,
             "suggested_filename": "beta_rep.pdf", "status": "has_link"},
            {"key": "brown2022", "title": "An Unrelated Old Paper", "author": "Other",
             "year": "2019", "url": None, "doi": "10.9/other",
             "suggested_filename": "brown2022.pdf", "status": "has_link"},
        ]}
        with open(os.path.join(self.proj, "sources_manifest.json"), "w") as f:
            json.dump(manifest, f)
        with open(os.path.join(self.proj, "my_text.md.refs.txt"), "w") as f:
            f.write("# existing refs\nsmith_alpha = smith_alpha.pdf\n"
                    "beta_rep = beta_rep.txt\nbrown2022 = brown2022.pdf\n")
        # the export: report md + its bib next to it
        self.exp = tempfile.mkdtemp()
        with open(os.path.join(self.exp, "found.bib"), "w") as f:
            f.write(BIB)
        self.report = os.path.join(self.exp, "report.md")
        with open(self.report, "w") as f:
            f.write(REPORT)

    def _merge(self, input_path=None):
        return imp.merge_sources(input_path or self.report, self.proj)

    def test_duplicates_skipped_by_doi_and_url(self):
        s = self._merge()
        skipped = {d["key"]: d for d in s["skipped"]}
        self.assertEqual(skipped["smith2020"]["existing_key"], "smith_alpha")
        self.assertEqual(skipped["smith2020"]["why"], "same DOI")
        self.assertEqual(skipped["jones2021"]["existing_key"], "beta_rep")
        self.assertEqual(skipped["jones2021"]["why"], "same URL")

    def test_key_collision_with_different_work_gets_suffix(self):
        s = self._merge()
        self.assertIn({"from": "brown2022", "to": "brown20222"}, s["renamed"])
        self.assertIn("brown20222", s["added"])

    def test_new_sources_added_to_manifest_and_refs_appended(self):
        s = self._merge()
        self.assertIn("lee2023", s["added"])
        self.assertEqual(s["needs_search"], ["lee2023"])       # no url/DOI
        manifest = json.load(open(os.path.join(self.proj, "sources_manifest.json")))
        keys = [x["key"] for x in manifest["sources"]]
        self.assertIn("lee2023", keys)
        self.assertIn("brown20222", keys)
        self.assertEqual(manifest["merges"][0]["added_keys"], s["added"])
        refs = open(os.path.join(self.proj, "my_text.md.refs.txt")).read()
        self.assertIn("beta_rep = beta_rep.txt", refs)         # existing lines untouched
        self.assertIn("lee2023 = lee2023.pdf", refs)
        self.assertIn("brown20222 = brown20222.pdf", refs)

    def test_bib_can_be_passed_directly(self):
        s = self._merge(os.path.join(self.exp, "found.bib"))
        self.assertIn("lee2023", s["added"])

    def test_subtitle_variant_and_same_url_are_duplicates(self):
        extra = os.path.join(self.exp, "extra.bib")
        with open(extra, "w") as f:
            f.write(r"""
@report{jones_beta_v2,
  title = {The Beta Report on Global Diffusion: A Longer Subtitle Edition},
  author = {Jones, Bob}, year = {2021},
}
@misc{beta_by_url,
  title = {Completely Different Words Here},
  url = {HTTPS://WWW.example.org/beta/},
}
""")
        s = self._merge(extra)
        skipped = {d["key"]: d for d in s["skipped"]}
        self.assertEqual(skipped["jones_beta_v2"]["existing_key"], "beta_rep")
        self.assertEqual(skipped["jones_beta_v2"]["why"], "same title")
        self.assertEqual(skipped["beta_by_url"]["existing_key"], "beta_rep")
        self.assertEqual(skipped["beta_by_url"]["why"], "same URL")
        self.assertEqual(s["added"], [])

    def test_rejects_non_project_dir(self):
        with self.assertRaises(ValueError):
            imp.merge_sources(self.report, tempfile.mkdtemp())

    def test_missing_bibliography_is_an_error(self):
        bare = os.path.join(self.exp, "bare.md")
        with open(bare, "w") as f:
            f.write("No frontmatter, citations only [@x2020].\n")
        with self.assertRaises(ValueError):
            imp.merge_sources(bare, self.proj)


if __name__ == "__main__":
    unittest.main()
