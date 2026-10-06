"""Task #94: the cap on how many extracted proof sentences reach the judge.

When the quick check finds no proof, the tool reads the whole source in pieces
and asks the model to copy out any sentence that supports the claim. Every
copied sentence from every piece is pooled, filtered, de-duplicated and ranked
by matcher._pool_extracted, and only the first EXTRACT_EVIDENCE_CAP of that
ranked list is shown to the judge. These tests pin both halves: the pooling
keeps and ranks everything, and the judged passage never carries more than the
cap. No API calls.

Run:  venv/bin/python3 -m unittest tests.test_evidence_cap -v
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.papertrail import matcher


class RecordingLLM:
    """Answers the extraction call with a fixed list of sentences and records the
    passage the judge was asked about."""
    def __init__(self, sentences):
        self.sentences = sentences
        self.judge_passages = []

    def call(self, prompt, **kw):
        if prompt.startswith("EXTRACT:"):
            import json
            return json.dumps({"sentences": self.sentences})
        self.judge_passages.append(prompt.split("|||")[-1].strip())
        return '{"supported": true, "reason": "stated"}'


def _source(n):
    """A source of n distinct, judge-length sentences about the same subject."""
    sents = [{"text": f"Finding number {i} shows that the treated group improved "
                      f"by {i} percent in the second year of the trial.", "page": 1}
             for i in range(n)]
    return {"title": "T", "sentences": sents}


class PoolingTests(unittest.TestCase):
    def test_pooling_keeps_every_distinct_sentence(self):
        src = _source(20)
        texts = [s["text"] for s in src["sentences"]]
        claim = "The treated group improved in the second year."
        lex = matcher._lex_scores(claim, texts)
        uniq = matcher._pool_extracted(claim, src["sentences"], texts, None, lex)
        self.assertEqual(len(uniq), 20)

    def test_pooling_drops_repeats_of_the_same_sentence(self):
        src = _source(5)
        texts = [s["text"] for s in src["sentences"]]
        claim = "The treated group improved in the second year."
        lex = matcher._lex_scores(claim, texts)
        uniq = matcher._pool_extracted(claim, src["sentences"], texts * 3, None, lex)
        self.assertEqual(len(uniq), 5)

    def test_a_sentence_strong_on_one_signal_alone_survives_the_cap(self):
        # The ranking fuses two signals: closeness in meaning (cosine) and shared
        # rare words (lexical). Sentence 15 below shares no wording with the claim
        # but is much the closest in meaning; the fusion must still carry it into
        # the eight the judge sees, which is why a lexically invisible proof
        # sentence is not lost (the run-7 t17 class).
        src = _source(20)
        texts = [s["text"] for s in src["sentences"]]
        claim = "The treated group improved in the second year."
        lex = matcher._lex_scores(claim, texts)
        row = [0.1] * 20
        row[15] = 0.99
        uniq = matcher._pool_extracted(claim, src["sentences"], texts, row, lex)
        kept = [m["j"] for m in uniq[:matcher.EXTRACT_EVIDENCE_CAP]]
        self.assertIn(15, kept)


class CapTests(unittest.TestCase):
    def test_the_judge_never_sees_more_than_the_cap(self):
        src = _source(20)
        texts = [s["text"] for s in src["sentences"]]
        llm = RecordingLLM(texts)
        matcher._extract_evidence("The treated group improved in the second year.",
                                  "pid1", src, llm,
                                  "EXTRACT: {CLAIM} ||| {SOURCE}",
                                  "JUDGE: {CLAIM} ||| {PASSAGE}")
        self.assertTrue(llm.judge_passages)
        passage = llm.judge_passages[0]
        shown = sum(1 for t in texts if t in passage)
        self.assertEqual(shown, matcher.EXTRACT_EVIDENCE_CAP)

    def test_a_short_pool_is_passed_through_whole(self):
        src = _source(3)
        texts = [s["text"] for s in src["sentences"]]
        llm = RecordingLLM(texts)
        matcher._extract_evidence("The treated group improved in the second year.",
                                  "pid1", src, llm,
                                  "EXTRACT: {CLAIM} ||| {SOURCE}",
                                  "JUDGE: {CLAIM} ||| {PASSAGE}")
        shown = sum(1 for t in texts if t in llm.judge_passages[0])
        self.assertEqual(shown, 3)

    def test_the_cap_is_the_value_the_replay_measured(self):
        # benchmarks/task94_replay/FINDINGS.md reports the measurement behind
        # this number; a change to it must be re-measured and gate-tested.
        self.assertEqual(matcher.EXTRACT_EVIDENCE_CAP, 8)


if __name__ == "__main__":
    unittest.main()
