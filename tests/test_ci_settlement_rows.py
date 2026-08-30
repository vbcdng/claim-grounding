"""The frozen arbiter-settlement row list (task #30, step 1).

What is worth pinning:

1. **The totals must reproduce the published table.** docs/BENCHMARK_RUN_2026-08-02.md
   §3.3 tells the author that letting the arbiter settle a complaint removes 12
   false alarms for 2 errors let through on pilot100 and 6 for 3 on fresh50, and
   every later decision about the arbiter panel is argued from those numbers. If
   the row list this task builds pages from disagreed with them, the pages would
   quietly be about a different set of rows.
2. **A row is sorted by its label band, not by a hand-kept list.** ACCURATE ->
   the settlement removed a false alarm; a major error -> it let an error
   through; a minor content error or an etiquette row -> it settles too but sits
   outside the published two counts, and must be emitted with `headline: false`
   rather than dropped.
3. **The panel simulation must swap only the arbiter.** Each replay arm's answer
   is folded into the same claim record, so the part of the bucket that does not
   depend on the arbiter cannot move; a majority of the VOTING arms settling is
   what upholds. Voting means one arm per company (the author's 2026-08-04
   objection): the second DeepSeek snapshot is a repeatability control and is
   pinned out of every majority here.
4. **The scoring settlement and the display amber clear are different
   mechanisms** and must be counted apart — §3.3 quotes the amber one ("blocked 4
   of the 12 removals"), and mixing them would make one number stand for both.

Offline: no API calls, no network. The batch-data test skips when the run
directories are absent, since `data/` is not in the repo.
"""
import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "benchmarks"))
import ci_settlement_rows as sr  # noqa: E402

PILOT = os.path.join(ROOT, "data/citation_integrity/batch_dev_pilot100")
PILOT_RUN = os.path.join(ROOT,
                         "data/citation_integrity/batch_dev_pilot100_run_gemma_0802")
HAVE_RUNS = os.path.exists(os.path.join(PILOT_RUN, "analysis.json"))


class TestSideClassification(unittest.TestCase):
    def test_accurate_row_is_a_removed_false_alarm(self):
        side, headline = sr._side("ACCURATE", "pass")
        self.assertEqual(side, "false alarm removed")
        self.assertTrue(headline)

    def test_major_error_is_counted_in_the_table(self):
        for label in ("CONTRADICT", "NOT_SUBSTANTIATE", "IRRELEVANT"):
            side, headline = sr._side(label, "pass")
            self.assertEqual(side, "major error let through", label)
            self.assertTrue(headline, label)

    def test_minor_and_etiquette_settle_but_stay_out_of_the_table(self):
        for label in ("MISQUOTE", "OVERSIMPLIFY"):
            side, headline = sr._side(label, "pass")
            self.assertIn("minor content error", side)
            self.assertFalse(headline, label)
        side, headline = sr._side("ETIQUETTE", "pass")
        self.assertFalse(headline)
        self.assertIn("outside the tally", side)

    def test_bands(self):
        self.assertEqual(sr.band("ACCURATE"), "accurate")
        self.assertEqual(sr.band("CONTRADICT"), "major")
        self.assertEqual(sr.band("MISQUOTE"), "minor-content")
        self.assertEqual(sr.band("INDIRECT"), "provenance")
        self.assertEqual(sr.band("ETIQUETTE"), "etiquette")


class TestArmSimulation(unittest.TestCase):
    """Only the arbiter payload may change when an arm is simulated."""

    UNSUPPORTED = {"id": "t1", "verdict": "unsupported",
                   "arbiter": {"action": "add_citation_or_rewrite",
                               "proofs": []}}

    def test_proof_backed_wrong_evidence_settles_an_unsupported_row(self):
        _, _, settles = sr._settles(
            self.UNSUPPORTED,
            {"action": "wrong_or_insufficient_evidence", "proofs": ["a quote"]})
        self.assertTrue(settles)

    def test_the_same_action_without_a_proof_does_not_settle(self):
        _, _, settles = sr._settles(
            self.UNSUPPORTED,
            {"action": "wrong_or_insufficient_evidence", "proofs": []})
        self.assertFalse(settles)

    def test_the_original_claim_is_not_mutated(self):
        before = json.dumps(self.UNSUPPORTED, sort_keys=True)
        sr._settles(self.UNSUPPORTED, {"action": "supported", "proofs": ["q"]})
        self.assertEqual(json.dumps(self.UNSUPPORTED, sort_keys=True), before)

    def test_a_gap_row_settles_when_the_arbiter_calls_the_gaps_minor(self):
        gapped = {"id": "t2", "verdict": "supported",
                  "covering": {"uncovered": ["a component"]},
                  "arbiter": {"action": "add_citation_or_rewrite",
                              "proofs": ["q"]}}
        bucket, _, settles = sr._settles(gapped, {"action": "supported",
                                                  "proofs": ["q"]})
        self.assertEqual(bucket, "pass")
        self.assertTrue(settles)
        # and the arbiter that named a missing component does not settle it
        _, _, still = sr._settles(gapped, {"action": "add_citation_or_rewrite",
                                           "proofs": ["q"]})
        self.assertFalse(still)


class TestTotals(unittest.TestCase):
    def test_counts_split_by_side(self):
        rows = [{"batch": "b", "side": "false alarm removed", "headline": True},
                {"batch": "b", "side": "false alarm removed", "headline": True},
                {"batch": "b", "side": "major error let through",
                 "headline": True},
                {"batch": "b", "side": "minor content error let through",
                 "headline": False}]
        t = sr.totals(rows)["b"]
        self.assertEqual(t["false_alarm_removed"], 2)
        self.assertEqual(t["major_error_let_through"], 1)
        self.assertEqual(t["other_settlements"], 1)
        self.assertEqual(t["headline"], 3)
        self.assertEqual(t["all"], 4)


class TestSyntheticBatch(unittest.TestCase):
    """One made-up batch end to end, so the reader does not need the real data."""

    def _batch(self, tmp):
        bdir = os.path.join(tmp, "batch_dev_toy")
        rdir = os.path.join(tmp, "batch_dev_toy_run")
        pdir = os.path.join(tmp, "replay_toy")
        for arm in sr.ARMS:
            os.makedirs(os.path.join(pdir, "replays", arm))
        os.makedirs(bdir)
        os.makedirs(rdir)
        gt = {"claims": {
            "cidev0001": {"label": "ACCURATE", "ci_id": "dev/toy/A_1"},
            "cidev0002": {"label": "CONTRADICT", "ci_id": "dev/toy/A_2"},
            "cidev0003": {"label": "ACCURATE", "ci_id": "dev/toy/A_3"},
        }}
        with open(os.path.join(bdir, "ci_ground_truth.json"), "w") as f:
            json.dump(gt, f)
        claims = [
            # settles: unsupported + proof-backed arbiter
            {"id": "t1", "markers": ["cidev0001"], "verdict": "unsupported",
             "method": "llm_fulltext", "reason": "no proof found",
             "arbiter": {"model": "live", "action": "wrong_or_insufficient_evidence",
                         "proofs": ["quote one"]}},
            # settles, and it is a wrong settlement (CONTRADICT row)
            {"id": "t2", "markers": ["cidev0002"], "verdict": "unsupported",
             "method": "llm_fulltext", "reason": "no proof found",
             "arbiter": {"model": "live", "action": "supported", "proofs": []}},
            # does not settle: the arbiter concurs with the flag
            {"id": "t3", "markers": ["cidev0003"], "verdict": "unsupported",
             "method": "llm_fulltext", "reason": "no proof found",
             "arbiter": {"model": "live", "action": "add_citation_or_rewrite",
                         "proofs": []}},
        ]
        with open(os.path.join(rdir, "analysis.json"), "w") as f:
            json.dump({"text_claims": claims}, f)
        # Three of the five VOTING arms settle t1, two do not, and the
        # non-voting control settles it — so a majority of companies upholds
        # while the control's agreement with its own company is visible.
        # t2 is outside the replay sample.
        per_arm = {"incumbent-or": "wrong_or_insufficient_evidence",
                   "ds0731": "wrong_or_insufficient_evidence",
                   "luna": "add_citation_or_rewrite",
                   "qwen37": "wrong_or_insufficient_evidence",
                   "kimi26": "wrong_or_insufficient_evidence",
                   "sonnet": "add_citation_or_rewrite"}
        for arm, action in per_arm.items():
            path = os.path.join(pdir, "replays", arm, "results.jsonl")
            with open(path, "w") as f:
                f.write(json.dumps({
                    "claim_id": "t1", "action_match": action == "supported",
                    "amber": {"eligible": True,
                              "would_resolve": arm not in ("luna", "sonnet")},
                    "rescue": {"proposed": False},
                    "new_payload": {"model": arm, "action": action,
                                    "proofs": ["quote one"]}}) + "\n")
        return bdir, rdir, pdir

    def test_end_to_end(self):
        with tempfile.TemporaryDirectory() as tmp:
            bdir, rdir, pdir = self._batch(tmp)
            data = sr.build(batches=((bdir, rdir, pdir),), root="")
            self.assertEqual(data["n_rows"], 2)          # t3 did not settle
            self.assertEqual(data["n_headline"], 2)
            t = data["totals"]["toy"]
            self.assertEqual(t["false_alarm_removed"], 1)
            self.assertEqual(t["major_error_let_through"], 1)
            by_row = {r["cidev"]: r for r in data["rows"]}
            good = by_row["cidev0001"]
            self.assertEqual(good["panel_scoring"]["verdict"], "upholds")
            self.assertEqual(good["panel_scoring"]["arms_settling"], 3)
            self.assertEqual(good["panel_scoring"]["arms_replayed"],
                             len(sr.VOTERS))
            self.assertEqual(good["panel_scoring"]["companies_settling"],
                             ["DeepSeek", "Alibaba", "Moonshot"])
            self.assertEqual(good["panel_scoring"]["companies_blocking"],
                             ["OpenAI", "Anthropic"])
            # the control settled it too and is reported outside the majority
            self.assertEqual(good["repeatability_control"]["ds0731"],
                             {"settles": True,
                              "agrees_with_same_company_voter": True})
            self.assertEqual(good["panel_amber"]["arms_would_resolve"], 3)
            self.assertEqual(good["panel_amber"]["arms_eligible"],
                             len(sr.VOTERS))
            self.assertEqual(good["live_arbiter"]["n_proofs"], 1)
            bad = by_row["cidev0002"]
            self.assertEqual(bad["panel_scoring"]["verdict"], "not replayed")
            self.assertEqual(bad["side"], "major error let through")
            # the two panels are reported separately, never merged
            self.assertIn("panel_amber", bad)
            self.assertEqual(bad["panel_amber"]["arms_eligible"], 0)
            # and the table renders without the real data
            md = sr.markdown(data)
            self.assertIn("toy:cidev0002", md)


@unittest.skipUnless(HAVE_RUNS, "the 2026-08-02 run directories are not on disk "
                                "(data/ is not in the repo)")
class TestPublishedTotals(unittest.TestCase):
    """The one test that ties this list to the document the author reads."""

    @classmethod
    def setUpClass(cls):
        cls.data = sr.build()

    def test_every_batch_reproduces_the_published_exchange_rate(self):
        for tag, published in sr.PUBLISHED.items():
            got = self.data["totals"][tag]
            self.assertEqual(got["false_alarm_removed"],
                             published["false_alarm_removed"], tag)
            self.assertEqual(got["major_error_let_through"],
                             published["major_error_let_through"], tag)

    def test_the_reading_list_is_23_rows(self):
        self.assertEqual(self.data["n_headline"], 23)

    def test_the_settlements_outside_the_table_are_kept_not_dropped(self):
        extra = [r for r in self.data["rows"] if not r["headline"]]
        self.assertEqual(len(extra), 6)
        self.assertEqual({r["band"] for r in extra},
                         {"minor-content", "etiquette"})

    def test_every_settlement_lifts_a_flag(self):
        for r in self.data["rows"]:
            self.assertEqual((r["settled_from"], r["settled_to"]),
                             ("flag", "pass"), r["row"])

    def test_the_amber_panel_survives_a_majority_of_companies(self):
        """The display decision, one arm per company (2026-08-04): 7 of
        pilot100's 12 live amber clears and 3 of fresh50's 7 survive a majority
        of the five voting companies. The same-family figures this replaced were
        8 of 12 and 6 of 7 — the panel is stricter once the second DeepSeek
        snapshot stops voting."""
        want = {"pilot100": (12, 7), "fresh50": (7, 3)}
        for tag, (eligible, upheld) in want.items():
            rows = [r for r in self.data["rows"]
                    if r["batch"] == tag and r["panel_amber"]["arms_eligible"]]
            self.assertEqual(len(rows), eligible, tag)
            self.assertEqual(sum(1 for r in rows
                                 if r["panel_amber"]["verdict"] == "upholds"),
                             upheld, tag)

    def test_the_panel_effect_is_recorded_per_batch(self):
        """The number task #23 is decided on, now with one arm per company
        (2026-08-04): needing a majority of the five companies keeps 7 of
        pilot100's 12 correct removals and 4 of fresh50's 6, and stops ALL FIVE
        errors that the single arbiter let through. The withdrawn same-family
        figures were 9-for-0 and 5-for-2."""
        eff = self.data["panel_effect"]
        self.assertEqual((eff["pilot100"]["false_alarm_removed"],
                          eff["pilot100"]["major_error_let_through"]), (7, 0))
        self.assertEqual((eff["fresh50"]["false_alarm_removed"],
                          eff["fresh50"]["major_error_let_through"]), (4, 0))
        for tag in ("pilot100", "fresh50"):
            e = eff[tag]
            self.assertEqual(e["no_panel_data"], 0, tag)
            kept = e["false_alarm_removed"] + e["major_error_let_through"]
            blocked = e["blocked_correct"] + e["blocked_wrong"]
            published = sr.PUBLISHED[tag]
            self.assertEqual(kept + blocked,
                             published["false_alarm_removed"]
                             + published["major_error_let_through"], tag)

    def test_the_panel_effect_splits_by_whether_the_question_was_fair(self):
        """The number that PARKED task #23 (2026-08-06). Of the five false
        supports a majority-of-companies rule prevents, four sit on rows the
        converter broke by deleting the paper's other citations; all seven
        warnings the rule puts back onto good citations sit on rows that cite a
        single article. On fair rows alone the rule prevents ONE false support,
        so it cannot be adopted or rejected until task #32 lands."""
        f = self.data["panel_effect_by_fairness"]
        self.assertEqual((f["fair"]["kept_and_right"],
                          f["fair"]["kept_and_wrong"],
                          f["fair"]["blocked_and_right"],
                          f["fair"]["blocked_and_wrong"]), (9, 0, 7, 1))
        self.assertEqual((f["unfair"]["kept_and_right"],
                          f["unfair"]["kept_and_wrong"],
                          f["unfair"]["blocked_and_right"],
                          f["unfair"]["blocked_and_wrong"]), (2, 0, 0, 4))
        # the two views of the same rows must add up
        eff = self.data["panel_effect"]
        by_fair = sum(v[k] for v in f.values()
                      for k in ("kept_and_right", "kept_and_wrong",
                                "blocked_and_right", "blocked_and_wrong"))
        self.assertEqual(by_fair,
                         sum(e[k] for e in eff.values()
                             for k in ("false_alarm_removed",
                                       "major_error_let_through",
                                       "blocked_correct", "blocked_wrong")))

    def test_the_one_row_still_worth_reading_asks_a_fair_question(self):
        """`pilot100:cidev0017` is the whole remaining case for the majority
        rule: the single fair row where it prevents a false support. If it ever
        stops being fair, or stops being blocked, the reading page built for it
        (`ci_settlement_row_page.py`) is arguing for nothing."""
        row = next(r for r in self.data["rows"]
                   if r["row"] == "pilot100:cidev0017")
        self.assertTrue(row["fair_question"])
        self.assertNotEqual(row["side"], "false alarm removed")
        self.assertEqual(row["panel_scoring"]["verdict"], "blocks")
        self.assertEqual(
            self.data["panel_effect_by_fairness"]["fair"]["blocked_and_wrong"],
            1)

    def test_every_row_records_whether_its_question_was_fair(self):
        for r in self.data["rows"]:
            self.assertIsInstance(r["fair_question"], bool, r["row"])

    def test_no_settlement_row_had_its_verdict_rewritten_by_a_rescue(self):
        """A rescue rewrites the verdict, which would make the arm simulation
        compare against a claim the live arbiter had already changed."""
        for r in self.data["rows"]:
            self.assertNotEqual(r["judge"]["method"], "arbiter_rescue", r["row"])

    def test_verdict_changes_are_listed_separately(self):
        rows = {r["row"] for r in self.data["rescue_candidates"]}
        self.assertEqual(rows, {"pilot100:cidev0002", "pilot100:cidev0019",
                                "fresh50:cidev0011"})
        live = [r for r in self.data["rescue_candidates"] if r["live_flip"]]
        self.assertEqual([r["row"] for r in live], ["pilot100:cidev0019"])
        # Not one verdict change is backed by more than a single company —
        # the reason task #23 exists.
        for r in self.data["rescue_candidates"]:
            self.assertEqual(r["panel_scoring"], "blocks", r["row"])
            self.assertEqual(len(r["companies_flipping"]), 1, r["row"])

    def test_the_second_deepseek_snapshot_is_a_control_not_a_voter(self):
        """The author's 2026-08-04 objection, pinned: ds0731 must never be in a
        majority, and its agreement with the arm it duplicates is reported."""
        self.assertNotIn("ds0731", sr.VOTERS)
        self.assertIn("ds0731", sr.CONTROLS)
        companies = [sr.ARMS[a]["company"] for a in sr.VOTERS]
        self.assertEqual(len(companies), len(set(companies)),
                         "two voting arms from one company is not a panel")
        for r in self.data["rows"]:
            self.assertEqual(r["panel_scoring"]["arms_replayed"],
                             len(sr.VOTERS), r["row"])
            self.assertIn("ds0731", r["repeatability_control"])


if __name__ == "__main__":
    unittest.main()
