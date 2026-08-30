#!/usr/bin/env python3
"""Guard the settlement-row triage (task #30, step 2).

The buckets themselves are a reading judgement and cannot be tested. What these
checks pin is that the judgement stays attached to the frozen list:

  * every frozen row is triaged exactly once, and no triage entry invents a row;
  * bucket, group, tier and converts values stay inside their allowed sets, and a
    group is present exactly when the bucket is A;
  * the three panel-framed groups are DERIVED from the frozen panel answer, never
    hand-written — a hand-written copy went stale the moment three new companies
    joined the panel on 2026-08-04, so writing one is an error;
  * the published split (20 / 1 / 8 and an 11-row reading list) cannot drift
    silently — a row moved between buckets has to move this number too;
  * every group has at least one first-tier row, or the reading list would leave
    a question unanswered;
  * the one row that must never be rewritten for Fable (`fresh50:cidev0043`,
    where the mistake IS a name confusion) stays marked `converts="no"`;
  * the arm-family measurement holds. It is what made the author's 2026-08-04
    objection concrete (the two DeepSeek snapshots agree 24/29 with each other,
    far more than either agrees with any other company) and it now also carries
    the answer: with one arm per company, the live run's arbiter drops the
    complaint on 23 of 29 rows while the other four companies drop it on 9-11,
    and the panel agrees with it least of all five.

Offline: no API, no network. The tests that need the frozen list skip when
`data/` is absent, since it is not in the repo.
"""

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "benchmarks"))

import ci_settlement_triage as T  # noqa: E402

HAVE_FROZEN = os.path.exists(T.FROZEN)


class TestTriageTableItself(unittest.TestCase):
    """These need no run directories — the table is in the module."""

    def test_every_entry_has_a_legal_bucket_and_converts_value(self):
        for row, t in T.TRIAGE.items():
            self.assertIn(t["bucket"], T.BUCKETS, row)
            self.assertIn(t["converts"], T.CONVERTS, row)

    def test_a_group_is_present_exactly_when_the_bucket_is_a(self):
        allowed = set(T.GROUPS) - set(T.PANEL_GROUPS) | {"panel"}
        for row, t in T.TRIAGE.items():
            if t["bucket"] == "A":
                self.assertIn(t["group"], allowed, row)
            else:
                self.assertIsNone(t["group"], row)

    def test_a_panel_group_is_never_written_by_hand(self):
        """The panel changed on 2026-08-04 and will change again; a hand-kept
        copy of these three groups would silently describe the old panel."""
        for row, t in T.TRIAGE.items():
            self.assertNotIn(t["group"], T.PANEL_GROUPS, row)

    def test_every_row_says_what_would_be_decided(self):
        for row, t in T.TRIAGE.items():
            self.assertTrue(t.get("question", "").strip(), row)

    def test_a_row_that_does_not_convert_says_why(self):
        for row, t in T.TRIAGE.items():
            if t["converts"] != "yes":
                self.assertTrue(t.get("converts_note", "").strip(), row)

    def test_the_name_confusion_row_is_never_marked_convertible(self):
        # fresh50:cidev0043's defect is SARS vs SARS-CoV-2. Renaming the subject
        # deletes the defect, so a twin of it would misrepresent the row.
        self.assertEqual(T.TRIAGE["fresh50:cidev0043"]["converts"], "no")

    def test_only_bucket_a_rows_are_on_the_reading_list(self):
        for row, t in T.TRIAGE.items():
            if t["tier"] in ("first", "second"):
                self.assertEqual(t["bucket"], "A", row)
            else:
                self.assertEqual(t["tier"], "table", row)

    def test_every_question_has_a_first_tier_example(self):
        """Checked on the hand-written groups here; the derived panel groups are
        checked against the frozen list below, where their membership is known."""
        firsts = {t["group"] for t in T.TRIAGE.values() if t["tier"] == "first"}
        self.assertEqual(firsts,
                         (set(T.GROUPS) - set(T.PANEL_GROUPS)) | {"panel"},
                         "a question with no row to read")


@unittest.skipUnless(HAVE_FROZEN, "the frozen settlement-row list is not on disk")
class TestJoinAgainstTheFrozenList(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.rows = T.triaged_rows()
        cls.summary = T.summary(cls.rows)

    def test_all_29_frozen_rows_are_triaged_once(self):
        self.assertEqual(len(self.rows), 29)
        self.assertEqual(len({r["row"] for r in self.rows}), 29)

    def test_the_published_split_holds(self):
        """20 / 1 / 8 since 2026-08-04. Two rows every earlier checker waved
        through became questions when the new companies kept the complaint on
        them, and one row stopped being a question when the panel agreed with
        its label."""
        self.assertEqual(self.summary["by_bucket"], {"A": 20, "B": 1, "C": 8})

    def test_the_reading_list_is_eleven_rows(self):
        self.assertEqual(self.summary["by_tier"]["first"], 11)
        self.assertEqual(self.summary["by_tier"]["second"], 9)
        self.assertEqual(self.summary["by_tier"]["table"], 9)

    def test_almost_everything_converts(self):
        self.assertEqual(self.summary["by_converts"], {"yes": 27, "care": 1, "no": 1})

    def test_the_five_questions_cover_every_bucket_a_row(self):
        self.assertEqual(sum(self.summary["by_group"].values()), 20)
        self.assertEqual(set(self.summary["by_group"]), set(T.GROUPS))

    def test_every_derived_group_has_a_row_to_read(self):
        for group in T.PANEL_GROUPS:
            rows = [r for r in self.rows if r["group"] == group]
            self.assertTrue(rows, group)
            self.assertTrue([r for r in rows if r["tier"] == "first"],
                            f"{group}: no first-tier row")

    def test_the_panel_group_matches_the_frozen_panel_answer(self):
        """The derived group and the frozen panel verdict cannot disagree."""
        for r in self.rows:
            if r["group"] not in T.PANEL_GROUPS:
                continue
            upholds = r["panel_scoring"] == "upholds"
            self.assertEqual(upholds, r["group"] == "panel-lets-error", r["row"])

    def test_a_missing_triage_entry_is_an_error_not_a_silent_gap(self):
        frozen = T.load_frozen()
        frozen["rows"] = frozen["rows"] + [dict(frozen["rows"][0], row="pilot100:cidev9999")]
        with self.assertRaises(ValueError):
            T.triaged_rows(frozen)

    def test_the_two_deepseek_snapshots_are_one_voice(self):
        # The author's 2026-08-04 objection, pinned: the same model at two
        # snapshots agrees with itself far more than with anyone else, which is
        # why 9-for-0 / 5-for-2 are withdrawn.
        ag = T.arm_agreement()
        self.assertEqual(ag["n"], 29)
        self.assertEqual(ag["pair_agreement"]["incumbent-or vs ds0731"], 24)
        for pair in ("incumbent-or vs luna", "incumbent-or vs qwen37",
                     "incumbent-or vs kimi26", "incumbent-or vs sonnet"):
            self.assertLess(ag["pair_agreement"][pair], 24, pair)

    def test_the_live_arbiter_is_the_lenient_one(self):
        """The 2026-08-04 answer: the arm the live run used drops the complaint on
        23 of 29 rows; the four other companies drop it on 9-11. So most of these
        settlements are one model's leniency, not a shared reading."""
        ag = T.arm_agreement()
        self.assertEqual(ag["rows_settled_by_arm"]["incumbent-or"], 23)
        for arm in ("luna", "qwen37", "kimi26", "sonnet"):
            self.assertLessEqual(ag["rows_settled_by_arm"][arm], 11, arm)
        # and the panel is furthest from the incumbent, not from the newcomers
        follows = ag["majority_follows_arm"]
        self.assertEqual(min(follows, key=follows.get), "ds0731")
        self.assertLess(follows["incumbent-or"], min(follows[a] for a in
                                                     ("luna", "qwen37", "kimi26",
                                                      "sonnet")))

    def test_the_page_says_the_old_figures_are_withdrawn(self):
        text = T.markdown(self.rows)
        self.assertIn("withdrawn", text.lower())
        self.assertIn("9-for-0", text)

    def test_the_markdown_names_every_row(self):
        text = T.markdown(self.rows)
        for r in self.rows:
            self.assertIn(r["row"], text)


if __name__ == "__main__":
    unittest.main()
