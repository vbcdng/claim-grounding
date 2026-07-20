"""P3 date context (owner ruling 2026-07-11): judges get the source's
publication date in the passage header, may resolve relative time references
against it, and a DATE-INFERRED reason surfaces as a visible flag + viewer
chip. No API calls.

Run:  venv/bin/python3 -m unittest tests.test_date_context -v
"""
import json
import os
import sys
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.papertrail import matcher

CLAIM = "Both volleyball teams won the national student cup in 2019."
S_WIN = "Both teams took the National Student Cup this year."


def _src(doc_date=""):
    s = {"title": "Essex volleyball news", "key": "essex",
         "sentences": [{"text": S_WIN, "page": 1}], "claims": []}
    if doc_date:
        s["doc_date"] = doc_date
    return s


class TestSrcLabel(unittest.TestCase):
    def test_label_with_date(self):
        self.assertEqual(matcher._src_label(_src("2019-02-15")),
                         "Essex volleyball news (article dated 2019-02-15)")

    def test_label_without_date(self):
        self.assertEqual(matcher._src_label(_src()), "Essex volleyball news")
        self.assertEqual(matcher._src_label(None), "")


class TestJudgePassageHeader(unittest.TestCase):
    def _run_judge(self, src):
        seen = {}

        def call(p, **kw):
            seen["prompt"] = p
            return json.dumps({"supported": True, "reason": "DATE-INFERRED: ok"})

        llm = MagicMock()
        llm.call.side_effect = call
        e = matcher._judge_source(CLAIM, "p1", src, [0.8], llm,
                                  "JUDGE {CLAIM} {PASSAGE}")
        return e, seen.get("prompt", "")

    def test_dated_source_header(self):
        e, prompt = self._run_judge(_src("2019-02-15"))
        self.assertIn("(article dated 2019-02-15)", prompt)
        # the metadata-resolution rule is injected for dated passages only
        self.assertIn("Article metadata:", prompt)
        self.assertTrue(e["supported"])
        # display fields stay bare — no date leaks into the shown title
        self.assertEqual(e["source_title"], "Essex volleyball news")

    def test_undated_source_header_unchanged(self):
        # undated source -> prompt byte-identical to the pre-P3 prompt: no
        # header decoration AND no injected rule (gate stability)
        _, prompt = self._run_judge(_src())
        self.assertNotIn("article dated", prompt)
        self.assertNotIn("Article metadata:", prompt)

    def test_inject_date_rule_anchor(self):
        p = "JUDGE stuff\n\nReturn ONLY a JSON object, no commentary:\n{...}"
        out = matcher._inject_date_rule(p, "From x (article dated 2020-01-01): y")
        self.assertIn("Article metadata:", out)
        self.assertLess(out.index("Article metadata:"),
                        out.index("Return ONLY a JSON object"))
        self.assertEqual(matcher._inject_date_rule(p, "From x: y"), p)


class TestDateInferredFlag(unittest.TestCase):
    def _evaluate(self, reason):
        def call(p, **kw):
            return json.dumps({"supported": True, "reason": reason})

        llm = MagicMock()
        llm.call.side_effect = call
        return matcher._evaluate(CLAIM, ["p1"], lambda pid: [0.8],
                                 {"p1": _src("2019-02-15")}, llm,
                                 "J {CLAIM} {PASSAGE}", "E {CLAIM} {SOURCE}",
                                 "C {CLAIM} {PASSAGE}")

    def test_flag_set_on_marker(self):
        out = self._evaluate("DATE-INFERRED: 'this year' resolved to 2019")
        self.assertEqual(out["verdict"], "supported")
        self.assertTrue(out.get("date_inferred"))

    def test_no_flag_without_marker(self):
        # claim year 2019 IS in the evidence sentence -> no inference happened
        def call(p, **kw):
            return json.dumps({"supported": True, "reason": "directly stated"})

        llm = MagicMock()
        llm.call.side_effect = call
        src = _src("2019-02-15")
        src["sentences"] = [{"text": "Both teams took the cup in 2019.", "page": 1}]
        out = matcher._evaluate(CLAIM, ["p1"], lambda pid: [0.8], {"p1": src},
                                llm, "J {CLAIM} {PASSAGE}", "E {CLAIM} {SOURCE}",
                                "C {CLAIM} {PASSAGE}")
        self.assertEqual(out["verdict"], "supported")
        self.assertNotIn("date_inferred", out)

    def test_deterministic_fallback_fires(self):
        # judge ignores the DATE-INFERRED instruction (flash-lite does), but the
        # claim's year is absent from all shown evidence while the article date
        # carries it -> flag anyway
        out = self._evaluate("explicitly stated that they won in 2019")
        # evidence sentence says "this year", never 2019; doc_date=2019-02-15
        self.assertEqual(out["verdict"], "supported")
        self.assertTrue(out.get("date_inferred"))

    def test_fallback_silent_without_doc_date(self):
        def call(p, **kw):
            return json.dumps({"supported": True, "reason": "stated"})

        llm = MagicMock()
        llm.call.side_effect = call
        out = matcher._evaluate(CLAIM, ["p1"], lambda pid: [0.8],
                                {"p1": _src()}, llm, "J {CLAIM} {PASSAGE}",
                                "E {CLAIM} {SOURCE}", "C {CLAIM} {PASSAGE}")
        self.assertEqual(out["verdict"], "supported")
        self.assertNotIn("date_inferred", out)


class TestBylineContext(unittest.TestCase):
    def test_doc_author_from_meta_marker_only(self):
        s = {"sentences": [{"text": "(meta data) AUTHOR: Celia Shatzman"},
                           {"text": "Annie wrote many things."}]}
        self.assertEqual(matcher._doc_author(s), "Celia Shatzman")
        # no explicit marker -> never guessed from prose
        s2 = {"sentences": [{"text": "By Annie Zaleski, staff writer."}]}
        self.assertEqual(matcher._doc_author(s2), "")

    def test_label_with_byline_and_date(self):
        src = _src("2019-02-15")
        src["doc_author"] = "Annie Zaleski"
        self.assertEqual(matcher._src_label(src),
                         "Essex volleyball news (byline: Annie Zaleski; "
                         "article dated 2019-02-15)")

    def test_rule_injected_for_byline_only_header(self):
        p = "J\n\nReturn ONLY a JSON object:"
        out = matcher._inject_date_rule(p, "From x (byline: Ann Smith): y")
        self.assertIn("Article metadata:", out)

    def test_byline_marker_sets_flag(self):
        def call(prompt, **kw):
            return json.dumps({"supported": True,
                               "reason": "BYLINE-INFERRED: reviewer is the author"})
        llm = MagicMock()
        llm.call.side_effect = call
        src = _src()
        src["doc_author"] = "Annie Zaleski"
        out = matcher._evaluate("Annie Zaleski praised the video.", ["p1"],
                                lambda pid: [0.8], {"p1": src}, llm,
                                "J {CLAIM} {PASSAGE}", "E {CLAIM} {SOURCE}",
                                "C {CLAIM} {PASSAGE}")
        self.assertTrue(out.get("byline_inferred"))

    def test_deterministic_byline_fallback(self):
        # judge passes without the marker; claim names the author, evidence
        # doesn't -> the byline was the only in-context proof
        def call(prompt, **kw):
            return json.dumps({"supported": True, "reason": "stated"})
        llm = MagicMock()
        llm.call.side_effect = call
        src = _src()
        src["doc_author"] = "Annie Zaleski"
        out = matcher._evaluate("Annie Zaleski praised the video.", ["p1"],
                                lambda pid: [0.8], {"p1": src}, llm,
                                "J {CLAIM} {PASSAGE}", "E {CLAIM} {SOURCE}",
                                "C {CLAIM} {PASSAGE}")
        self.assertTrue(out.get("byline_inferred"))

    def test_no_flag_when_evidence_names_the_person(self):
        def call(prompt, **kw):
            return json.dumps({"supported": True, "reason": "stated"})
        llm = MagicMock()
        llm.call.side_effect = call
        src = _src()
        src["doc_author"] = "Annie Zaleski"
        src["sentences"] = [{"text": "Annie Zaleski praised the video warmly.",
                             "page": 1}]
        out = matcher._evaluate("Annie Zaleski praised the video.", ["p1"],
                                lambda pid: [0.8], {"p1": src}, llm,
                                "J {CLAIM} {PASSAGE}", "E {CLAIM} {SOURCE}",
                                "C {CLAIM} {PASSAGE}")
        self.assertNotIn("byline_inferred", out)


class TestRunComputesDocDate(unittest.TestCase):
    def test_run_fills_doc_date(self):
        sources = {"p1": {"title": "t", "key": "k", "claims": [],
                          "sentences": [{"text": "Posted: Nov 14, 2018"},
                                        {"text": S_WIN}]}}
        llm = MagicMock()
        llm.call.return_value = json.dumps({"supported": False, "reason": "no"})
        matcher.run([], sources, llm)
        self.assertEqual(sources["p1"]["doc_date"], "2018-11-14")


class TestViewerChip(unittest.TestCase):
    def test_datechip_rendered(self):
        import tempfile
        from modules.papertrail import viewer
        claim = {"id": "t1", "text": CLAIM, "markers": ["essex"],
                 "paper_ids": ["p1"],
                 "verdict": "supported", "method": "llm", "reason": "ok",
                 "date_inferred": True,
                 "evidence": {"paper_id": "p1", "source_title": "t",
                              "sentence": S_WIN, "supported": True},
                 "evidences": [{"paper_id": "p1", "source_title": "t",
                                "sentence": S_WIN, "supported": True}]}
        analysis = {"text_claims": [claim],
                    "sources": [{"paper_id": "p1", "key": "essex",
                                 "filename": "p1.txt", "title": "t"}],
                    "coverage": {"totals": {"claims": 1, "supported": 1,
                                            "unsupported": 0, "own": 0,
                                            "omitted": 0}},
                    "metadata": {}, "omitted": []}
        out = os.path.join(tempfile.mkdtemp(), "v.html")
        viewer.generate(analysis, out)
        html = open(out, encoding="utf-8").read()
        self.assertIn("datechip", html)
        self.assertIn("date inferred from article date", html)


if __name__ == "__main__":
    unittest.main()
