"""Stream D — review-loop paper finder. Offline: injected search + download
fakes, no network, no real files fetched. Asserts the propose-only contract
(sources registered in manifest + refs; author text never touched)."""

import json
import os
import tempfile
import unittest

from modules.papertrail import review_paper_finder as rpf


def _review(*marks):
    return {"run": {"project_dir": "/x"}, "marks": list(marks)}


def _claim(cid, text, markers, marks):
    return {"id": cid, "text": text, "markers": markers, "marks": marks}


class TestReviewPaperFinder(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(self.dir, ignore_errors=True))
        self.refs = os.path.join(self.dir, "my_text.md.refs.txt")
        with open(self.refs, "w") as f:
            f.write("# key = filename\noldkey = oldkey.pdf\n")
        self.manifest = os.path.join(self.dir, "sources_manifest.json")
        with open(self.manifest, "w") as f:
            json.dump({"sources": [{"key": "oldkey", "title": "Old"}]}, f)

    def _search_fn(self, candidates):
        def fn(claim_text, keywords, llm=None, cache_dir=None):
            return {"status": "ok", "candidates": candidates}
        return fn

    def _download_ok(self, entry, sources_dir, session):
        # Simulate a successful OA fetch writing <key>.pdf.
        fn = f"{entry['key']}.pdf"
        open(os.path.join(sources_dir, fn), "w").close()
        return {"key": entry["key"], "outcome": "pdf", "filename": fn}

    def test_wrong_source_marks_filtering(self):
        r = _review(_claim("t1", "A", ["k"], ["wrong_source"]),
                    _claim("t2", "B", ["k"], ["rewrite"]),
                    _claim("t3", "C", ["k"], ["wrong_source", "rewrite"]))
        got = [m["id"] for m in rpf._wrong_source_marks(r)]
        self.assertEqual(got, ["t1", "t3"])

    def test_finds_downloads_and_registers(self):
        cands = [{"title": "Better Paper on X", "year": 2024, "doi": "10.1/x",
                  "url": "http://x", "relevance": 0.9, "reason": "close"}]
        report = rpf.find_replacements(
            _review(_claim("t1", "claim text", ["oldkey"], ["wrong_source"])),
            self.dir, search_fn=self._search_fn(cands),
            download_fn=self._download_ok, session="S")
        p = report["proposals"][0]
        self.assertEqual(p["claim_id"], "t1")
        self.assertTrue(p["suggested_key"])
        key = p["suggested_key"]
        # Registered in manifest...
        man = json.load(open(self.manifest))
        self.assertIn(key, [s["key"] for s in man["sources"]])
        # ...and in refs...
        refs = open(self.refs).read()
        self.assertIn(f"{key} = {key}.pdf", refs)
        # ...and the file exists.
        self.assertTrue(os.path.exists(os.path.join(self.dir, "sources", f"{key}.pdf")))
        # The old mapping is preserved.
        self.assertIn("oldkey = oldkey.pdf", refs)

    def test_refs_only_key_is_never_rebound(self):
        # 'climate2023' exists ONLY in the refs file (manual/ingested source, no
        # manifest entry). A candidate slugging to the same key must be
        # uniquified — rebinding would repoint every [[climate2023]] in the
        # author's text at a different paper.
        with open(self.refs, "a") as f:
            f.write("climate2023 = original_paper.pdf\n")
        cands = [{"title": "Climate impacts revisited", "year": 2023,
                  "doi": "10.9/z", "relevance": 0.9}]
        report = rpf.find_replacements(
            _review(_claim("t1", "c", ["oldkey"], ["wrong_source"])),
            self.dir, search_fn=self._search_fn(cands),
            download_fn=self._download_ok, session="S")
        key = report["proposals"][0]["suggested_key"]
        self.assertNotEqual(key, "climate2023")
        refs = open(self.refs).read()
        self.assertIn("climate2023 = original_paper.pdf", refs)   # untouched
        self.assertIn(f"{key} = {key}.pdf", refs)

    def test_dry_run_registers_nothing(self):
        cands = [{"title": "Paper", "year": 2024, "doi": "10.1/x", "relevance": 0.8}]
        report = rpf.find_replacements(
            _review(_claim("t1", "c", ["oldkey"], ["wrong_source"])),
            self.dir, download=False, search_fn=self._search_fn(cands))
        p = report["proposals"][0]
        self.assertIsNone(p["suggested_key"])          # nothing downloaded
        self.assertEqual(len(p["candidates"]), 1)      # but candidate surfaced
        man = json.load(open(self.manifest))
        self.assertEqual([s["key"] for s in man["sources"]], ["oldkey"])  # unchanged
        self.assertNotIn("Paper", open(self.refs).read())

    def test_candidates_without_link_skipped(self):
        cands = [{"title": "No link paper", "year": 2024},           # no doi/url -> skip
                 {"title": "Has doi", "year": 2023, "doi": "10.2/y", "relevance": 0.5}]
        report = rpf.find_replacements(
            _review(_claim("t1", "c", ["oldkey"], ["wrong_source"])),
            self.dir, search_fn=self._search_fn(cands),
            download_fn=self._download_ok, session="S")
        titles = [r["title"] for r in report["proposals"][0]["candidates"]]
        self.assertEqual(titles, ["Has doi"])

    def test_failed_download_not_registered(self):
        def dl_fail(entry, sources_dir, session):
            return {"key": entry["key"], "outcome": "not_fetchable", "filename": None}
        cands = [{"title": "Paywalled", "year": 2024, "doi": "10.3/z", "relevance": 0.7}]
        report = rpf.find_replacements(
            _review(_claim("t1", "c", ["oldkey"], ["wrong_source"])),
            self.dir, search_fn=self._search_fn(cands),
            download_fn=dl_fail, session="S")
        p = report["proposals"][0]
        self.assertIsNone(p["suggested_key"])
        self.assertFalse(p["candidates"][0]["downloaded"])
        man = json.load(open(self.manifest))
        self.assertEqual([s["key"] for s in man["sources"]], ["oldkey"])

    def test_download_exception_isolated(self):
        def dl_boom(entry, sources_dir, session):
            raise RuntimeError("network down")
        cands = [{"title": "Paper", "year": 2024, "doi": "10.4/a"}]
        report = rpf.find_replacements(
            _review(_claim("t1", "c", ["oldkey"], ["wrong_source"])),
            self.dir, search_fn=self._search_fn(cands),
            download_fn=dl_boom, session="S")
        self.assertIn("error", report["proposals"][0]["candidates"][0]["outcome"])

    def test_key_uniquification(self):
        taken = {"better2024"}
        k = rpf._slug_key("Better Paper", 2024, taken)
        self.assertEqual(k, "better2024_2")

    def test_render_report_is_propose_only(self):
        cands = [{"title": "Paper", "year": 2024, "doi": "10.1/x", "relevance": 0.9}]
        report = rpf.find_replacements(
            _review(_claim("t1", "claim", ["oldkey"], ["wrong_source"])),
            self.dir, search_fn=self._search_fn(cands),
            download_fn=self._download_ok, session="S")
        md = rpf.render_report(report)
        self.assertIn("Propose-only", md)
        self.assertIn("Nothing in your text was changed", md)
        self.assertIn("Suggested:", md)


if __name__ == "__main__":
    unittest.main()
