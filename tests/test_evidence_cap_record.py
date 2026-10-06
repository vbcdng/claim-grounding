"""Task #98: every full read records what the eight-sentence cap did.

When task #94 was merged (2026-09-08) the author asked that every future run
save this information, so it can be analysed again once enough runs exist. So the
fulltext evidence entry carries an `evidence_cap` block — pool size, how many
the cap cut, and each cut sentence's rank among all source sentences by both
retrieval signals — computed from what the tool already holds, no model call,
and nothing that decides a verdict reads it.

Run:  venv/bin/python3 -m unittest tests.test_evidence_cap_record -v
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.papertrail import matcher
from tests.test_evidence_cap import RecordingLLM, _source

CAP = matcher.EXTRACT_EVIDENCE_CAP


def _pool(n, row=None):
    src = _source(n)
    texts = [s["text"] for s in src["sentences"]]
    claim = "The treated group improved in the second year."
    lex = matcher._lex_scores(claim, texts)
    pooled = matcher._pool_extracted(claim, src["sentences"], texts, row, lex)
    return src, pooled, row, lex


class CapRecordUnitTests(unittest.TestCase):
    def test_a_short_pool_records_no_cut(self):
        src, pooled, row, lex = _pool(3)
        rec = matcher._cap_record(pooled, src["sentences"], row, lex)
        self.assertEqual((rec["cap"], rec["pooled"], rec["shown"], rec["cut"]), (CAP, 3, 3, 0))
        self.assertEqual(rec["cut_sentences"], [])
        self.assertEqual(rec["n_sents_in_source"], 3)

    def test_a_long_pool_records_every_cut_sentence_with_its_ranks(self):
        row = [0.1] * 20
        row[15] = 0.99                       # sentence 15 is the cosine top-1
        src, pooled, row, lex = _pool(20, row)
        rec = matcher._cap_record(pooled, src["sentences"], row, lex)
        self.assertEqual((rec["pooled"], rec["shown"], rec["cut"]), (20, CAP, 20 - CAP))
        self.assertEqual(len(rec["cut_sentences"]), 20 - CAP)
        # pool ranks are the positions past the cap, in order
        self.assertEqual([c["pool_rank"] for c in rec["cut_sentences"]],
                         list(range(CAP, 20)))
        # every cut sentence maps to a source sentence and has both ranks
        for c in rec["cut_sentences"]:
            self.assertGreaterEqual(c["j"], 0)
            self.assertTrue(0 <= c["lex_rank"] < 20)
            self.assertTrue(0 <= c["cos_rank"] < 20)
            self.assertTrue(c["text"])
        # the cosine top-1 was kept, so it is not among the cut
        self.assertNotIn(15, [c["j"] for c in rec["cut_sentences"]])

    def test_ranks_follow_the_replay_convention(self):
        # 0-based rank among ALL source sentences, -1 when the hit maps to no
        # sentence — the convention of benchmarks/task94_replay, so both
        # measurements can be read side by side.
        src, pooled, row, lex = _pool(12)
        pooled = pooled + [{"j": -1, "text": "an unmapped condensation"}]
        rec = matcher._cap_record(pooled, src["sentences"], None, lex)
        last = rec["cut_sentences"][-1]
        self.assertEqual((last["j"], last["lex_rank"], last["cos_rank"]), (-1, -1, -1))
        best_lex = min(range(12), key=lambda j: -lex[j])
        mapped = [c for c in rec["cut_sentences"] if c["j"] >= 0]
        self.assertTrue(all(c["cos_rank"] == -1 for c in mapped))   # no cosine row given
        self.assertTrue(all(c["lex_rank"] != 0 or c["j"] == best_lex for c in mapped))


class CapRecordEndToEndTests(unittest.TestCase):
    def test_the_fulltext_evidence_entry_carries_the_record(self):
        src = _source(20)
        texts = [s["text"] for s in src["sentences"]]
        llm = RecordingLLM(texts)
        e = matcher._extract_evidence("The treated group improved in the second year.",
                                      "pid1", src, llm,
                                      "EXTRACT: {CLAIM} ||| {SOURCE}",
                                      "JUDGE: {CLAIM} ||| {PASSAGE}")
        rec = e["evidence_cap"]
        self.assertEqual((rec["pooled"], rec["shown"], rec["cut"]), (20, CAP, 20 - CAP))
        # the judged passage is unchanged by the bookkeeping: still exactly CAP sentences
        shown = sum(1 for t in texts if t in llm.judge_passages[0])
        self.assertEqual(shown, CAP)
        # the shown sentences and the cut sentences partition the pool
        cut_texts = {c["text"] for c in rec["cut_sentences"]}
        self.assertEqual(len(cut_texts), 20 - CAP)
        self.assertTrue(all(t not in llm.judge_passages[0] for t in cut_texts))

    def test_a_pool_that_fits_records_zero_cut(self):
        src = _source(3)
        texts = [s["text"] for s in src["sentences"]]
        llm = RecordingLLM(texts)
        e = matcher._extract_evidence("The treated group improved in the second year.",
                                      "pid1", src, llm,
                                      "EXTRACT: {CLAIM} ||| {SOURCE}",
                                      "JUDGE: {CLAIM} ||| {PASSAGE}")
        self.assertEqual(e["evidence_cap"]["cut"], 0)


if __name__ == "__main__":
    unittest.main()
