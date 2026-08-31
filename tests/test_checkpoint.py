"""Save-as-you-go checkpoint (2026-08-10): journal + recovery guards.

The journal exists so a power cut mid-run costs minutes, not the whole run
(the 2026-08-09 battery death lost 69 of 81 judged claims). These tests pin
the safety properties: recovery only under the exact same configuration,
truncated tails dropped, outage verdicts never recovered, edited claims
re-judged."""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from modules.papertrail import checkpoint, rerun

HASHES = {"a.pdf": "sha-a", "b.txt": "sha-b"}


def _claim(cid, text, verdict="supported", **extra):
    return {"id": cid, "text": text, "markers": ["k1"], "verdict": verdict,
            "method": "retrieval", "reason": "ok", **extra}


class CheckpointTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "checkpoint.jsonl")

    def tearDown(self):
        self.tmp.cleanup()

    def _write_journal(self, claims, model="m1", text_sha1="t1", hashes=HASHES):
        w = checkpoint.Writer(self.path, model, text_sha1, hashes)
        for c in claims:
            w(c)
        w.close()

    def test_round_trip(self):
        claims = [_claim("c1", "First claim."),
                  _claim("c2", "Second claim.", "unsupported")]
        self._write_journal(claims)
        self.assertEqual(checkpoint.load(self.path, "m1", "t1", HASHES), claims)

    def test_configuration_mismatch_discards_everything(self):
        self._write_journal([_claim("c1", "First claim.")])
        cases = [("OTHER", "t1", HASHES),             # different judge model
                 ("m1", "OTHER", HASHES),             # the text was edited
                 ("m1", "t1", {"a.pdf": "CHANGED"})]  # a source file changed
        for model, sha, hashes in cases:
            self.assertEqual(checkpoint.load(self.path, model, sha, hashes), [],
                             msg=f"{model}/{sha}/{hashes} must discard the journal")

    def test_missing_file_is_empty(self):
        self.assertEqual(
            checkpoint.load(os.path.join(self.tmp.name, "nope.jsonl"),
                            "m1", "t1", HASHES), [])

    def test_truncated_tail_dropped_whole_lines_kept(self):
        self._write_journal([_claim("c1", "First claim."),
                             _claim("c2", "Second claim.")])
        with open(self.path, "a", encoding="utf-8") as f:
            f.write('{"id": "c3", "text": "cut off by the powe')
        got = checkpoint.load(self.path, "m1", "t1", HASHES)
        self.assertEqual([c["id"] for c in got], ["c1", "c2"])

    def test_writer_truncates_previous_journal(self):
        self._write_journal([_claim("old", "Old run claim.")])
        self._write_journal([_claim("new", "New run claim.")])
        got = checkpoint.load(self.path, "m1", "t1", HASHES)
        self.assertEqual([c["id"] for c in got], ["new"])

    def test_outage_verdicts_never_pass_the_reuse_guard(self):
        # Recovery routes every journaled claim through rerun.reusable() — the
        # same rule that keeps incremental runs from freezing an outage verdict.
        dead = _claim("c1", "First claim.", "unsupported",
                      reason="no LLM response -> treated as unsupported")
        flagged = _claim("c2", "Second claim.", "unsupported", judge_error=True)
        good = _claim("c3", "Third claim.", "unsupported", reason="not in source")
        self.assertFalse(rerun.reusable(dead))
        self.assertFalse(rerun.reusable(flagged))
        self.assertTrue(rerun.reusable(good))

    def test_recovered_claims_match_by_text_and_markers(self):
        prev = [_claim("old1", "Alpha claim."), _claim("old2", "Beta claim.")]
        new = [{"id": "n1", "text": "Alpha claim.", "markers": ["k1"]},
               {"id": "n2", "text": "EDITED beta claim entirely different.",
                "markers": ["k1"]}]
        matched = rerun.match_claims(prev, new)
        self.assertEqual(matched["n1"]["reuse"]["id"], "old1")
        self.assertIsNone(matched["n2"]["reuse"])   # edited text is re-judged


if __name__ == "__main__":
    unittest.main()


class CheckpointPdfReaderGuard(unittest.TestCase):
    """Task #71: the journal header records WHICH library read the PDFs.

    Source files are compared by bytes, so a changed PDF library is invisible to
    the model/text/source-hash guards while every sentence read out of those
    files may have moved. A journal written under a different reader holds
    verdicts this run cannot reproduce, so it is ignored."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "checkpoint.jsonl")

    def tearDown(self):
        self.tmp.cleanup()

    def _journal(self, reader):
        w = checkpoint.Writer(self.path, "m1", "t1", HASHES, reader)
        w(_claim("c1", "one"))
        w.close()

    def test_same_reader_recovers(self):
        self._journal("pypdf 6.16.2")
        got = checkpoint.load(self.path, "m1", "t1", HASHES, "pypdf 6.16.2")
        self.assertEqual([c["id"] for c in got], ["c1"])

    def test_different_reader_is_ignored(self):
        self._journal("PyPDF2 3.0.1")
        got = checkpoint.load(self.path, "m1", "t1", HASHES, "pypdf 6.16.2")
        self.assertEqual(got, [])

    def test_untracked_reader_is_discarded(self):
        # a journal written before task #71 has no source_reader field. Author
        # ruling 2026-08-31 ("option B"): that is not unknown, it is known to be
        # PyPDF2, so the journal cannot be reproduced and is dropped.
        self._journal("")
        got = checkpoint.load(self.path, "m1", "t1", HASHES, "pypdf 6.16.2")
        self.assertEqual(got, [])

    def test_untracked_reader_recovers_on_the_old_reader(self):
        self._journal("")
        got = checkpoint.load(self.path, "m1", "t1", HASHES, rerun.PRE_TRACKING_READER)
        self.assertEqual([c["id"] for c in got], ["c1"])

    def test_plain_text_project_is_exempt(self):
        # no PDF among the sources, so the reader cannot have changed anything
        txt_only = {"a.txt": "sha-a"}
        w = checkpoint.Writer(self.path, "m1", "t1", txt_only, "")
        w(_claim("c1", "one"))
        w.close()
        got = checkpoint.load(self.path, "m1", "t1", txt_only, "pypdf 6.16.2")
        self.assertEqual([c["id"] for c in got], ["c1"])

    def test_caller_that_names_no_reader_is_unaffected(self):
        # back-compat: an older caller passing four arguments skips the check
        self._journal("pypdf 6.16.2")
        got = checkpoint.load(self.path, "m1", "t1", HASHES)
        self.assertEqual([c["id"] for c in got], ["c1"])
