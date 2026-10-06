"""Offline tests for the task #15 labeling panel (no API calls, no network).

Covers the hard requirements:
 1. a refused call is recorded as answered=False, never as a verdict;
 2. proof quotes are verbatim-checked against the source (with PDF-artifact
    tolerance) and the result recorded per part;
 3. the funnel sorts unanimous / split / insufficient correctly and never
    counts a refusal as a vote;
 4. sectioned reading (opt-in) never judges on part of a source: every piece
    must be read, an invented scan quote is thrown away, and the verdict is
    stamped with the reading mode so the funnel can caution about it;
 5. (2026-09-04 review rulings) a vote that disagrees with its own written
    note or its own part list is not counted, and an agreement that rests on
    a piece-by-piece "the source never says this" answer proposes no label.
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from benchmarks.labeler.panel_runner import (
    run_panel, quote_in_sources, validate_verdict, split_into_pieces,
    plan_pieces, filter_rows)
from benchmarks.labeler.funnel import (
    sort_rows, vote_contradicts_itself, label_from_parts, write_summary)
from benchmarks.labeler.compare_read_modes import compare as compare_read_modes
from benchmarks.labeler.compare_piece_read import (
    load_rulings, score_against_rulings, write_report)

SOURCE_TEXT = ("The study followed 4,000 adults for ten years. "
               "Egg consumption was associated with a 42% higher risk. "
               "The liver responds by suppression of cholesterol synthesis.")

ROW = {"row_id": "test:r1", "pile": "retreat_contested",
       "claim_text": "Eggs raise risk by 42%.", "context": "…",
       "old_label": "contested",
       "sources": [{"name": "src.txt", "chars": len(SOURCE_TEXT),
                    "garble_ratio": 0.0, "text": SOURCE_TEXT}]}

TEMPLATE = "CLAIM {CLAIM} CONTEXT {CONTEXT} SOURCES {SOURCES}"


def verdict_json(label, quote):
    return json.dumps({"strict_label": label,
                       "parts": [{"part": "42% higher risk",
                                  "classification": "proven",
                                  "quote": quote, "source": "src.txt"}],
                       "hard_note": None})


class StubClient:
    """Returns canned responses in order; None models a refused call."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def call(self, prompt, temperature=0.1, max_output_tokens=8000,
             purpose="untagged", claim_id=None):
        self.calls += 1
        return self.responses.pop(0) if self.responses else None


class TestPanelRunner(unittest.TestCase):

    def _run(self, factory, models, rows=None, out=None):
        if out is None:
            out = os.path.join(self.tmp.name, "verdicts.jsonl")
        counts = run_panel(rows or [ROW], models, out, client_factory=factory,
                           template=TEMPLATE, progress=lambda *_: None)
        with open(out) as f:
            recs = [json.loads(l) for l in f if l.strip()]
        return counts, recs

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def test_refusal_recorded_as_no_answer_never_a_verdict(self):
        counts, recs = self._run(lambda m: StubClient([None]), ["m1"])
        self.assertEqual(counts, {"judged": 0, "refused": 1, "skipped": 0})
        self.assertFalse(recs[0]["answered"])
        self.assertNotIn("strict_label", recs[0])

    def test_good_verdict_with_verbatim_quote_verifies(self):
        good = verdict_json(
            "pass", "Egg consumption was associated with a 42% higher risk.")
        counts, recs = self._run(lambda m: StubClient([good]), ["m1"])
        self.assertEqual(counts["judged"], 1)
        part = recs[0]["parts"][0]
        self.assertTrue(part["quote_verified"])
        self.assertEqual(part["quote_verified_in"], "src.txt")

    def test_fabricated_quote_marked_unverified_but_verdict_kept(self):
        bad = verdict_json(
            "pass", "Eggs were proven entirely harmless in all cohorts.")
        _, recs = self._run(lambda m: StubClient([bad]), ["m1"])
        self.assertTrue(recs[0]["answered"])
        self.assertFalse(recs[0]["parts"][0]["quote_verified"])

    def test_malformed_json_retried_once_then_no_answer(self):
        stub = StubClient(["not json at all", "{\"strict_label\": \"banana\"}"])
        counts, recs = self._run(lambda m: stub, ["m1"])
        self.assertEqual(stub.calls, 2)
        self.assertEqual(counts["refused"], 1)
        self.assertFalse(recs[0]["answered"])
        self.assertIn("malformed", recs[0]["reason"])

    def test_oversized_source_becomes_no_answer_not_truncated(self):
        big_row = dict(ROW, sources=[{"name": "big.txt", "chars": 1000,
                                      "garble_ratio": 0.0, "text": "x" * 1000}])
        stub = StubClient(["should never be called"])
        out = os.path.join(self.tmp.name, "verdicts.jsonl")
        counts = run_panel([big_row], ["m1"], out, client_factory=lambda m: stub,
                           template=TEMPLATE, progress=lambda *_: None,
                           max_prompt_chars=500)
        self.assertEqual(stub.calls, 0)
        self.assertEqual(counts["refused"], 1)
        with open(out) as f:
            rec = json.loads(f.readline())
        self.assertFalse(rec["answered"])
        self.assertIn("source too long", rec["reason"])

    def test_resume_skips_already_judged_pairs(self):
        good = verdict_json(
            "pass", "The study followed 4,000 adults for ten years.")
        out = os.path.join(self.tmp.name, "verdicts.jsonl")
        self._run(lambda m: StubClient([good]), ["m1"], out=out)
        counts, _ = self._run(lambda m: StubClient([good]), ["m1"], out=out)
        self.assertEqual(counts, {"judged": 0, "refused": 0, "skipped": 1})


class TestQuoteGateAndValidation(unittest.TestCase):

    def test_quote_check_tolerates_pdf_linebreak_artifacts(self):
        src = [{"name": "s", "text": ("the CRL4DCAF1 E3 ubiquitin ligase, leading "
                                      "to proteasome- dependent degradation of the protein.")}]
        self.assertEqual(quote_in_sources(
            "leading to proteasome-dependent degradation of the protein", src), "s")
        self.assertIsNone(quote_in_sources(
            "a sentence that is nowhere in the source at all", src))

    def test_validate_verdict_rejects_bad_shapes(self):
        self.assertIsNone(validate_verdict(None)[0])
        self.assertIsNone(validate_verdict({"strict_label": "pass", "parts": []})[0])
        self.assertIsNone(validate_verdict({"strict_label": "maybe", "parts": [{}]})[0])
        ok, err = validate_verdict({"strict_label": "fail_unproven",
                                    "parts": [{"part": "x",
                                               "classification": "unproven",
                                               "quote": None}]})
        self.assertIsNone(err)
        self.assertEqual(ok["strict_label"], "fail_unproven")


PROOF = "Egg consumption was associated with a 42% higher risk."
LONG_SOURCE = ("Filler sentence about the methods. " * 5
               + PROOF + " "
               + "More filler about the discussion. " * 5)
LONG_ROW = dict(ROW, row_id="test:long",
                sources=[{"name": "long.txt", "chars": len(LONG_SOURCE),
                          "garble_ratio": 0.0, "text": LONG_SOURCE}])
SCAN_TEMPLATE = ("SCAN {CLAIM} piece {PIECE_NO}/{N_PIECES} of {SOURCE_NAME} "
                 "::: {PIECE}")


class ScanStub:
    """Answers scan calls from what the piece actually contains, and the final
    judging call with a canned verdict — so the test exercises the real
    split / verify / digest path."""

    def __init__(self, verdict=None, scan_quote=None, fail_piece=None):
        self.verdict = verdict if verdict is not None else verdict_json(
            "pass", PROOF)
        self.scan_quote = scan_quote  # None = copy honestly from the piece
        self.fail_piece = fail_piece  # piece number whose calls all fail
        self.scan_calls = 0
        self.judge_calls = 0

    def call(self, prompt, temperature=0.1, max_output_tokens=8000,
             purpose="untagged", claim_id=None):
        if prompt.startswith("SCAN "):
            self.scan_calls += 1
            piece_no = int(prompt.split("piece ")[1].split("/")[0])
            if self.fail_piece == piece_no:
                return None
            quote = self.scan_quote
            if quote is None:
                quote = PROOF if PROOF in prompt else None
            if quote is None:
                return json.dumps({"found": []})
            return json.dumps({"found": [{"quote": quote,
                                          "relation": "supports"}]})
        self.judge_calls += 1
        return self.verdict


class TestSectionedReading(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self, stub, sectioned=None, row=None):
        out = os.path.join(self.tmp.name, "verdicts.jsonl")
        counts = run_panel([row or LONG_ROW], ["m1"], out,
                           client_factory=lambda m: stub, template=TEMPLATE,
                           scan_template=SCAN_TEMPLATE,
                           progress=lambda *_: None, max_prompt_chars=150,
                           sectioned=sectioned or {"piece_chars": 100,
                                                   "overlap": 20})
        with open(out) as f:
            recs = [json.loads(l) for l in f if l.strip()]
        return counts, recs[0]

    def test_split_covers_every_character_and_overlaps(self):
        text = "".join(str(i % 10) for i in range(1000))
        pieces = split_into_pieces(text, piece_chars=100, overlap_chars=20)
        self.assertGreater(len(pieces), 1)
        # nothing is lost: dropping each piece's overlap rebuilds the source
        rebuilt = pieces[0] + "".join(p[20:] for p in pieces[1:])
        self.assertEqual(rebuilt, text)
        # neighbouring pieces really share the overlap
        self.assertEqual(pieces[0][-20:], pieces[1][:20])

    def test_short_source_is_one_piece(self):
        self.assertEqual(split_into_pieces("short text", 100, 20),
                         ["short text"])

    def test_sectioned_read_produces_a_stamped_verdict(self):
        stub = ScanStub()
        counts, rec = self._run(stub)
        self.assertEqual(counts["judged"], 1)
        self.assertEqual(rec["read_mode"], "sectioned")
        self.assertEqual(rec["pieces"], stub.scan_calls)
        self.assertEqual(stub.judge_calls, 1)
        self.assertGreaterEqual(rec["scan_quotes_kept"], 1)
        # the final quote is still checked against the FULL original source
        self.assertTrue(rec["parts"][0]["quote_verified"])

    def test_an_unread_piece_means_no_verdict(self):
        stub = ScanStub(fail_piece=2)
        counts, rec = self._run(stub)
        self.assertEqual(counts["refused"], 1)
        self.assertFalse(rec["answered"])
        self.assertNotIn("strict_label", rec)
        self.assertIn("sectioned read incomplete", rec["reason"])
        self.assertEqual(stub.judge_calls, 0)

    def test_invented_scan_quote_is_thrown_away(self):
        stub = ScanStub(scan_quote="A sentence that is nowhere in this source.")
        _, rec = self._run(stub)
        self.assertGreater(rec["scan_quotes_returned"], 0)
        self.assertEqual(rec["scan_quotes_kept"], 0)

    def test_sectioning_is_opt_in(self):
        stub = ScanStub()
        out = os.path.join(self.tmp.name, "off.jsonl")
        run_panel([LONG_ROW], ["m1"], out, client_factory=lambda m: stub,
                  template=TEMPLATE, progress=lambda *_: None,
                  max_prompt_chars=150)
        with open(out) as f:
            rec = json.loads(f.readline())
        self.assertEqual(stub.scan_calls, 0)
        self.assertFalse(rec["answered"])
        self.assertIn("source too long", rec["reason"])
        self.assertEqual(rec["read_mode"], "whole_source")

    def test_only_filter_keeps_named_rows_and_refuses_unknown_ids(self):
        rows = [dict(ROW, row_id="a"), dict(ROW, row_id="b"),
                dict(ROW, row_id="c")]
        self.assertEqual([r["row_id"] for r in filter_rows(rows, ["c", "a"])],
                         ["a", "c"])
        with self.assertRaises(SystemExit):
            filter_rows(rows, ["a", "zz"])

    def test_control_comparison_counts_agreement(self):
        whole = {("r1", "m1"): {"row_id": "r1", "model": "m1", "answered": True,
                                "strict_label": "pass"},
                 ("r2", "m1"): {"row_id": "r2", "model": "m1", "answered": True,
                                "strict_label": "pass"}}
        sectioned = {("r1", "m1"): {"row_id": "r1", "model": "m1",
                                    "answered": True, "strict_label": "pass",
                                    "pieces": 2, "scan_quotes_kept": 3},
                     ("r2", "m1"): {"row_id": "r2", "model": "m1",
                                    "answered": True,
                                    "strict_label": "fail_unproven",
                                    "pieces": 2, "scan_quotes_kept": 0}}
        out = {r["row_id"]: r for r in compare_read_modes(whole, sectioned)}
        self.assertTrue(out["r1"]["agree"])
        self.assertFalse(out["r2"]["agree"])

    def test_plan_pieces_lists_every_call(self):
        plan = plan_pieces(LONG_ROW, 100, 20)
        self.assertEqual(len(plan), len(split_into_pieces(LONG_SOURCE, 100, 20)))
        self.assertEqual(plan[0][0], "long.txt")
        self.assertEqual(plan[0][1], 1)


def _verdict(row_id, model, label, answered=True, read_mode=None):
    rec = {"row_id": row_id, "model": model, "answered": answered}
    if answered:
        rec.update({"strict_label": label, "parts": []})
        if read_mode:
            rec["read_mode"] = read_mode
    else:
        rec["reason"] = "refused"
    return rec


class TestFunnel(unittest.TestCase):

    def test_sorts_unanimous_split_insufficient_and_ignores_refusals(self):
        rows = [dict(ROW, row_id="test:r%d" % i) for i in (1, 2, 3)]
        verdicts = [
            _verdict("test:r1", "m1", "pass"),
            _verdict("test:r1", "m2", "pass"),
            _verdict("test:r1", "m3", None, answered=False),
            _verdict("test:r2", "m1", "pass"),
            _verdict("test:r2", "m2", "fail_unproven"),
            _verdict("test:r3", "m1", "pass"),
            _verdict("test:r3", "m2", None, answered=False),
        ]
        out = {r["row_id"]: r for r in sort_rows(rows, verdicts)}
        self.assertEqual(out["test:r1"]["status"], "unanimous")
        self.assertEqual(out["test:r1"]["proposed_label"], "pass")
        self.assertEqual(out["test:r1"]["refusals"], ["m3"])
        self.assertEqual(out["test:r2"]["status"], "split")
        self.assertIsNone(out["test:r2"]["proposed_label"])
        # one answer + one refusal is NOT enough to propose anything
        self.assertEqual(out["test:r3"]["status"], "insufficient")

    def test_marks_a_piece_by_piece_reading_and_cautions_on_unproven(self):
        rows = [dict(ROW, row_id="test:r%d" % i) for i in (1, 2)]
        verdicts = [
            _verdict("test:r1", "m1", "fail_unproven"),
            _verdict("test:r1", "m2", "fail_unproven", read_mode="sectioned"),
            _verdict("test:r2", "m1", "pass"),
            _verdict("test:r2", "m2", "pass", read_mode="sectioned"),
        ]
        out = {r["row_id"]: r for r in sort_rows(rows, verdicts)}
        self.assertEqual(out["test:r1"]["read_modes"]["m2"], "sectioned")
        # round-1 verdicts carry no read_mode; they were whole-source reads
        self.assertEqual(out["test:r1"]["read_modes"]["m1"], "whole_source")
        # "the source never says it" from a piece-by-piece read needs care
        self.assertEqual(out["test:r1"]["sectioned_unproven"], ["m2"])
        # a "pass" from the same reading needs no such caution
        self.assertEqual(out["test:r2"]["sectioned_unproven"], [])


# The Gemma vote stored for retreat:b128 on 2026-09-04. Its label field says
# "pass" while its own last sentence says the label must be fail_unproven.
B128_NOTE = (
    "The first sentence 'The disagreement runs deeper than calibration' is not "
    "in the source text, but the prompt asks if the claim is true as written. "
    "However, looking at the provided source, the sentence 'Their disagreement "
    "about AI risk is deeper than the question of whether a very small number "
    "of humans will survive an AI catastrophe' exists, but not the calibration "
    "sentence. Wait, I must mark it unproven. But the rubric says 'every part "
    "proven or tolerated -> pass'. If one part is unproven, it is "
    "'fail_unproven'. I will re-evaluate. The first sentence is an assertion "
    "not found in the text. Therefore, the label must be fail_unproven.")

# Two real notes from votes that ARE consistent with themselves: both are
# labelled pass and both use the words "unproven"/"unsupported" while
# explaining why the part was tolerated instead. Neither may be discarded.
B008_NOTE = (
    "The two lead-in framing sentences are the essay's own analytical bridge "
    "rather than a direct restatement of a source sentence, but they closely "
    "paraphrase the paper's core thesis and are not contradicted, so I treated "
    "them as tolerated paraphrase rather than unproven assertions.")
B137_NOTE = (
    "The word 'every' asserts exhaustiveness that the source doesn't explicitly "
    "claim, but the source itself frames the 14 models as spanning the full "
    "breadth of forecasting paradigms, so this reads as reasonable summary "
    "language rather than an unsupported new fact.")


class TestSelfContradictingVote(unittest.TestCase):
    """A vote whose own words or own part list point at a different label than
    the label it gave is evidence of nothing: it is not counted, and the row
    goes to the author with the vote quoted (review ruling, 2026-09-04)."""

    def test_b128_note_contradicts_its_pass_label(self):
        reason = vote_contradicts_itself(
            {"answered": True, "strict_label": "pass", "hard_note": B128_NOTE,
             "parts": []})
        self.assertIsNotNone(reason)
        self.assertIn("written note", reason)

    def test_hedging_notes_that_merely_use_the_word_unproven_are_kept(self):
        for note in (B008_NOTE, B137_NOTE):
            self.assertIsNone(vote_contradicts_itself(
                {"answered": True, "strict_label": "pass", "hard_note": note,
                 "parts": []}))

    def test_a_failure_note_under_a_failure_label_is_kept(self):
        note = ("The source never names Drago and Laine, so that attributed "
                "claim is unproven rather than contradicted.")
        self.assertIsNone(vote_contradicts_itself(
            {"answered": True, "strict_label": "fail_unproven",
             "hard_note": note, "parts": []}))

    def test_a_pass_conclusion_under_a_failure_label_is_caught(self):
        reason = vote_contradicts_itself(
            {"answered": True, "strict_label": "fail_unproven",
             "hard_note": "Every part checks out, so the label must be pass.",
             "parts": []})
        self.assertIsNotNone(reason)

    def test_part_list_forces_the_label_the_rubric_derives(self):
        self.assertEqual(label_from_parts(
            [{"classification": "proven"}, {"classification": "tolerated"}]),
            "pass")
        self.assertEqual(label_from_parts(
            [{"classification": "proven"}, {"classification": "unproven"}]),
            "fail_unproven")
        self.assertEqual(label_from_parts(
            [{"classification": "unproven"},
             {"classification": "contradicted"}]), "fail_contradicted")
        # no parts recorded: nothing to check against, so no complaint
        self.assertIsNone(label_from_parts([]))

    def test_pass_label_over_an_unproven_part_is_not_counted(self):
        reason = vote_contradicts_itself(
            {"answered": True, "strict_label": "pass", "hard_note": "",
             "parts": [{"classification": "proven"},
                       {"classification": "unproven"}]})
        self.assertIsNotNone(reason)
        self.assertIn("list of parts", reason)

    def test_a_refusal_is_never_called_self_contradicting(self):
        self.assertIsNone(vote_contradicts_itself(
            {"answered": False, "reason": "refused"}))

    def test_the_row_goes_to_the_author_with_the_vote_quoted(self):
        rows = [dict(ROW, row_id="retreat:b128")]
        verdicts = [
            {"row_id": "retreat:b128", "model": "claude-code/sonnet",
             "answered": True, "strict_label": "pass", "parts": []},
            {"row_id": "retreat:b128", "model": "gemini/gemma-4-31b-it",
             "answered": True, "strict_label": "pass", "hard_note": B128_NOTE,
             "read_mode": "sectioned", "parts": []},
        ]
        out = sort_rows(rows, verdicts)[0]
        self.assertEqual(out["status"], "self_contradicting")
        self.assertIsNone(out["proposed_label"])
        # the contradicting vote is out of the vote count entirely
        self.assertEqual(list(out["votes"]), ["claude-code/sonnet"])
        self.assertEqual(len(out["self_contradicting_votes"]), 1)
        self.assertEqual(out["self_contradicting_votes"][0]["note"], B128_NOTE)
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "s.md")
            write_summary([out], verdicts, path)
            text = open(path).read()
        self.assertIn("Rows with a vote whose reasoning contradicts its label",
                      text)
        self.assertIn("Wait, I must mark it unproven", text)
        self.assertNotIn("proposed label:", text)


class TestPieceReadSilenceProposesNoLabel(unittest.TestCase):
    """The 2026-09-04 control run turned two of three passes into "the source
    is silent" when the same model read in pieces instead of whole, so such a
    vote may not carry a proposed label (review ruling, 2026-09-04)."""

    def _row(self, row_id, label_a, label_b, read_mode_b="sectioned"):
        rows = [dict(ROW, row_id=row_id)]
        verdicts = [_verdict(row_id, "m1", label_a),
                    _verdict(row_id, "m2", label_b, read_mode=read_mode_b)]
        return sort_rows(rows, verdicts)[0], verdicts

    def test_agreed_silence_from_a_piece_read_is_held_back(self):
        out, verdicts = self._row("test:r1", "fail_unproven", "fail_unproven")
        self.assertEqual(out["status"], "needs_whole_reading")
        self.assertIsNone(out["proposed_label"])
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "s.md")
            write_summary([out], verdicts, path)
            text = open(path).read()
        self.assertIn("Needs a whole reading before any label is proposed", text)
        self.assertIn("CAUTION", text)

    def test_agreed_pass_from_a_piece_read_still_proposes_a_label(self):
        out, _ = self._row("test:r2", "pass", "pass")
        self.assertEqual(out["status"], "unanimous")
        self.assertEqual(out["proposed_label"], "pass")

    def test_agreed_contradiction_from_a_piece_read_still_proposes_a_label(self):
        # finding a sentence that says something different is positive
        # evidence; a piece-by-piece reading cannot invent one.
        out, _ = self._row("test:r3", "fail_contradicted", "fail_contradicted")
        self.assertEqual(out["status"], "unanimous")
        self.assertEqual(out["proposed_label"], "fail_contradicted")

    def test_agreed_silence_from_two_whole_readings_is_a_proposal(self):
        out, _ = self._row("test:r4", "fail_unproven", "fail_unproven",
                           read_mode_b="whole_source")
        self.assertEqual(out["status"], "unanimous")
        self.assertEqual(out["proposed_label"], "fail_unproven")


# ---------- piece-read mode (task #105) ----------

FILLER = "The committee met and discussed the agenda at length. "
NEEDLE = ("Weathered biotite sorbed caesium two orders of magnitude more "
          "strongly than fresh biotite.")
PR_SOURCE = FILLER * 300 + NEEDLE + " " + FILLER * 100
PR_ROW = {"row_id": "test:long", "pile": "retreat_contested",
          "claim_text": "Weathered biotite sorbs caesium two orders of "
                        "magnitude better than fresh biotite.",
          "context": "…", "old_label": "contested",
          "sources": [{"name": "biotite.pdf", "chars": len(PR_SOURCE),
                       "garble_ratio": 0.0, "text": PR_SOURCE}]}
SPLIT_TEMPLATE = "SPLIT-CALL claim={CLAIM} context={CONTEXT}"
PIECE_TEMPLATE = ("PIECE-CALL claim={CLAIM} parts={PARTS} "
                  "piece {PIECE_NO} of {N_PIECES} of {SOURCE_NAME} >>> {PIECE}")
PIECE_CHARS, PIECE_OVERLAP = 5_000, 500
PARTS = ["weathered biotite sorbs caesium", "two orders of magnitude better"]


def _split_answer(parts=None):
    return json.dumps({"parts": parts if parts is not None else PARTS})


def _piece_answer(verdicts, quotes=None):
    """One piece's answer: verdicts per part, in part order."""
    quotes = quotes or [None] * len(verdicts)
    return json.dumps({"answers": [
        {"part_no": i, "verdict": v, "quote": q}
        for i, (v, q) in enumerate(zip(verdicts, quotes), 1)]})


class PieceStub:
    """A stub client that answers by what the prompt asks, not by call order —
    pieces are read most-relevant-first, so a queue would be brittle."""

    def __init__(self, split=None, on_needle=None, off_needle=None,
                 fail_piece_with_needle=False):
        self.split = split if split is not None else _split_answer()
        self.on_needle = on_needle if on_needle is not None else _piece_answer(
            ["proven", "proven"], [NEEDLE, NEEDLE])
        self.off_needle = off_needle if off_needle is not None else \
            _piece_answer(["not_stated_here", "not_stated_here"])
        self.fail_piece_with_needle = fail_piece_with_needle
        self.split_calls = 0
        self.piece_calls = 0

    def call(self, prompt, temperature=0.1, max_output_tokens=8000,
             purpose="untagged", claim_id=None):
        if prompt.startswith("SPLIT-CALL"):
            self.split_calls += 1
            return self.split
        self.piece_calls += 1
        if NEEDLE in prompt:
            return None if self.fail_piece_with_needle else self.on_needle
        return self.off_needle


class TestPieceReadMode(unittest.TestCase):
    """The reading built for task #105: every numbered piece answers the
    rubric's own question about one fixed list of claim parts."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self, stub, piece_read=True, sectioned=None):
        out = os.path.join(self.tmp.name, "v.jsonl")
        counts = run_panel(
            [PR_ROW], ["m1"], out, client_factory=lambda m: stub,
            template=TEMPLATE, progress=lambda *_: None,
            max_prompt_chars=1_000,
            sectioned=sectioned,
            piece_read=({"piece_chars": PIECE_CHARS, "overlap": PIECE_OVERLAP}
                        if piece_read else None),
            split_template=SPLIT_TEMPLATE, piece_template=PIECE_TEMPLATE)
        with open(out) as f:
            recs = [json.loads(l) for l in f if l.strip()]
        return counts, recs[0]

    def test_a_proof_deep_in_a_long_source_is_found_and_stamped(self):
        stub = PieceStub()
        counts, rec = self._run(stub)
        self.assertEqual(counts["judged"], 1)
        self.assertEqual(rec["strict_label"], "pass")
        self.assertEqual(rec["read_mode"], "piece_read")
        self.assertTrue(rec["silence_trustworthy"])
        self.assertEqual(rec["split_calls"], 1)
        self.assertEqual(rec["piece_read_calls"], rec["pieces"])
        self.assertGreater(rec["pieces"], 1)
        self.assertEqual(stub.split_calls, 1)
        self.assertEqual(stub.piece_calls, rec["pieces"])
        for p in rec["parts"]:
            self.assertTrue(p["quote_verified"])
            self.assertEqual(p["quote_verified_in"], "biotite.pdf")
            self.assertIsNotNone(p["found_in_piece"])

    def test_the_round_1_5_loss_class_is_the_difference_between_the_modes(self):
        """The measured failure this mode exists for: a scanning pass that does
        not copy out the proof sentence makes the judge call the source silent,
        while asking every piece directly finds the same proof."""
        class ScanStub:
            """The scanning pass copies nothing out — the measured loss — and
            the judge then sees an empty collection of sentences."""

            def call(self, prompt, temperature=0.1, max_output_tokens=8000,
                     purpose="untagged", claim_id=None):
                if prompt.startswith("SCAN "):
                    return json.dumps({"found": []})
                return verdict_json("fail_unproven", None)

        scan_stub = ScanStub()
        out = os.path.join(self.tmp.name, "scan.jsonl")
        run_panel([PR_ROW], ["m1"], out, client_factory=lambda m: scan_stub,
                  template=TEMPLATE, progress=lambda *_: None,
                  max_prompt_chars=1_000,
                  sectioned={"piece_chars": PIECE_CHARS,
                             "overlap": PIECE_OVERLAP},
                  scan_template="SCAN {CLAIM} {SOURCE_NAME} {PIECE_NO} "
                                "{N_PIECES} {PIECE}")
        with open(out) as f:
            scan_rec = json.loads(f.readline())
        self.assertEqual(scan_rec["read_mode"], "sectioned")
        self.assertEqual(scan_rec["strict_label"], "fail_unproven")

        _, piece_rec = self._run(PieceStub())
        self.assertEqual(piece_rec["read_mode"], "piece_read")
        self.assertEqual(piece_rec["strict_label"], "pass")

    def test_one_unread_piece_gives_no_verdict_at_all(self):
        counts, rec = self._run(PieceStub(fail_piece_with_needle=True))
        self.assertEqual(counts, {"judged": 0, "refused": 1, "skipped": 0})
        self.assertFalse(rec["answered"])
        self.assertNotIn("strict_label", rec)
        self.assertIn("never read", rec["reason"])
        self.assertIn("no verdict on part of a source", rec["reason"])

    def test_a_quote_not_in_the_piece_is_dropped_and_the_part_falls_back(self):
        stub = PieceStub(on_needle=_piece_answer(
            ["proven", "proven"],
            ["The paper proves this beyond any doubt.", NEEDLE]))
        _, rec = self._run(stub)
        self.assertEqual(rec["quotes_dropped"], 1)
        self.assertEqual(rec["strict_label"], "fail_unproven")
        by_part = {p["part"]: p for p in rec["parts"]}
        self.assertEqual(by_part[PARTS[0]]["classification"], "unproven")
        self.assertIsNone(by_part[PARTS[0]]["quote"])
        self.assertEqual(by_part[PARTS[1]]["classification"], "proven")

    def test_a_claim_that_cannot_be_split_gets_no_verdict(self):
        _, rec = self._run(PieceStub(split="not json at all"))
        self.assertFalse(rec["answered"])
        self.assertIn("could not be split", rec["reason"])
        self.assertEqual(rec["pieces"], 0)

    def test_a_piece_that_answers_only_some_parts_counts_as_unread(self):
        stub = PieceStub(off_needle=_piece_answer(["not_stated_here"]))
        _, rec = self._run(stub)
        self.assertFalse(rec["answered"])
        self.assertIn("were not answered", rec["reason"])

    def test_a_contradiction_in_any_piece_outranks_a_proof_in_another(self):
        stub = PieceStub(
            on_needle=_piece_answer(["proven", "proven"], [NEEDLE, NEEDLE]),
            off_needle=_piece_answer(
                ["contradicted", "not_stated_here"],
                [FILLER.strip(), None]))
        _, rec = self._run(stub)
        self.assertEqual(rec["strict_label"], "fail_contradicted")

    def test_the_mode_is_off_unless_asked_for(self):
        counts, rec = self._run(PieceStub(), piece_read=False)
        self.assertEqual(counts["refused"], 1)
        self.assertEqual(rec["read_mode"], "whole_source")
        self.assertIn("source too long", rec["reason"])

    def test_merge_precedence_and_silence_wording(self):
        from benchmarks.labeler.panel_runner import merge_piece_answers
        per_piece = [
            ("s.pdf", 1, {1: {"verdict": "not_stated_here", "quote": None},
                          2: {"verdict": "tolerated", "quote": "about half"}}),
            ("s.pdf", 2, {1: {"verdict": "proven", "quote": "exactly this"},
                          2: {"verdict": "proven", "quote": "half of them"}}),
            ("s.pdf", 3, {1: {"verdict": "contradicted", "quote": "the opposite"},
                          2: {"verdict": "not_stated_here", "quote": None}}),
        ]
        merged = merge_piece_answers(["part one", "part two"], per_piece)
        self.assertEqual(merged[0]["classification"], "contradicted")
        self.assertEqual(merged[0]["found_in_piece"], 3)
        self.assertEqual(merged[1]["classification"], "proven")
        self.assertEqual(merged[1]["found_in_piece"], 2)
        nothing = merge_piece_answers(
            ["part one"],
            [("s.pdf", 1, {1: {"verdict": "not_stated_here", "quote": None}})])
        self.assertEqual(nothing[0]["classification"], "unproven")
        self.assertIsNone(nothing[0]["found_in_piece"])

    def test_the_funnel_proposes_a_label_on_an_agreed_piece_read_silence(self):
        """The opposite of the sectioned rule: this reading covered the whole
        source, so its silence is not held back."""
        rows = [{"row_id": "test:pr", "pile": "retreat_contested",
                 "claim_text": "c", "old_label": "ACCURATE"}]
        verdicts = [
            {"row_id": "test:pr", "model": "m1", "answered": True,
             "strict_label": "fail_unproven", "read_mode": "piece_read",
             "silence_trustworthy": True,
             "parts": [{"part": "p", "classification": "unproven",
                        "quote": None}]},
            {"row_id": "test:pr", "model": "m2", "answered": True,
             "strict_label": "fail_unproven", "read_mode": "whole_source",
             "parts": [{"part": "p", "classification": "unproven",
                        "quote": None}]},
        ]
        out = sort_rows(rows, verdicts)[0]
        self.assertEqual(out["status"], "unanimous")
        self.assertEqual(out["proposed_label"], "fail_unproven")
        self.assertEqual(out["sectioned_unproven"], [])

    def test_a_piece_read_vote_that_did_not_cover_the_source_is_held_back(self):
        from benchmarks.labeler.funnel import silence_is_trustworthy
        rows = [{"row_id": "test:pr2", "pile": "retreat_contested",
                 "claim_text": "c", "old_label": "ACCURATE"}]
        weak = {"row_id": "test:pr2", "model": "m1", "answered": True,
                "strict_label": "fail_unproven", "read_mode": "piece_read",
                "silence_trustworthy": False,
                "parts": [{"part": "p", "classification": "unproven",
                           "quote": None}]}
        strong = dict(weak, model="m2", read_mode="whole_source")
        strong.pop("silence_trustworthy")
        self.assertFalse(silence_is_trustworthy(weak))
        self.assertTrue(silence_is_trustworthy(strong))
        out = sort_rows(rows, [weak, strong])[0]
        self.assertEqual(out["status"], "needs_whole_reading")
        self.assertIsNone(out["proposed_label"])


class TestDiagnosingAControlMiss(unittest.TestCase):
    """Card #105 follow-up, 2026-09-13. One mechanical check that needs no
    judgement: a part called 'the source never says this' whose own words sit
    inside a quote the same reading already checked word for word."""

    def test_a_part_inside_another_parts_verified_quote_is_reported(self):
        from benchmarks.labeler.card105_diagnose_control_misses import (
            self_contradicting_parts)
        parts = [
            {"part": "biotite sorbed more", "classification": "proven",
             "quote": "Biotite sorbed far more than illite, smectite, "
                      "kaolinite and halloysite.",
             "quote_verified": True},
            {"part": "kaolinite", "classification": "unproven", "quote": None,
             "quote_verified": False},
            {"part": "imogolite", "classification": "unproven", "quote": None,
             "quote_verified": False},
        ]
        found = self_contradicting_parts(parts)
        self.assertEqual([f["part"] for f in found], ["kaolinite"])
        self.assertEqual(found[0]["already_quoted_for"], "biotite sorbed more")

    def test_an_unverified_quote_never_rescues_a_part(self):
        """A quote the reading could not find in the source proves nothing,
        even if the part's words are inside it."""
        from benchmarks.labeler.card105_diagnose_control_misses import (
            self_contradicting_parts)
        parts = [
            {"part": "a", "classification": "proven",
             "quote": "mentions kaolinite", "quote_verified": False},
            {"part": "kaolinite", "classification": "unproven", "quote": None,
             "quote_verified": False},
        ]
        self.assertEqual(self_contradicting_parts(parts), [])

    def test_line_breaks_and_case_inside_a_quote_do_not_hide_a_match(self):
        from benchmarks.labeler.card105_diagnose_control_misses import (
            self_contradicting_parts)
        parts = [
            {"part": "a", "classification": "proven",
             "quote": "listed Kaolinite\n   and halloysite together",
             "quote_verified": True},
            {"part": "kaolinite and halloysite", "classification": "unproven",
             "quote": None, "quote_verified": False},
        ]
        found = self_contradicting_parts(parts)
        self.assertEqual(len(found), 1)

    def test_a_proven_part_is_never_reported(self):
        from benchmarks.labeler.card105_diagnose_control_misses import (
            self_contradicting_parts)
        parts = [
            {"part": "a", "classification": "proven", "quote": "about b",
             "quote_verified": True},
            {"part": "b", "classification": "proven", "quote": "about b",
             "quote_verified": True},
        ]
        self.assertEqual(self_contradicting_parts(parts), [])


class TestScoringAgainstTheAuthorsRulings(unittest.TestCase):
    """Card #105 follow-up, 2026-09-13. Two readings disagreeing says only that
    one of them is wrong. Where the author has ruled, each reading is marked
    right or wrong instead."""

    def _comparison(self, row_id, piece_label, whole_label):
        return {"row_id": row_id, "model": "m", "reference_model": "m",
                "new_answered": True, "reference_answered": True,
                "new_label": piece_label, "reference_label": whole_label,
                "pieces": 1, "quotes_dropped": 0,
                "agree": piece_label == whole_label}

    def test_only_rows_the_author_ruled_on_are_scored(self):
        """A row a panel merely agreed on is not a ruling and must not count."""
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "labels.jsonl")
            with open(path, "w") as f:
                f.write(json.dumps({"row_id": "a", "strict_label": "pass",
                                    "ruled_by": "the author",
                                    "date": "2026-09-09"}) + "\n")
                f.write(json.dumps({"row_id": "b", "strict_label": "pass",
                                    "ruled_by": "the panel",
                                    "date": "2026-09-09"}) + "\n")
                f.write(json.dumps({"row_id": "c", "strict_label": None,
                                    "ruled_by": "the author"}) + "\n")
            rulings = load_rulings(path)
        self.assertEqual(sorted(rulings), ["a"])
        self.assertEqual(rulings["a"]["label"], "pass")

    def test_missing_rulings_file_scores_nothing_rather_than_failing(self):
        self.assertEqual(load_rulings(""), {})
        self.assertEqual(load_rulings("/no/such/file.jsonl"), {})

    def test_each_reading_is_marked_right_or_wrong_per_ruled_row(self):
        rulings = {
            "r1": {"label": "pass", "date": "2026-09-09", "reason": ""},
            "r2": {"label": "fail_unproven", "date": "2026-09-08", "reason": ""},
            "r3": {"label": "fail_contradicted", "date": "2026-09-09",
                   "reason": ""},
        }
        rows = [
            # both right
            self._comparison("r1", "pass", "pass"),
            # the piece reading right, the whole reading wrong
            self._comparison("r2", "fail_unproven", "pass"),
            # the whole reading right, the piece reading wrong
            self._comparison("r3", "pass", "fail_contradicted"),
            # not ruled on: skipped entirely
            self._comparison("r4", "pass", "pass"),
        ]
        scored = score_against_rulings(rows, rulings)
        self.assertEqual([s["row_id"] for s in scored], ["r1", "r2", "r3"])
        self.assertEqual(sum(1 for s in scored if s["piece_matches"]), 2)
        self.assertEqual(sum(1 for s in scored if s["whole_matches"]), 2)
        self.assertTrue(scored[1]["piece_matches"])
        self.assertFalse(scored[1]["whole_matches"])

    def test_an_unanswered_reading_never_counts_as_matching_the_ruling(self):
        """A refused call must not be scored as a right answer, whatever label
        happens to sit in the record."""
        rulings = {"r1": {"label": "pass", "date": "d", "reason": ""}}
        row = self._comparison("r1", "pass", "pass")
        row["new_answered"] = False
        scored = score_against_rulings([row], rulings)
        self.assertFalse(scored[0]["piece_matches"])
        self.assertTrue(scored[0]["whole_matches"])

    def test_the_report_states_both_scores_and_names_the_rows_each_got_right(self):
        rulings = {
            "r2": {"label": "fail_unproven", "date": "2026-09-08", "reason": ""},
            "r3": {"label": "fail_contradicted", "date": "2026-09-09",
                   "reason": ""},
        }
        control = [self._comparison("r2", "fail_unproven", "pass"),
                   self._comparison("r3", "pass", "fail_contradicted")]
        piece_read = {("r2", "m"): {"row_id": "r2", "model": "m",
                                    "answered": True, "pieces": 2}}
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "report.md")
            summary = write_report(piece_read, control, [], out, rulings=rulings)
            text = open(out).read()
        self.assertEqual(summary["control_ruled"], 2)
        self.assertEqual(summary["control_piece_right"], 1)
        self.assertEqual(summary["control_whole_right"], 1)
        self.assertIn("1 of 2 rows match the author's ruling", text)
        self.assertIn("Rows the piece-by-piece reading got right and the whole "
                      "reading got wrong: r2", text)
        self.assertIn("Rows the whole reading got right and the piece-by-piece "
                      "reading got wrong: r3", text)

    def test_with_no_rulings_the_report_says_so_instead_of_claiming_a_score(self):
        piece_read = {("r1", "m"): {"row_id": "r1", "model": "m",
                                    "answered": True, "pieces": 1}}
        control = [self._comparison("r1", "pass", "pass")]
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "report.md")
            summary = write_report(piece_read, control, [], out, rulings={})
            text = open(out).read()
        self.assertEqual(summary["control_ruled"], 0)
        self.assertIn("No row compared above has a ruling from the author yet",
                      text)
        self.assertNotIn("match the author's ruling", text)


if __name__ == "__main__":
    unittest.main()
