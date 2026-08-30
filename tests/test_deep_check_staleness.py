"""Task #57: deep_check.json staleness guard (modules/papertrail/deep_check_store.py).

Claim ids handed out by text_decomposer are POSITIONAL (t0, t1, t2 ... down the
document), so after the author edits their text the same id can mean a
different sentence. Before this fix, a re-run left an old deep_check.json
beside fresh verdicts and anything joining by bare id could silently pair an
old comment with a new verdict. `wrap()` now stamps each comment with the
claim's text hash + verdict at check time; `validate()`/`load_valid()` hand a
consumer only the comments that still match, with a report of what was
dropped and why; `archive_previous()` moves an old file aside on a re-run so
it never sits next to fresh verdicts.

No network, no API calls.

Run:  venv/bin/python3 -m unittest tests.test_deep_check_staleness -v
"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.papertrail import deep_check_store as dcs


def _analysis(claims):
    """A minimal analysis.json-shaped dict: just what wrap/validate/viewer need."""
    return {
        "metadata": {"model": "fake/model", "timestamp": "2026-08-19T00:00:00Z",
                     "text_file": "t.md"},
        "text_claims": claims,
        "sources": [{"paper_id": "p1", "key": "a", "filename": "p1.txt",
                     "title": "Source One", "num_claims": 1}],
        "coverage": {"totals": {}},
        "omitted": [],
    }


def _claim(cid, text, verdict="supported", **extra):
    c = {"id": cid, "text": text, "verdict": verdict, "markers": ["a"],
         "paper_ids": ["p1"], "reason": "ok", "cosine": 0.9,
         "evidences": [{"paper_id": "p1", "source_title": "Source One",
                        "supported": verdict == "supported",
                        "sentence": text, "page": 1}]}
    c.update(extra)
    return c


def _result(supported=True, commentary="looks fine", **extra):
    r = {"model": "claude-code/sonnet", "supported": supported,
         "agrees": True, "confidence": "high", "commentary": commentary,
         "quote": "a quote", "better_sentence": None}
    r.update(extra)
    return r


class WrapValidateRoundTripTests(unittest.TestCase):
    """Case A: a fresh wrap validates fully against the analysis it describes."""

    def test_round_trip_all_usable_and_fingerprint_matches(self):
        analysis = _analysis([_claim("t0", "The bridge is long."),
                              _claim("t1", "The river is wide.", verdict="unsupported")])
        results = {"t0": _result(), "t1": _result(supported=False, agrees=True)}
        payload = dcs.wrap(analysis, "claude-code/sonnet", results)
        usable, report = dcs.validate(payload, analysis)
        self.assertEqual(set(usable), {"t0", "t1"})
        self.assertEqual(report["usable"], 2)
        self.assertEqual(report["dropped"], 0)
        self.assertEqual(report["total"], 2)
        self.assertIs(report["fingerprint_matches"], True)
        self.assertEqual(report["reasons"], {})


class ClaimTextChangedTests(unittest.TestCase):
    """Case B: same id, different sentence (a positional renumbering)."""

    def test_changed_claim_text_is_dropped_others_stay_usable(self):
        analysis = _analysis([_claim("t0", "The bridge is long."),
                              _claim("t1", "The river is wide.")])
        results = {"t0": _result(), "t1": _result()}
        payload = dcs.wrap(analysis, "claude-code/sonnet", results)

        # Author edited their text: t0 now names a different sentence, as a
        # positional renumbering would produce after an insertion/deletion.
        analysis["text_claims"][0]["text"] = "A completely different claim about ferries."
        usable, report = dcs.validate(payload, analysis)

        self.assertNotIn("t0", usable)
        self.assertIn("t1", usable)
        self.assertEqual(report["reasons"].get("claim_text_changed"), 1)
        self.assertEqual(report["usable"], 1)
        self.assertEqual(report["dropped"], 1)


class VerdictChangedTests(unittest.TestCase):
    """Case C: same sentence, the tool now says something different about it."""

    def test_changed_verdict_is_dropped_others_stay_usable(self):
        analysis = _analysis([_claim("t0", "The bridge is long.", verdict="supported"),
                              _claim("t1", "The river is wide.", verdict="supported")])
        results = {"t0": _result(), "t1": _result()}
        payload = dcs.wrap(analysis, "claude-code/sonnet", results)

        analysis["text_claims"][0]["verdict"] = "unsupported"
        usable, report = dcs.validate(payload, analysis)

        self.assertNotIn("t0", usable)
        self.assertIn("t1", usable)
        self.assertEqual(report["reasons"].get("verdict_changed"), 1)
        self.assertEqual(report["usable"], 1)
        self.assertEqual(report["dropped"], 1)


class PreTask57FileTests(unittest.TestCase):
    """Case D: a file written before the stamp existed (no 'format' key).

    Shape modelled on data/paper1_verification/deep_check.json (read, not
    copied): top level is {"model": ..., "results": {id: {model, supported,
    agrees, commentary, quote, better_sentence}}} — no format/fingerprint,
    and no per-result claim_sha/verdict_checked.
    """

    def test_unstamped_file_yields_zero_usable_all_dropped_as_no_version_info(self):
        analysis = _analysis([_claim("t0", "The bridge is long."),
                              _claim("t1", "The river is wide.")])
        legacy_payload = {
            "model": "claude-code/sonnet",
            "results": {
                "t0": {"model": "claude-code/sonnet", "supported": True,
                       "agrees": False, "confidence": "medium",
                       "commentary": "old-style comment, no stamp",
                       "quote": "q", "better_sentence": None},
                "t1": {"model": "claude-code/sonnet", "supported": False,
                       "agrees": True, "confidence": "high",
                       "commentary": "another old-style comment",
                       "quote": "q2", "better_sentence": None},
            },
        }
        usable, report = dcs.validate(legacy_payload, analysis)
        self.assertEqual(usable, {})
        self.assertEqual(report["total"], 2)
        self.assertEqual(report["usable"], 0)
        self.assertEqual(report["dropped"], 2)
        self.assertEqual(report["reasons"], {"no_version_information": 2})
        self.assertIsNone(report["fingerprint_matches"])


class WhitespaceNormalizationTests(unittest.TestCase):
    """Case E: whitespace-only text differences must not count as a change."""

    def test_whitespace_only_difference_keeps_comment_usable(self):
        analysis = _analysis([_claim("t0", "The  bridge   is\nlong.")])
        results = {"t0": _result()}
        payload = dcs.wrap(analysis, "claude-code/sonnet", results)

        # Re-flowed whitespace only — same claim, same meaning.
        analysis["text_claims"][0]["text"] = "The bridge is long."
        usable, report = dcs.validate(payload, analysis)

        self.assertIn("t0", usable)
        self.assertEqual(report["dropped"], 0)
        self.assertEqual(report["reasons"], {})


class LoadValidNoFileTests(unittest.TestCase):
    """Case F: load_valid on a run dir with no deep_check.json."""

    def test_missing_file_returns_empty_without_raising(self):
        with tempfile.TemporaryDirectory() as run_dir:
            analysis = _analysis([_claim("t0", "The bridge is long.")])
            usable, report = dcs.load_valid(run_dir, analysis)
            self.assertEqual(usable, {})
            self.assertFalse(report["present"])
            self.assertEqual(report["total"], 0)
            self.assertEqual(report["usable"], 0)
            self.assertEqual(report["dropped"], 0)


class ArchivePreviousTests(unittest.TestCase):
    """Case G: archive_previous renames deep_check.json to deep_check_prev.json."""

    def test_archives_existing_file_preserving_content(self):
        with tempfile.TemporaryDirectory() as run_dir:
            src = os.path.join(run_dir, dcs.FILENAME)
            content = {"model": "m", "results": {"t0": {"supported": True}}}
            with open(src, "w", encoding="utf-8") as f:
                json.dump(content, f)

            archived = dcs.archive_previous(run_dir)

            self.assertEqual(archived, os.path.join(run_dir, dcs.PREV_FILENAME))
            self.assertFalse(os.path.exists(src))
            self.assertTrue(os.path.exists(archived))
            with open(archived, encoding="utf-8") as f:
                self.assertEqual(json.load(f), content)

    def test_no_file_present_returns_none_and_creates_nothing(self):
        with tempfile.TemporaryDirectory() as run_dir:
            self.assertIsNone(dcs.archive_previous(run_dir))
            self.assertEqual(os.listdir(run_dir), [])

    def test_calling_twice_overwrites_older_archive_without_error(self):
        with tempfile.TemporaryDirectory() as run_dir:
            src = os.path.join(run_dir, dcs.FILENAME)
            with open(src, "w", encoding="utf-8") as f:
                json.dump({"gen": 1}, f)
            dcs.archive_previous(run_dir)

            with open(src, "w", encoding="utf-8") as f:
                json.dump({"gen": 2}, f)
            archived = dcs.archive_previous(run_dir)

            self.assertIsNotNone(archived)
            with open(archived, encoding="utf-8") as f:
                self.assertEqual(json.load(f), {"gen": 2})


class ViewerNeverRendersStaleCommentTests(unittest.TestCase):
    """Case H: the viewer must never show a comment stamped against a
    different version of the analysis.

    Chose the REAL viewer.generate path (not a monkeypatch): the module
    already builds happily off a minimal analysis dict (verified by hand
    before writing this test), so exercising the actual HTML string is more
    convincing than recording which claims got a `deep_check` key in memory.
    The marker checked is the commentary text itself, which only appears
    inside the "🔎 Deep check" dc-note div viewer.py renders (see
    modules/papertrail/viewer.py, the `dc = c.get("deep_check")` block).
    """

    def setUp(self):
        import deep_check
        self.deep_check = deep_check
        self.run_dir = tempfile.mkdtemp(prefix="pt-dc-viewer-")

    def tearDown(self):
        shutil.rmtree(self.run_dir, ignore_errors=True)

    def test_stale_payload_is_not_rendered(self):
        analysis = _analysis([_claim("t0", "The bridge is long.")])
        stale_analysis = _analysis([_claim("t0", "The bridge is long.")])
        results = {"t0": _result(commentary="STALE COMMENTARY MARKER")}
        stale_payload = dcs.wrap(stale_analysis, "claude-code/sonnet", results)
        # Change the current analysis so it no longer matches what was wrapped.
        analysis["text_claims"][0]["text"] = "A different claim about ferries."

        out = self.deep_check.regenerate_viewer(self.run_dir, analysis, stale_payload)
        with open(out, encoding="utf-8") as f:
            html = f.read()
        self.assertNotIn("STALE COMMENTARY MARKER", html)
        self.assertNotIn("Deep check", html)

    def test_fresh_payload_is_rendered(self):
        analysis = _analysis([_claim("t0", "The bridge is long.")])
        results = {"t0": _result(commentary="FRESH COMMENTARY MARKER")}
        fresh_payload = dcs.wrap(analysis, "claude-code/sonnet", results)

        out = self.deep_check.regenerate_viewer(self.run_dir, analysis, fresh_payload)
        with open(out, encoding="utf-8") as f:
            html = f.read()
        self.assertIn("FRESH COMMENTARY MARKER", html)
        self.assertIn("Deep check", html)


class FakeLLM:
    """Stands in for LLMClient (same pattern as tests/test_preflight.py)."""
    ping_response = "ok"

    def __init__(self, model=None, api_key=None, api_base=None):
        self.model = model or "fake/model"
        self.api_key = api_key
        self.api_base = api_base

    @staticmethod
    def _normalize_model(model):
        return model if model and "/" in model else f"gemini/{model or 'x'}"

    def call(self, prompt, temperature=0.1, max_output_tokens=8000, **kwargs):
        return self.ping_response


class EndToEndArchiveOnRerunTests(unittest.TestCase):
    """Case I: a re-run into the same output dir archives an existing
    deep_check.json before writing fresh verdicts.

    Uses verify_my_text.main() end to end (tests/test_preflight.py's fake-LLM
    pattern) because that is the code path that actually calls
    deep_check_store.archive_previous — a unit test of archive_previous alone
    (case G) does not prove verify_my_text wires it in on a real re-run.
    """

    def setUp(self):
        import verify_my_text
        self.verify_my_text = verify_my_text
        self.dir = tempfile.mkdtemp(prefix="pt-dc-archive-")
        self.out = os.path.join(self.dir, "out")
        text = os.path.join(self.dir, "my_text.md")
        with open(text, "w", encoding="utf-8") as f:
            f.write("Printing spread fast across Europe [[k1]].\n")
        with open(text + ".refs.txt", "w", encoding="utf-8") as f:
            f.write("k1 = source1.txt\n")
        src_dir = os.path.join(self.dir, "sources")
        os.mkdir(src_dir)
        with open(os.path.join(src_dir, "source1.txt"), "w", encoding="utf-8") as f:
            f.write("Printing presses appeared in many European cities. "
                    "The spread of printing was rapid.\n")
        self.argv = ["verify_my_text.py",
                     "--text", text, "--sources", src_dir,
                     "--references", text + ".refs.txt",
                     "--output-dir", self.out,
                     "--model", "gemini/gemini-2.5-flash-lite",
                     "--api-key", "fake-key", "--no-arbiter", "--yes"]

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def _run_main(self):
        with patch.object(sys, "argv", self.argv), \
             patch.object(self.verify_my_text, "LLMClient", FakeLLM), \
             redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            self.verify_my_text.main()

    def test_second_run_archives_deep_check_json(self):
        self._run_main()
        self.assertTrue(os.path.exists(os.path.join(self.out, "analysis.json")))

        dc_path = os.path.join(self.out, "deep_check.json")
        placed_content = {"model": "claude-code/sonnet",
                          "results": {"t0": {"supported": True}}}
        with open(dc_path, "w", encoding="utf-8") as f:
            json.dump(placed_content, f)

        self._run_main()  # incremental re-run into the same output dir

        prev_path = os.path.join(self.out, "deep_check_prev.json")
        self.assertFalse(os.path.exists(dc_path))
        self.assertTrue(os.path.exists(prev_path))
        with open(prev_path, encoding="utf-8") as f:
            self.assertEqual(json.load(f), placed_content)


if __name__ == "__main__":
    unittest.main()
