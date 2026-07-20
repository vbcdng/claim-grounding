"""The review/repair loop's viewer side: triage marks, review-bar export embeds,
the changed-claims diff view — plus matcher's `alternatives` (re-citation
candidates for "wrong source" repairs). No API calls.

Run:  venv/bin/python3 -m unittest tests.test_review_loop -v
"""
import os
import sys
import json
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.papertrail import matcher, viewer


def _analysis(**meta):
    e = {"paper_id": "p1", "source_title": "Bridge Survey", "supported": True,
         "sentence": "The surveyed bridge measures 400 m in length.", "page": 3}
    m = {"text_file": "/tmp/article.md", "sources_dir": "/tmp/sources",
         "output_dir": "/tmp/run", "model": "gemini/x", "timestamp": "2026-07-04 10:00:00"}
    m.update(meta)
    return {
        "text_claims": [{"id": "t1", "text": "The bridge is long.", "markers": ["a"],
                         "paper_ids": ["p1"], "verdict": "supported", "method": "llm",
                         "reason": "stated", "cosine": 0.9, "evidence": e, "evidences": [e],
                         "alternatives": [{"paper_id": "p2", "source_title": "Other Paper",
                                           "text": "An alternative claim.",
                                           "evidence": "Alternative evidence sentence.",
                                           "relevance": 0.77}]}],
        "sources": [{"paper_id": "p1", "key": "a", "filename": "a.txt", "title": "Bridge Survey"}],
        "coverage": {"totals": {"claims": 1, "supported": 1, "unsupported": 0,
                                "own": 0, "omitted": 0}},
        "metadata": m, "omitted": [],
    }


def _render(analysis):
    out = os.path.join(tempfile.mkdtemp(), "v.html")
    viewer.generate(analysis, out)
    with open(out, encoding="utf-8") as f:
        return f.read()


class TestTriageAndExport(unittest.TestCase):

    def test_every_card_has_the_three_triage_buttons_and_note(self):
        page = _render(_analysis())
        self.assertIn('class="triage" data-id="t1"', page)
        for mark in ("wrong_source", "rewrite", "verdict_wrong"):
            self.assertIn(f'data-mark="{mark}"', page)
        self.assertIn('class="tnote"', page)

    def test_review_bar_present(self):
        page = _render(_analysis())
        self.assertIn('id="revCount"', page)
        self.assertIn("Copy repair brief", page)
        self.assertIn("Download review file", page)
        self.assertIn("Copy research request", page)
        self.assertIn("buildScienceRequest", page)

    def test_review_data_carries_project_dir_for_the_merge_path(self):
        page = _render(_analysis())
        payload = page.split("const REVIEW_DATA = ")[1].split(";\n")[0]
        data = json.loads(payload.replace("<\\/", "</"))
        self.assertEqual(data["run"]["project_dir"], "/tmp")   # dirname of text_file
        self.assertIn("--merge-into", page)                    # brief names the return path

    def test_review_data_embeds_claims_run_info_and_alternatives(self):
        page = _render(_analysis())
        self.assertIn("const REVIEW_DATA = ", page)
        payload = page.split("const REVIEW_DATA = ")[1].split(";\n")[0]
        data = json.loads(payload.replace("<\\/", "</"))
        self.assertEqual(data["run"]["text_file"], "/tmp/article.md")
        self.assertEqual(data["claims"][0]["id"], "t1")
        self.assertEqual(data["claims"][0]["confidence"], "high")
        self.assertEqual(data["claims"][0]["alternatives"][0]["source_title"], "Other Paper")

    def test_run_id_stable_for_same_run_and_new_for_new_run(self):
        p1 = _render(_analysis())
        p2 = _render(_analysis())
        p3 = _render(_analysis(timestamp="2026-07-05 09:00:00"))
        rid = lambda p: p.split("const RUN_ID = '")[1].split("'")[0]
        self.assertEqual(rid(p1), rid(p2))
        self.assertNotEqual(rid(p1), rid(p3))

    def test_omitted_cards_have_no_triage(self):
        a = _analysis()
        a["omitted"] = [{"paper_id": "p1", "source_title": "Bridge Survey",
                         "source_claim_id": "sc9", "text": "Unused source claim.",
                         "evidence": ["Some sentence."], "page": 1, "snippet": "Some",
                         "relevance": 0.5}]
        page = _render(a)
        self.assertEqual(page.count('class="triage"'), 1)     # only the text claim


class TestChangedDiff(unittest.TestCase):

    def _changed_analysis(self):
        a = _analysis()
        a["text_claims"][0]["prev"] = {"changed": True, "text": "The old bridge wording.",
                                       "verdict": "unsupported"}
        return a

    def test_changed_claim_gets_chip_note_filter_and_left_marker(self):
        page = _render(self._changed_analysis())
        self.assertIn("✎ changed", page)
        self.assertIn("The old bridge wording.", page)
        self.assertIn('data-f="changed">Changed (1)', page)
        self.assertIn('class="changedmark"', page)
        self.assertIn("1 changed since last run", page)

    def test_new_claim_without_prev_text_says_new(self):
        a = _analysis()
        a["text_claims"][0]["prev"] = {"changed": True}
        page = _render(a)
        self.assertIn("New since the last run", page)

    def test_no_changed_claims_no_changed_ui(self):
        page = _render(_analysis())
        self.assertNotIn('data-f="changed"', page)
        self.assertNotIn("changed since last run", page)


def _fake_cosine(a, b, **kw):
    return [[0.8] * len(b) for _ in a]


def _llm_never_supports():
    llm = MagicMock()

    def call(p, **kw):
        if "evidence finder" in p:
            return json.dumps({"sentences": []})
        return json.dumps({"supported": False, "reason": "not stated"})

    llm.call.side_effect = call
    return llm


class TestAlternatives(unittest.TestCase):

    def _sources(self):
        return {
            "p1": {"title": "Cited Source", "key": "a",
                   "sentences": [{"text": "A sentence that does not support the claim at all.",
                                  "page": 1}],
                   "claims": []},
            "p2": {"title": "Other Source", "key": "b",
                   "sentences": [{"text": "Other source sentence one for the record.", "page": 1}],
                   "claims": [{"id": "sc0", "text": "A promising replacement claim.",
                               "evidence": ["Other source sentence one for the record."]},
                              {"id": "sc1", "text": "Second candidate claim.",
                               "evidence": ["Other source sentence one for the record."]}]},
        }

    def test_unsupported_claim_gets_alternatives_from_other_sources_only(self):
        tc1 = {"id": "t1", "text": "An unsupported single-sentence assertion.",
               "markers": ["a"], "paper_ids": ["p1"]}
        # p2 must be a cited source in the run (another claim cites it)
        tc2 = {"id": "t2", "text": "Something the other source states.",
               "markers": ["b"], "paper_ids": ["p2"]}
        with patch.object(matcher.embeddings, "cosine_matrix", side_effect=_fake_cosine):
            analysis = matcher.run([tc1, tc2], self._sources(), _llm_never_supports())
        c = analysis["text_claims"][0]
        self.assertEqual(c["verdict"], "unsupported")
        alts = c.get("alternatives")
        self.assertTrue(alts)
        self.assertTrue(all(a["paper_id"] == "p2" for a in alts))
        self.assertLessEqual(len(alts), matcher.ALTERNATIVES_PER_CLAIM)
        self.assertEqual(alts[0]["evidence"], "Other source sentence one for the record.")
        self.assertAlmostEqual(alts[0]["relevance"], 0.8)

    def test_supported_claims_get_no_alternatives(self):
        tc = {"id": "t1", "text": "A supported assertion.", "markers": ["a"], "paper_ids": ["p1"]}
        llm = MagicMock()
        llm.call.side_effect = lambda p, **kw: json.dumps({"supported": True, "reason": "yes"})
        with patch.object(matcher.embeddings, "cosine_matrix", side_effect=_fake_cosine):
            analysis = matcher.run([tc], self._sources(), llm)
        self.assertNotIn("alternatives", analysis["text_claims"][0])


if __name__ == "__main__":
    unittest.main()
