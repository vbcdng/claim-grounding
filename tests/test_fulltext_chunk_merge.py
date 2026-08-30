"""Task #50 chunk merging on the full-text extraction fallback: consecutive kept
~1200-word chunks are packed into ONE extraction call up to EXTRACT_MERGE_WORDS
words (typically 2 chunks; a short tail chunk may ride along). The extractor
reads exactly the same text as before — no chunk is ever dropped (a replay of
every logged run killed all skip-chunks rules, benchmarks/task50_replay/) — in
roughly half as many calls. Only the fulltext-fallback call site passes a cap;
merge_words=None (the default every other call site uses) is byte-identical to
the old one-call-per-chunk behavior. No API calls.

Run:  venv/bin/python3 -m unittest tests.test_fulltext_chunk_merge -v
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.papertrail import matcher


class CountingLLM:
    """Returns 'nothing found' for extraction and 'unsupported' for judging,
    recording every extraction prompt so tests can count and inspect them."""
    def __init__(self):
        self.extract_prompts = []

    def call(self, prompt, **kw):
        if prompt.startswith("EXTRACT:"):
            self.extract_prompts.append(prompt)
            return '{"sentences": []}'
        return '{"supported": false, "reason": "not stated"}'


def _mk_source(n_chunks, words_per_sentence=40):
    """A source whose sentences pack exactly `n_chunks` 1200-word chunks; sentence
    texts are neutral filler with no overlap with the test claims."""
    per_chunk = matcher.EXTRACT_CHUNK_WORDS // words_per_sentence
    sents = []
    for c in range(n_chunks):
        for s in range(per_chunk):
            word = f"filler{c}x{s}"
            sents.append({"text": " ".join([word] * words_per_sentence), "page": 1})
    return {"title": "T", "sentences": sents}, per_chunk


def _extract(src, row, merge_words, claim="Quarterly revenue grew nine percent."):
    llm = CountingLLM()
    matcher._extract_evidence(claim, "pid1", src, llm,
                              "EXTRACT: {CLAIM} ||| {SOURCE}",
                              "JUDGE: {CLAIM} ||| {PASSAGE}",
                              row=row, merge_words=merge_words)
    return llm


class MergeTests(unittest.TestCase):
    def test_no_cap_reads_one_call_per_chunk(self):
        src, per_chunk = _mk_source(4)
        row = [0.1] * len(src["sentences"])
        llm = _extract(src, row, merge_words=None)
        # old behavior: 4 chunks -> 4 calls, +1 retry on the first (nothing found)
        self.assertEqual(len(llm.extract_prompts), 5)

    def test_cap_halves_the_calls_and_drops_no_text(self):
        src, per_chunk = _mk_source(4)
        row = [0.1] * len(src["sentences"])
        llm = _extract(src, row, merge_words=matcher.EXTRACT_MERGE_WORDS)
        # 4 chunks packed into 2 calls (2 chunks each), +1 single-chunk retry
        self.assertEqual(len(llm.extract_prompts), 3)
        sent = "".join(llm.extract_prompts[:2])
        for c in range(4):                       # every chunk's text still sent
            self.assertIn(f"filler{c}x0", sent)

    def test_merged_call_keeps_document_order(self):
        src, per_chunk = _mk_source(4)
        row = [0.1] * len(src["sentences"])
        llm = _extract(src, row, merge_words=matcher.EXTRACT_MERGE_WORDS)
        first = llm.extract_prompts[0]
        self.assertIn("filler0x0", first)
        self.assertIn("filler1x0", first)
        self.assertLess(first.index("filler0x0"), first.index("filler1x0"))

    def test_empty_retry_uses_a_single_unmerged_chunk(self):
        src, per_chunk = _mk_source(4)
        row = [0.1] * len(src["sentences"])
        llm = _extract(src, row, merge_words=matcher.EXTRACT_MERGE_WORDS)
        retry = llm.extract_prompts[-1]
        self.assertIn("filler0x0", retry)        # the most relevant chunk alone
        self.assertNotIn("filler1x0", retry)

    def test_short_tail_chunk_rides_along(self):
        # 2 full 1200-word chunks + a 120-word tail chunk: 2400 + 120 = 2520
        # words, under the 2600 cap, so all three pack into ONE call.
        src, per_chunk = _mk_source(2)
        src["sentences"].append({"text": " ".join(["tailword"] * 120), "page": 1})
        row = [0.1] * len(src["sentences"])
        llm = _extract(src, row, merge_words=matcher.EXTRACT_MERGE_WORDS)
        self.assertEqual(len(llm.extract_prompts), 2)   # 1 merged call + 1 retry
        self.assertIn("tailword", llm.extract_prompts[0])

    def test_long_doc_cap_still_applies_before_merging(self):
        n = matcher.EXTRACT_TOP_CHUNKS + 4
        src, per_chunk = _mk_source(n)
        row = [0.1] * len(src["sentences"])      # uniform -> stable top-6 = chunks 0-5
        llm = _extract(src, row, merge_words=matcher.EXTRACT_MERGE_WORDS)
        # 6 kept chunks packed into 3 calls, +1 retry
        self.assertEqual(len(llm.extract_prompts), 4)
        sent = "".join(llm.extract_prompts)
        self.assertNotIn(f"filler{matcher.EXTRACT_TOP_CHUNKS}x0", sent)

    def test_default_parameter_is_none(self):
        import inspect
        sig = inspect.signature(matcher._extract_evidence)
        self.assertIsNone(sig.parameters["merge_words"].default)

    def test_merging_is_off_by_default(self):
        # The 2026-08-19/20 gate pair proved merging costs verdicts (paper1 23/27
        # with it, 25/27 without), so the shipped fallback path must stay one call
        # per chunk. PT_EXTRACT_MERGE=1 re-enables it for a benchmark arm only.
        self.assertFalse(matcher.EXTRACT_MERGE_ON)
        src, per_chunk = _mk_source(4)
        row = [0.1] * len(src["sentences"])
        off = matcher.EXTRACT_MERGE_WORDS if matcher.EXTRACT_MERGE_ON else None
        llm = _extract(src, row, merge_words=off)
        self.assertEqual(len(llm.extract_prompts), 5)   # 4 chunks + 1 retry


if __name__ == "__main__":
    unittest.main()
