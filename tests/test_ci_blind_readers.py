"""Two blind readers over the Citation-Integrity rows (task #28).

Three things are worth pinning:

1. The packet builder still writes the 2026-07-30 rubric byte-for-byte. If it
   ever drifts, the votes collected by the first reader stop being comparable
   with the new ones, and no number in the disagreement list means anything.
2. The reader's prompt really does carry the paper's text (a one-turn headless
   call cannot open a file), and a malformed model answer is rejected rather
   than quietly recorded as a vote.
3. The three piles are assigned by the rule the author reads them by: readers
   together against the key, readers split, tool alone.

Offline: no API calls, no network.
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "benchmarks"))
import ci_blind_packet  # noqa: E402
import ci_blind_reader  # noqa: E402
import ci_disagreement_list as dl  # noqa: E402

FROZEN_PACKET = os.path.join(ROOT, "data", "citation_integrity", "c0_packet")
PILOT_BATCH = os.path.join(ROOT, "data", "citation_integrity", "batch_dev_pilot100")


class TestPacketRubricIsFrozen(unittest.TestCase):
    """The rubric a new reader gets must equal the one the first reader got."""

    @unittest.skipUnless(os.path.isdir(os.path.join(FROZEN_PACKET, "tasks")),
                         "the frozen 2026-07-30 packet is not in this checkout")
    def test_regenerating_the_pilot_packet_changes_no_task_file(self):
        tmp = tempfile.mkdtemp(prefix="blindpacket-")
        try:
            ci_blind_packet.build(PILOT_BATCH, tmp)
            frozen = sorted(os.listdir(os.path.join(FROZEN_PACKET, "tasks")))
            fresh = sorted(os.listdir(os.path.join(tmp, "tasks")))
            self.assertEqual(frozen, fresh)
            for name in frozen:
                a = open(os.path.join(FROZEN_PACKET, "tasks", name)).read()
                b = open(os.path.join(tmp, "tasks", name)).read()
                self.assertEqual(a, b, f"{name} would change")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_packet_holds_no_labels(self):
        row = {"citing_paragraph": "A sentence <|cit|> in a paragraph.",
               "claim_text": "A sentence [[cidev0001]]", "source_chars": 10,
               "label": "MISQUOTE", "strict_side": "flag"}
        body = ci_blind_packet.task_body("cidev0001", row)
        # the row's own label and every field that encodes it must be absent;
        # the words pass/flag appear only as the two answers the reader may give
        for leak in ("MISQUOTE", "strict_side", "grounding_side"):
            self.assertNotIn(leak, body)


class TestReaderPrompt(unittest.TestCase):
    def test_source_pointer_is_replaced_by_the_paper_itself(self):
        row = {"citing_paragraph": "Context <|cit|>.", "claim_text": "A claim [[cidev0007]]",
               "source_chars": 12}
        task = ci_blind_packet.task_body("cidev0007", row)
        prompt = ci_blind_reader.build_prompt(task, "PAPER BODY HERE", "cidev0007")
        self.assertNotIn("sources/cidev0007.txt", prompt)
        self.assertIn("PAPER BODY HERE", prompt)
        self.assertIn("<<<CITED PAPER cidev0007>>>", prompt)
        # the rubric survives untouched
        self.assertIn(ci_blind_packet.RUBRIC.strip(), prompt)

    def test_a_packet_it_does_not_recognise_is_an_error_not_a_silent_pass(self):
        with self.assertRaises(SystemExit):
            ci_blind_reader.build_prompt("# no pointer here\n", "text", "cidev0001")


class TestVoteNormalisation(unittest.TestCase):
    def test_good_vote(self):
        v, why = ci_blind_reader.normalise(
            {"id": "x", "vote": "FLAG", "defect": "Overstated", "quote": "q",
             "reason": "r", "confidence": "High"}, "cidev0001", "claude-code/opus")
        self.assertIsNone(why)
        self.assertEqual((v["vote"], v["defect"], v["confidence"]), ("flag", "overstated", "high"))
        self.assertEqual(v["id"], "cidev0001")          # the packet id wins, not the model's
        self.assertNotIn("_warnings", v)

    def test_a_vote_that_is_neither_pass_nor_flag_is_refused(self):
        v, why = ci_blind_reader.normalise({"vote": "maybe"}, "cidev0001", "m")
        self.assertIsNone(v)
        self.assertIn("maybe", why)

    def test_defect_on_a_pass_is_dropped_and_reported(self):
        v, _ = ci_blind_reader.normalise({"vote": "pass", "defect": "overstated"}, "c", "m")
        self.assertIsNone(v["defect"])
        self.assertTrue(v["_warnings"])

    def test_unknown_defect_code_is_kept_but_flagged(self):
        v, _ = ci_blind_reader.normalise({"vote": "flag", "defect": "vibes"}, "c", "m")
        self.assertEqual(v["defect"], "vibes")
        self.assertTrue(v["_warnings"])

    def test_non_object_output_is_refused(self):
        self.assertIsNone(ci_blind_reader.normalise(["flag"], "c", "m")[0])


def _rec(key, tool, sonnet, opus):
    return {"qid": "pilot100:cidev0001", "cid": "cidev0001", "tag": "pilot100",
            "label": "ACCURATE" if key == "pass" else "CONTRADICT",
            "key": key, "tool": tool, "claim": "c", "fair": True,
            "readers": {"sonnet": {"side": sonnet}, "opus": {"side": opus}}}


class TestPiles(unittest.TestCase):
    NAMES = ["sonnet", "opus"]

    def test_everything_agrees_is_not_listed(self):
        self.assertIsNone(dl.pile_of(_rec("pass", "pass", "pass", "pass"), self.NAMES))

    def test_both_readers_against_the_key_is_pile_a(self):
        self.assertEqual(dl.pile_of(_rec("flag", "flag", "pass", "pass"), self.NAMES), "A")
        # and it stays pile A when the tool happens to side with the readers
        self.assertEqual(dl.pile_of(_rec("flag", "pass", "pass", "pass"), self.NAMES), "A")

    def test_readers_split_is_pile_b_whatever_the_key_says(self):
        self.assertEqual(dl.pile_of(_rec("flag", "flag", "pass", "flag"), self.NAMES), "B")
        self.assertEqual(dl.pile_of(_rec("pass", "pass", "pass", "flag"), self.NAMES), "B")

    def test_tool_alone_is_pile_c(self):
        self.assertEqual(dl.pile_of(_rec("pass", "flag", "pass", "pass"), self.NAMES), "C")

    def test_a_missing_reading_is_pile_d_not_a_disagreement(self):
        self.assertEqual(
            dl.pile_of(_rec("pass", "pass", "pass", dl.MISSING), self.NAMES), "D")

    def test_claim_markers_are_spelled_out_for_the_reader(self):
        text = dl.wrap_claim({"cid": "cidev0001",
                              "claim": "A claim [[cidev0001]] and <|other_cit|>"})
        self.assertNotIn("[[", text)
        self.assertNotIn("<|", text)
        self.assertIn("the cited paper", text)

    def test_no_stray_brackets_survive_any_marker_shape(self):
        # every shape the converter actually produces, including the spans it
        # cut off inside the bracket group
        for claim, want in [
            ("Vpr does not degrade SAMHD1 [[[C]],",
             "Vpr does not degrade SAMHD1 [the cited paper, plus other references]"),
            ("most infectious [[[C]],3",
             "most infectious [the cited paper, plus other references]"),
            ("other reports[[C]], we found that Depression",
             "other reports[the cited paper], we found that Depression"),
            ("the finding [[C]].", "the finding [the cited paper]."),
            ("Smith et al. ([[C]]) documented",
             "Smith et al. ([the cited paper]) documented"),
            ("levels rose [[C]]]", "levels rose [the cited paper]"),
        ]:
            got = dl.wrap_claim({"cid": "C", "claim": claim})
            self.assertEqual(got, want)
            self.assertNotIn("[[", got)


class TestReaderLoading(unittest.TestCase):
    def test_raw_votes_dir_skips_underscore_files(self):
        tmp = tempfile.mkdtemp(prefix="votes-")
        try:
            json.dump({"id": "cidev0001", "vote": "pass"},
                      open(os.path.join(tmp, "cidev0001.json"), "w"))
            open(os.path.join(tmp, "_prompt_sample.txt"), "w").write("not a vote")
            json.dump({"nope": 1}, open(os.path.join(tmp, "_scratch.json"), "w"))
            votes = dl.load_reader({"votes_dir": tmp}, None)
            self.assertEqual(list(votes), ["cidev0001"])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
