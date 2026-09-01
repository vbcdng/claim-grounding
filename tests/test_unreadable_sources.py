"""Unreadable cited sources (task #69, known-issues item 2): a source file that
parsed to zero sentences (scanned PDF, empty text layer) must show as a
distinct "could not be checked" state — a grey per-source card note and an
"unverifiable" header count — never as an ordinary judged rejection. The
stored verdict stays "unsupported" (display-only, like source_file_missing).
No API calls.

Run:  venv/bin/python3 -m unittest tests.test_unreadable_sources -v
"""
import os
import sys
import json
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.papertrail import matcher, viewer, viewer_v2, source_decomposer


def _fake_cosine(a, b, **kw):
    return [[0.8] * len(b) for _ in a]


def _sources():
    return {"p1": {"title": "Bridge Survey", "key": "a",
                   "sentences": [{"text": "The surveyed bridge measures 400 m.",
                                  "page": 3}],
                   "claims": []},
            "p2": {"title": "Scanned Report", "key": "b",
                   "sentences": [], "claims": []},          # unreadable: empty
            "p3": {"title": "Ciphered Report", "key": "g",
                   "sentences": [{"text": "ZK[KF ZIN FZ\\OIS K", "page": 1}],
                   "claims": [],
                   "text_quality": {"control_char_rate": 0.15,
                                    "unreadable": True}}}   # unreadable: garbled


def _llm_supporting():
    llm = MagicMock()

    def call(p, **kw):
        if "evidence finder" in p:
            return json.dumps({"sentences": []})
        return json.dumps({"supported": True, "reason": "stated verbatim",
                           "sentence": "The surveyed bridge measures 400 m."})

    llm.call.side_effect = call
    return llm


class TestMatcherRecordsUnreadable(unittest.TestCase):

    def test_single_unreadable_source_keeps_verdict_and_reason(self):
        tc = {"id": "t1", "text": "The bridge is 400 m long.",
              "markers": ["b"], "paper_ids": ["p2"]}
        with patch.object(matcher.embeddings, "cosine_matrix", side_effect=_fake_cosine):
            res = matcher.run([tc], _sources(), MagicMock())
        c = res["text_claims"][0]
        self.assertEqual(c["verdict"], "unsupported")          # unchanged contract
        self.assertTrue(c["reason"].startswith("no_source_sentences"))
        self.assertEqual([u["key"] for u in c["unreadable_sources"]], ["b"])

    def test_multi_citation_claim_names_the_unreadable_member(self):
        tc = {"id": "t1", "text": "The bridge is 400 m long.",
              "markers": ["a", "b"], "paper_ids": ["p1", "p2"]}
        with patch.object(matcher.embeddings, "cosine_matrix", side_effect=_fake_cosine):
            res = matcher.run([tc], _sources(), _llm_supporting())
        c = res["text_claims"][0]
        self.assertEqual(c["verdict"], "supported")            # judged on the readable one
        self.assertEqual([u["key"] for u in c["unreadable_sources"]], ["b"])

    def test_readable_sources_add_no_field(self):
        tc = {"id": "t1", "text": "The bridge is 400 m long.",
              "markers": ["a"], "paper_ids": ["p1"]}
        with patch.object(matcher.embeddings, "cosine_matrix", side_effect=_fake_cosine):
            res = matcher.run([tc], _sources(), _llm_supporting())
        self.assertNotIn("unreadable_sources", res["text_claims"][0])

    def test_garbled_source_is_recorded_with_why(self):
        # Ciphered text layer (task #71 handover): sentences exist but the
        # index-time quality flag marks them gibberish. The judge still judges
        # (verdict untouched); the claim just carries the display field.
        tc = {"id": "t1", "text": "The bridge is 400 m long.",
              "markers": ["a", "g"], "paper_ids": ["p1", "p3"]}
        with patch.object(matcher.embeddings, "cosine_matrix", side_effect=_fake_cosine):
            res = matcher.run([tc], _sources(), _llm_supporting())
        c = res["text_claims"][0]
        self.assertEqual([(u["key"], u["why"]) for u in c["unreadable_sources"]],
                         [("g", "garbled")])
        self.assertEqual(c["verdict"], "supported")   # judged on the readable one

    def test_empty_source_carries_why_empty(self):
        tc = {"id": "t1", "text": "The bridge is 400 m long.",
              "markers": ["b"], "paper_ids": ["p2"]}
        with patch.object(matcher.embeddings, "cosine_matrix", side_effect=_fake_cosine):
            res = matcher.run([tc], _sources(), MagicMock())
        self.assertEqual(res["text_claims"][0]["unreadable_sources"][0]["why"], "empty")


class TestControlCharRate(unittest.TestCase):
    """The index-time quality measure (task #71 handover): control characters
    (below the printable range, excluding tab/CR/LF) mark a ciphered text
    layer; real language in any alphabet scores ~0."""

    def test_clean_text_scores_zero(self):
        q = source_decomposer._text_quality(
            "Ein längerer deutscher Satz über die Brücke.\tMit Tab und\nZeilen.\r\n")
        self.assertEqual(q["control_char_rate"], 0.0)
        self.assertFalse(q["unreadable"])

    def test_ciphered_text_is_flagged(self):
        garble = ("ZK\x03KF\x02 ZIN\x01 FZ\x04OIS K " * 40)
        q = source_decomposer._text_quality(garble)
        self.assertGreater(q["control_char_rate"],
                           source_decomposer.GARBLED_CONTROL_RATE)
        self.assertTrue(q["unreadable"])

    def test_empty_text_scores_zero(self):
        self.assertEqual(source_decomposer._control_char_rate(""), 0.0)

    def test_decompose_source_stores_quality_and_warns_on_garble(self):
        import tempfile as tf
        tmp = tf.mkdtemp()
        path = os.path.join(tmp, "g.txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write("AB\x03CD\x02 EF\x01GH. " * 200)
        res = source_decomposer.decompose_source(
            path, "pg", "g", os.path.join(tmp, "cache"), MagicMock(),
            extract_claims=False)
        self.assertTrue(res["text_quality"]["unreadable"])
        self.assertIn("source_text_garbled", res.get("warning", ""))

    def test_decompose_source_clean_file_not_flagged(self):
        import tempfile as tf
        tmp = tf.mkdtemp()
        path = os.path.join(tmp, "c.txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write("The surveyed bridge measures four hundred meters. " * 50)
        res = source_decomposer.decompose_source(
            path, "pc", "c", os.path.join(tmp, "cache"), MagicMock(),
            extract_claims=False)
        self.assertFalse(res["text_quality"]["unreadable"])
        self.assertNotIn("warning", res)


def _analysis():
    return {"text_claims": [
                {"id": "t1", "text": "The bridge is haunted.", "markers": ["b"],
                 "paper_ids": ["p2"], "verdict": "unsupported", "method": "none",
                 "reason": "no_source_sentences (source empty or unreadable)",
                 "evidence": None, "evidences": [],
                 "unreadable_sources": [{"pid": "p2", "key": "b",
                                         "title": "Scanned Report", "why": "empty"}]},
                {"id": "t2", "text": "The bridge sings at night.", "markers": ["g"],
                 "paper_ids": ["p3"], "verdict": "unsupported", "method": "llm_fulltext",
                 "reason": "not stated in the source",
                 "evidence": None, "evidences": [],
                 "unreadable_sources": [{"pid": "p3", "key": "g",
                                         "title": "Ciphered Report", "why": "garbled"}]},
            ],
            "sources": [{"paper_id": "p2", "key": "b", "filename": "b.pdf",
                         "title": "Scanned Report"},
                        {"paper_id": "p3", "key": "g", "filename": "g.pdf",
                         "title": "Ciphered Report"}],
            "coverage": {"totals": {"claims": 2, "supported": 0, "unsupported": 2,
                                    "own": 0, "omitted": 0}},
            "metadata": {"output_dir": "/tmp/runs/x"}, "omitted": []}


class TestViewerShowsUnreadable(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        out = os.path.join(tempfile.mkdtemp(), "v.html")
        viewer.generate(_analysis(), out)
        with open(out, encoding="utf-8") as f:
            cls.page = f.read()

    def test_card_has_plain_language_note_not_raw_reason(self):
        self.assertIn("source unreadable", self.page)
        self.assertIn("no readable text", self.page)
        self.assertNotIn("⚠ no_source_sentences", self.page)

    def test_header_counts_both_as_unverifiable(self):
        # t1 (empty) via its reason, t2 (garbled, judged reason) via the
        # all-cited-sources-unreadable rule.
        self.assertIn("2 unverifiable", self.page)
        self.assertIn("source unreadable)", self.page)

    def test_garbled_card_note(self):
        self.assertIn("source text garbled", self.page)
        self.assertIn("gibberish", self.page)

    def test_confidence_tag_suppressed_for_both(self):
        self.assertIsNone(viewer._confidence(_analysis()["text_claims"][0]))
        self.assertIsNone(viewer._confidence(_analysis()["text_claims"][1]))

    def test_partly_readable_claim_keeps_its_confidence(self):
        # Only one of two cited sources unreadable -> still a real judgment.
        c = {"id": "t3", "text": "x", "paper_ids": ["p1", "p3"],
             "verdict": "supported", "method": "llm", "reason": "ok",
             "cosine": 0.9, "votes": None,
             "evidences": [{"paper_id": "p1", "supported": True,
                            "sentence": "s", "votes": None}],
             "unreadable_sources": [{"pid": "p3", "key": "g", "why": "garbled"}]}
        self.assertIsNotNone(viewer._confidence(c))


class TestViewerV2ShowsUnreadable(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        out = os.path.join(tempfile.mkdtemp(), "v2.html")
        viewer_v2.generate(_analysis(), out)
        with open(out, encoding="utf-8") as f:
            cls.page = f.read()

    def test_card_note_and_header_count(self):
        self.assertIn("no readable text", self.page)
        self.assertIn("gibberish", self.page)
        self.assertIn("2 unverifiable", self.page)
        self.assertIn("source unreadable)", self.page)


if __name__ == "__main__":
    unittest.main()
