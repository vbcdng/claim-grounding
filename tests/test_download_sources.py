"""Unit tests for the source downloader's routing/matching/report logic.

No network anywhere — HTTP is mocked. Run:
  venv/bin/python3 -m unittest tests.test_download_sources -v
"""

import os
import sys
import json
import tempfile
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.papertrail import direct_downloader as dd
from modules.papertrail import semantic_scholar_api as s2
import download_sources


def entry(**kw):
    base = {"key": "k1", "title": "T", "year": None, "url": None, "doi": None,
            "arxiv_id": None, "pmc_id": None, "s2_paper_id": None,
            "oa_pdf_url": None, "status": "has_link"}
    base.update(kw)
    return base


class TestUrlAnalysis(unittest.TestCase):
    def test_arxiv_id_from_abs_and_pdf_urls(self):
        self.assertEqual(dd.extract_arxiv_id("https://arxiv.org/abs/2402.08797"), "2402.08797")
        self.assertEqual(dd.extract_arxiv_id("https://arxiv.org/pdf/2402.08797v2"), "2402.08797v2")
        self.assertIsNone(dd.extract_arxiv_id("https://example.org/x"))

    def test_doi_from_url(self):
        self.assertEqual(dd.extract_doi_from_url("https://doi.org/10.1000/xyz"), "10.1000/xyz")
        self.assertIsNone(dd.extract_doi_from_url("https://nber.org/papers/w1"))

    def test_normalize_derives_ids(self):
        e = dd.normalize_entry({"key": "a", "url": "https://arxiv.org/abs/2402.08797",
                                "doi": None, "status": "has_link"})
        self.assertEqual(e["arxiv_id"], "2402.08797")
        e = dd.normalize_entry({"key": "b", "url": "https://doi.org/10.5/x",
                                "doi": None, "status": "has_link"})
        self.assertEqual(e["doi"], "10.5/x")


class TestClassify(unittest.TestCase):
    def test_paper_shapes(self):
        self.assertEqual(dd.classify(entry(arxiv_id="2402.08797")), "paper")
        self.assertEqual(dd.classify(entry(doi="10.5/x")), "paper")
        self.assertEqual(dd.classify(entry(url="https://x.org/report.pdf")), "paper")
        self.assertEqual(dd.classify(entry(url="https://www.nber.org/papers/w23928")), "paper")

    def test_plain_web_page(self):
        self.assertEqual(dd.classify(entry(url="https://hai.stanford.edu/ai-index")), "web")
        self.assertEqual(dd.classify(entry(url="https://www.aljazeera.com/news/x")), "web")


class TestTitleMatch(unittest.TestCase):
    def test_exact_and_near(self):
        self.assertTrue(s2._titles_match("Computing Power and the Governance of AI",
                                         "Computing power and the governance of AI"))
        self.assertTrue(s2._titles_match("AI and Economic Growth",
                                         "AI and Economic Growth."))

    def test_different_paper_rejected(self):
        self.assertFalse(s2._titles_match("Superintelligence: Paths, Dangers, Strategies",
                                          "The Simple Macroeconomics of AI"))

    def test_find_paper_year_mismatch_rejected(self):
        hits = [{"paperId": "p1", "title": "Some Paper", "year": 1999}]
        with patch.object(s2, "search_papers", return_value=hits):
            self.assertEqual(s2.find_paper_by_title("Some Paper", year="2024"),
                             (None, "no_match"))
            paper, status = s2.find_paper_by_title("Some Paper", year="1999")
            self.assertEqual((paper["paperId"], status), ("p1", "matched"))

    def test_find_paper_search_failure_distinguished(self):
        with patch.object(s2, "search_papers", return_value=None):
            self.assertEqual(s2.find_paper_by_title("Some Paper"),
                             (None, "search_failed"))
        with patch.object(s2, "search_papers", return_value=[]):
            self.assertEqual(s2.find_paper_by_title("Some Paper"),
                             (None, "no_match"))

    def test_enrich(self):
        paper = {"paperId": "abc123", "title": "T",
                 "externalIds": {"DOI": "10.5/x", "ArXiv": "2402.1"},
                 "openAccessPdf": {"url": "https://x/p.pdf"}}
        e = s2.enrich_entry_from_s2(entry(), paper)
        self.assertEqual((e["doi"], e["arxiv_id"], e["s2_paper_id"], e["oa_pdf_url"]),
                         ("10.5/x", "2402.1", "abc123", "https://x/p.pdf"))


class TestDownloadSourceRouting(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def test_already_present_skips(self):
        path = os.path.join(self.dir, "k1.pdf")
        with open(path, "wb") as f:
            f.write(b"%PDF" + b"x" * 2000)
        r = dd.download_source(entry(), self.dir, session=MagicMock())
        self.assertEqual(r["outcome"], "already_present")

    def test_web_page_saves_text(self):
        e = entry(url="https://example.org/report")
        with patch.object(dd, "download_file", return_value=False) as df, \
             patch.object(dd, "try_page",
                          return_value=("text", "k1.txt", "page text, 1200 words")) as tp:
            r = dd.download_source(e, self.dir, session=MagicMock())
        self.assertEqual(r["outcome"], "text")
        self.assertEqual(r["filename"], "k1.txt")
        df.assert_called_once()   # the cheap is-it-secretly-a-PDF attempt
        tp.assert_called_once()

    def test_web_page_total_failure_reports_landing(self):
        e = entry(url="https://example.org/report")
        with patch.object(dd, "download_file", return_value=False), \
             patch.object(dd, "try_page", return_value=(None, None, "page fetch failed")):
            r = dd.download_source(e, self.dir, session=MagicMock())
        self.assertEqual(r["outcome"], "not_fetchable")
        self.assertEqual(r["landing"], "https://example.org/report")

    def test_force_bypasses_existing(self):
        path = os.path.join(self.dir, "k1.pdf")
        with open(path, "wb") as f:
            f.write(b"%PDF" + b"x" * 2000)
        e = entry(url="https://example.org/report")
        with patch.object(dd, "download_file", return_value=False), \
             patch.object(dd, "try_page", return_value=("text", "k1.txt", "page text")):
            r = dd.download_source(e, self.dir, session=MagicMock(), force=True)
        self.assertEqual(r["outcome"], "text")

    def test_arxiv_paper_uses_pdf_url(self):
        e = entry(url="https://arxiv.org/abs/2402.08797", arxiv_id="2402.08797")
        seen = []

        def fake_download(url, path, session, **kw):
            seen.append(url)
            if "arxiv.org/pdf" in url:
                with open(path, "wb") as f:
                    f.write(b"%PDF fake")
                return True
            return False

        with patch.object(dd, "download_file", side_effect=fake_download), \
             patch.object(dd, "pdf_has_text", return_value=True):
            r = dd.download_source(e, self.dir, session=MagicMock())
        self.assertEqual(r["outcome"], "pdf")
        self.assertIn("https://arxiv.org/pdf/2402.08797.pdf", seen)

    def test_pdf_without_text_flagged(self):
        e = entry(url="https://x.org/p.pdf")

        def fake_download(url, path, session, **kw):
            with open(path, "wb") as f:
                f.write(b"%PDF fake")
            return True

        with patch.object(dd, "download_file", side_effect=fake_download), \
             patch.object(dd, "pdf_has_text", return_value=False):
            r = dd.download_source(e, self.dir, session=MagicMock())
        self.assertEqual(r["outcome"], "pdf_no_text")

    def test_doi_landing_used_when_no_pdf(self):
        e = entry(doi="10.5/x")
        resp = MagicMock(url="https://publisher.org/article/1")
        session = MagicMock()
        session.get.return_value = resp
        session.headers = {}
        with patch.object(dd, "download_file", return_value=False), \
             patch.object(dd, "_unpaywall_pdf_urls", return_value=[]), \
             patch.object(dd, "try_page",
                          return_value=("text", "k1.txt", "page text, 3000 words")) as tp:
            r = dd.download_source(e, self.dir, session=session)
        self.assertEqual(r["outcome"], "text")
        tp.assert_called_with("https://publisher.org/article/1", "k1", self.dir, session,
                              title="T", author=None)
        self.assertEqual(r["landing"], "https://doi.org/10.5/x")


class TestPageHelpers(unittest.TestCase):
    def test_extract_pdf_links_meta_first_then_anchors(self):
        from bs4 import BeautifulSoup
        html = '''<html><head>
          <meta name="citation_pdf_url" content="/system/files/w31815.pdf"></head>
          <body><a href="other.html">x</a>
          <a href="/dl/report.pdf?utm=1">report</a>
          <a href="/dl/report.pdf?utm=1">dup</a></body></html>'''
        soup = BeautifulSoup(html, "html.parser")
        links = dd.extract_pdf_links(soup, "https://www.nber.org/papers/w31815")
        self.assertEqual(links, ["https://www.nber.org/system/files/w31815.pdf",
                                 "https://www.nber.org/dl/report.pdf?utm=1"])

    def test_try_page_prefers_linked_pdf(self):
        from bs4 import BeautifulSoup
        soup = BeautifulSoup('<meta name="citation_pdf_url" content="https://x.org/p.pdf">',
                             "html.parser")
        with tempfile.TemporaryDirectory() as d, \
             patch.object(dd, "fetch_html", return_value=(soup, "https://x.org/page")), \
             patch.object(dd, "download_file", return_value=True) as df:
            outcome, filename, _ = dd.try_page("https://x.org/page", "k1", d, MagicMock())
        self.assertEqual((outcome, filename), ("pdf", "k1.pdf"))
        self.assertEqual(df.call_args[0][0], "https://x.org/p.pdf")

    def test_try_page_flags_thin_text(self):
        from bs4 import BeautifulSoup
        body = " ".join(["some sentence of words here"] * 30)  # ~150 words, >500 chars
        soup = BeautifulSoup(f"<body><main><p>{body}</p></main></body>", "html.parser")
        with tempfile.TemporaryDirectory() as d, \
             patch.object(dd, "fetch_html", return_value=(soup, "https://x.org/page")):
            outcome, filename, detail = dd.try_page("https://x.org/page", "k1", d, MagicMock())
            self.assertEqual((outcome, filename), ("text_thin", "k1.txt"))
            self.assertIn("words extracted", detail)
            self.assertTrue(os.path.exists(os.path.join(d, "k1.txt")))


class TestRefsRewrite(unittest.TestCase):
    def test_rewrites_only_saved_keys_preserves_comments(self):
        with tempfile.TemporaryDirectory() as d:
            refs = os.path.join(d, "r.refs.txt")
            with open(refs, "w") as f:
                f.write("# comment with = sign\na = a.pdf\nb = b.pdf\nc = custom_name.pdf\n")
            download_sources.rewrite_refs(refs, {"a": "a.txt", "c": "c.pdf"})
            lines = open(refs).read().splitlines()
            self.assertEqual(lines, ["# comment with = sign", "a = a.txt",
                                     "b = b.pdf", "c = c.pdf"])


class TestReport(unittest.TestCase):
    def test_report_covers_whole_manifest_not_just_run(self):
        with tempfile.TemporaryDirectory() as d:
            sources = os.path.join(d, "sources")
            os.makedirs(sources)
            with open(os.path.join(sources, "a.txt"), "w") as f:
                f.write("Source URL: x\n\n---\n\n" + "word " * 2000)   # healthy text
            with open(os.path.join(sources, "t.txt"), "w") as f:
                f.write("Source URL: x\n\n---\n\n" + "word " * 300)    # thin text
            with open(os.path.join(sources, "p.pdf"), "wb") as f:
                f.write(b"%PDF" + b"x" * 2000)
            manifest = [
                {"key": "a", "title": "Paper A", "status": "has_link", "url": "https://x/a"},
                {"key": "t", "title": "Thin T", "status": "has_link", "url": "https://x/t"},
                {"key": "p", "title": "Paper P", "status": "has_link", "url": "https://x/p"},
                {"key": "b", "title": "Paper B", "year": "2024", "doi": "10.5/b",
                 "status": "has_link", "url": None},
                {"key": "c", "title": "Paper C", "author": "Smith, J.", "year": "1994",
                 "status": "needs_search", "url": None, "doi": None},
            ]
            path = os.path.join(d, "report.md")
            # empty run_results = the --report-only case; every entry must still appear
            counts = download_sources.write_report(path, manifest, sources, {})
            self.assertEqual(counts, {"present": 3, "thin": 1, "mismatch": 0,
                                      "missing_link": 1, "missing_search": 1})
            text = open(path).read()
            self.assertIn("3 of 5 sources", text)
            self.assertIn("https://doi.org/10.5/b", text)     # link for manual download
            self.assertIn("needs a literature search", text)
            self.assertIn("Paper C", text)
            self.assertIn("suspiciously thin", text)
            self.assertIn("`t.txt`", text)
            self.assertIn("`a.txt`", text)
            self.assertIn("`p.pdf`", text)


class TestOpenAlexResolver(unittest.TestCase):
    """queue #7: OpenAlex as a second OA index (prior-art from sciwrite-lint)."""

    def _session(self, status, payload):
        sess = MagicMock()
        resp = MagicMock()
        resp.status_code = status
        resp.json.return_value = payload
        sess.get.return_value = resp
        return sess

    def test_extracts_pdf_urls_best_first(self):
        payload = {
            "best_oa_location": {"pdf_url": "https://oa.example/best.pdf"},
            "locations": [{"pdf_url": "https://oa.example/best.pdf"},
                          {"pdf_url": "https://repo.example/copy.pdf"},
                          {"pdf_url": None}],
            "open_access": {"oa_url": "https://oa.example/landing.pdf"},
        }
        urls = dd._openalex_pdf_urls("10.1/x", self._session(200, payload))
        self.assertEqual(urls[0], "https://oa.example/best.pdf")
        self.assertIn("https://repo.example/copy.pdf", urls)
        self.assertEqual(len(urls), len(set(urls)))  # de-duplicated

    def test_404_and_no_oa_return_empty(self):
        self.assertEqual(dd._openalex_pdf_urls("10.1/x", self._session(404, {})), [])
        no_pdf = {"best_oa_location": None, "locations": [], "open_access": {"oa_url": None}}
        self.assertEqual(dd._openalex_pdf_urls("10.1/x", self._session(200, no_pdf)), [])

    def test_network_error_is_swallowed(self):
        sess = MagicMock()
        sess.get.side_effect = RuntimeError("boom")
        self.assertEqual(dd._openalex_pdf_urls("10.1/x", sess), [])


if __name__ == "__main__":
    unittest.main()
